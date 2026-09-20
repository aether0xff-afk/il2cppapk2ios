from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

from .elf import Elf64, EM_AARCH64, ET_DYN, SHT_RELA, SHN_UNDEF

PT_LOAD = 1

R_AARCH64_ABS64 = 257
R_AARCH64_GLOB_DAT = 1025
R_AARCH64_JUMP_SLOT = 1026
R_AARCH64_RELATIVE = 1027

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64_ALL = 0
MH_DYLIB = 6

LC_SEGMENT_64 = 0x19
LC_ID_DYLIB = 0x0D
LC_BUILD_VERSION = 0x32
PLATFORM_IOS = 2

VM_PROT_READ = 1
VM_PROT_WRITE = 2
VM_PROT_EXECUTE = 4

IOS_PAGE = 0x4000
HEADER_VM = 0x100000000
ELF_SLIDE = 0x100010000


class TranslateError(ValueError):
    pass


@dataclass
class MappedLoad:
    name: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int
    maxprot: int
    initprot: int
    delta: int
    elf_vaddr: int
    elf_offset: int
    elf_filesz: int
    elf_memsz: int


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _floor(value: int, alignment: int) -> int:
    return value & ~(alignment - 1)


def _mach_prot(elf_flags: int) -> int:
    # ELF PF_X=1, PF_W=2, PF_R=4. Mach uses R=1, W=2, X=4.
    value = 0
    if elf_flags & 4:
        value |= VM_PROT_READ
    if elf_flags & 2:
        value |= VM_PROT_WRITE
    if elf_flags & 1:
        value |= VM_PROT_EXECUTE
    return value


def _segment_command(seg: MappedLoad) -> bytes:
    name = seg.name.encode("ascii")[:16].ljust(16, b"\0")
    return struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        72,
        name,
        seg.vmaddr,
        seg.vmsize,
        seg.fileoff,
        seg.filesize,
        seg.maxprot,
        seg.initprot,
        0,
        0,
    )


def _id_dylib_command(name: str) -> bytes:
    raw = name.encode("utf-8") + b"\0"
    size = _align(24 + len(raw), 8)
    return (
        struct.pack("<IIIIII", LC_ID_DYLIB, size, 24, 0, 0x10000, 0x10000)
        + raw
        + b"\0" * (size - 24 - len(raw))
    )


def _build_version_command(min_ios: tuple[int, int, int]) -> bytes:
    encoded = (min_ios[0] << 16) | (min_ios[1] << 8) | min_ios[2]
    return struct.pack(
        "<IIIIII",
        LC_BUILD_VERSION,
        24,
        PLATFORM_IOS,
        encoded,
        encoded,
        0,
    )


class Phase2Translator:
    """Static/AOT experiment for an AArch64 Android Unity IL2CPP ELF.

    This keeps the original machine code at one constant virtual-address slide,
    copies PT_LOAD contents into Mach-O segments, and resolves relocations whose
    targets are fully known inside the ELF.

    External imports intentionally remain unresolved for the next phase.
    """

    def __init__(
        self,
        elf: Elf64,
        *,
        header_vm: int = HEADER_VM,
        slide: int = ELF_SLIDE,
        page_size: int = IOS_PAGE,
    ):
        if elf.e_machine != EM_AARCH64:
            raise TranslateError("Phase 2 supports AArch64 only")
        if elf.e_type != ET_DYN:
            raise TranslateError("Phase 2 expects an ET_DYN shared object")
        self.elf = elf
        self.header_vm = header_vm
        self.slide = slide
        self.page_size = page_size
        self.loads = self._map_loads()

    def _map_loads(self) -> list[MappedLoad]:
        cursor = self.page_size
        result: list[MappedLoad] = []
        load_index = 0

        for ph in self.elf.program_headers:
            if ph.p_type != PT_LOAD:
                continue

            new_vm = self.slide + ph.vaddr
            vm_floor = _floor(new_vm, self.page_size)
            delta = new_vm - vm_floor
            fileoff = _align(cursor, self.page_size)

            prot = _mach_prot(ph.flags)
            if ph.flags & 1:
                name = "__ELF_TEXT"
            elif ph.flags & 2:
                name = "__ELF_DATA"
            else:
                name = f"__ELF_{load_index}"

            result.append(
                MappedLoad(
                    name=name,
                    vmaddr=vm_floor,
                    vmsize=_align(delta + ph.memsz, self.page_size),
                    fileoff=fileoff,
                    filesize=delta + ph.filesz,
                    maxprot=prot,
                    initprot=prot,
                    delta=delta,
                    elf_vaddr=ph.vaddr,
                    elf_offset=ph.offset,
                    elf_filesz=ph.filesz,
                    elf_memsz=ph.memsz,
                )
            )
            cursor = _align(fileoff + delta + ph.filesz, self.page_size)
            load_index += 1

        if not result:
            raise TranslateError("ELF has no PT_LOAD segments")
        return result

    def _vaddr_to_output_offset(self, vaddr: int) -> int:
        for seg in self.loads:
            if seg.elf_vaddr <= vaddr < seg.elf_vaddr + seg.elf_filesz:
                return (
                    seg.fileoff
                    + seg.delta
                    + (vaddr - seg.elf_vaddr)
                )
        raise TranslateError(
            f"relocation target {vaddr:#x} is not inside a file-backed PT_LOAD"
        )

    def _iter_relocations(self):
        for sec in self.elf.sections:
            if sec.sh_type != SHT_RELA:
                continue
            entsize = sec.entsize or 24
            for off in range(sec.offset, sec.offset + sec.size, entsize):
                if off + 24 > len(self.elf.data):
                    break
                r_offset, r_info, r_addend = struct.unpack_from(
                    "<QQq", self.elf.data, off
                )
                yield {
                    "offset": r_offset,
                    "type": r_info & 0xFFFFFFFF,
                    "symbol_index": r_info >> 32,
                    "addend": r_addend,
                }

    def translate(
        self,
        output: str | Path,
        *,
        install_name: str = "@rpath/libil2cpp_ported.dylib",
        min_ios: tuple[int, int, int] = (12, 0, 0),
    ) -> dict:
        output = Path(output)
        end = max(_align(x.fileoff + x.filesize, self.page_size) for x in self.loads)
        image = bytearray(end)

        # Copy each PT_LOAD into an independent page-aligned Mach-O file region,
        # while preserving one constant virtual slide for every original address.
        for seg in self.loads:
            dst = seg.fileoff + seg.delta
            src = seg.elf_offset
            image[dst:dst + seg.elf_filesz] = self.elf.data[
                src:src + seg.elf_filesz
            ]

        symbols = self.elf.dynamic_symbols()
        patched = {
            "relative": 0,
            "internal_abs64": 0,
            "internal_glob_dat": 0,
            "internal_jump_slot": 0,
        }
        unresolved: list[dict] = []

        for rel in self._iter_relocations():
            r_type = rel["type"]
            dst = self._vaddr_to_output_offset(rel["offset"])

            if r_type == R_AARCH64_RELATIVE:
                value = self.slide + rel["addend"]
                struct.pack_into("<Q", image, dst, value & 0xFFFFFFFFFFFFFFFF)
                patched["relative"] += 1
                continue

            if r_type not in (
                R_AARCH64_ABS64,
                R_AARCH64_GLOB_DAT,
                R_AARCH64_JUMP_SLOT,
            ):
                raise TranslateError(
                    f"unsupported relocation {r_type} at {rel['offset']:#x}"
                )

            index = rel["symbol_index"]
            if index >= len(symbols):
                raise TranslateError(f"bad dynamic symbol index {index}")
            symbol = symbols[index]

            if symbol["shndx"] == SHN_UNDEF:
                unresolved.append(
                    {
                        "offset": rel["offset"],
                        "type": r_type,
                        "symbol": symbol["name"],
                        "addend": rel["addend"],
                    }
                )
                continue

            value = self.slide + symbol["value"]
            if r_type == R_AARCH64_ABS64:
                value += rel["addend"]
                patched["internal_abs64"] += 1
            elif r_type == R_AARCH64_GLOB_DAT:
                patched["internal_glob_dat"] += 1
            else:
                patched["internal_jump_slot"] += 1

            struct.pack_into("<Q", image, dst, value & 0xFFFFFFFFFFFFFFFF)

        # Mach-O header page.
        header_seg = MappedLoad(
            name="__HEADER",
            vmaddr=self.header_vm,
            vmsize=self.slide - self.header_vm,
            fileoff=0,
            filesize=self.page_size,
            maxprot=VM_PROT_READ,
            initprot=VM_PROT_READ,
            delta=0,
            elf_vaddr=0,
            elf_offset=0,
            elf_filesz=0,
            elf_memsz=0,
        )
        commands = [_segment_command(header_seg)]
        commands.extend(_segment_command(seg) for seg in self.loads)
        commands.append(_id_dylib_command(install_name))
        commands.append(_build_version_command(min_ios))
        load_commands = b"".join(commands)

        header = struct.pack(
            "<IiiIIIII",
            MH_MAGIC_64,
            CPU_TYPE_ARM64,
            CPU_SUBTYPE_ARM64_ALL,
            MH_DYLIB,
            len(commands),
            len(load_commands),
            0,
            0,
        )
        if len(header) + len(load_commands) > self.page_size:
            raise TranslateError("Mach-O load commands exceed reserved header page")

        image[:len(header)] = header
        image[len(header):len(header) + len(load_commands)] = load_commands

        output.write_bytes(image)

        unresolved_symbols = sorted({
            item["symbol"] for item in unresolved if item["symbol"]
        })
        return {
            "output": str(output),
            "output_size": len(image),
            "header_vm": hex(self.header_vm),
            "elf_slide": hex(self.slide),
            "segments": [
                {
                    "name": x.name,
                    "vmaddr": hex(x.vmaddr),
                    "vmsize": hex(x.vmsize),
                    "fileoff": hex(x.fileoff),
                    "filesize": hex(x.filesize),
                    "protection": x.initprot,
                    "elf_vaddr": hex(x.elf_vaddr),
                }
                for x in self.loads
            ],
            "patched": patched,
            "patched_total": sum(patched.values()),
            "unresolved_relocations": len(unresolved),
            "unresolved_symbol_count": len(unresolved_symbols),
            "unresolved_symbols": unresolved_symbols,
            "note": (
                "Phase 2 output is not runnable yet: external imports, Darwin ABI "
                "shims, dyld import metadata, code signing, and Unity runtime "
                "integration are intentionally not implemented."
            ),
        }


def translate_phase2(
    input_elf: str | Path,
    output: str | Path,
    **kwargs,
) -> dict:
    return Phase2Translator(Elf64.from_path(input_elf)).translate(output, **kwargs)
