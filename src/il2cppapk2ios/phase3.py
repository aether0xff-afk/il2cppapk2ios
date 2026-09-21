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
MH_DYLDLINK = 0x4
MH_TWOLEVEL = 0x80

LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_BUILD_VERSION = 0x32
LC_DYLD_INFO_ONLY = 0x80000022
PLATFORM_IOS = 2

VM_PROT_READ = 1
VM_PROT_WRITE = 2
VM_PROT_EXECUTE = 4

REBASE_TYPE_POINTER = 1
REBASE_OPCODE_DONE = 0x00
REBASE_OPCODE_SET_TYPE_IMM = 0x10
REBASE_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB = 0x20
REBASE_OPCODE_DO_REBASE_IMM_TIMES = 0x50

BIND_TYPE_POINTER = 1
BIND_OPCODE_DONE = 0x00
BIND_OPCODE_SET_DYLIB_ORDINAL_IMM = 0x10
BIND_OPCODE_SET_SYMBOL_TRAILING_FLAGS_IMM = 0x40
BIND_OPCODE_SET_TYPE_IMM = 0x50
BIND_OPCODE_SET_ADDEND_SLEB = 0x60
BIND_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB = 0x70
BIND_OPCODE_DO_BIND = 0x90

IOS_PAGE = 0x4000
HEADER_RESERVE = 0x10000
IMAGE_BASE = 0
ELF_SLIDE = IMAGE_BASE + HEADER_RESERVE


class Phase3Error(ValueError):
    pass


@dataclass
class ImageSegment:
    name: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int
    protection: int
    elf_vaddr: int
    elf_offset: int
    elf_filesz: int
    elf_memsz: int
    data_addr: int
    data_fileoff: int
    data_size: int
    section_name: str


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _floor(value: int, alignment: int) -> int:
    return value & ~(alignment - 1)


def _mach_prot(elf_flags: int) -> int:
    result = 0
    if elf_flags & 4:
        result |= VM_PROT_READ
    if elf_flags & 2:
        result |= VM_PROT_WRITE
    if elf_flags & 1:
        result |= VM_PROT_EXECUTE
    return result


def _uleb128(value: int) -> bytes:
    if value < 0:
        raise Phase3Error("ULEB128 cannot encode a negative value")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        out.append(byte)
        if not value:
            return bytes(out)


def _sleb128(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        next_value = value >> 7
        done = (
            (next_value == 0 and not (byte & 0x40))
            or (next_value == -1 and (byte & 0x40))
        )
        if not done:
            byte |= 0x80
        out.append(byte)
        if done:
            return bytes(out)
        value = next_value


def _dylib_command(command: int, name: str) -> bytes:
    raw = name.encode("utf-8") + b"\0"
    size = _align(24 + len(raw), 8)
    return (
        struct.pack("<IIIIII", command, size, 24, 0, 0x10000, 0x10000)
        + raw
        + b"\0" * (size - 24 - len(raw))
    )


def _segment_no_sections(
    name: str,
    vmaddr: int,
    vmsize: int,
    fileoff: int,
    filesize: int,
    protection: int,
) -> bytes:
    return struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        72,
        name.encode("ascii")[:16].ljust(16, b"\0"),
        vmaddr,
        vmsize,
        fileoff,
        filesize,
        protection,
        protection,
        0,
        0,
    )


def _segment_with_data_section(seg: ImageSegment) -> bytes:
    seg_name = seg.name.encode("ascii")[:16].ljust(16, b"\0")
    section_name = seg.section_name.encode("ascii")[:16].ljust(16, b"\0")

    # The zero-sized sentinel makes the first section address equal the real
    # segment vmaddr. This keeps section-derived Mach-O tooling consistent with
    # dyld's segment-relative rebase/bind offsets, while the actual copied ELF
    # data can begin after the reserved/padding bytes.
    sentinel_name = b"__segstart".ljust(16, b"\0")

    command = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        72 + 80 * 2,
        seg_name,
        seg.vmaddr,
        seg.vmsize,
        seg.fileoff,
        seg.filesize,
        seg.protection,
        seg.protection,
        2,
        0,
    )
    sentinel = struct.pack(
        "<16s16sQQIIIIIIII",
        sentinel_name,
        seg_name,
        seg.vmaddr,
        0,
        seg.fileoff,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    data_section = struct.pack(
        "<16s16sQQIIIIIIII",
        section_name,
        seg_name,
        seg.data_addr,
        seg.data_size,
        seg.data_fileoff,
        3,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    return command + sentinel + data_section


def _build_version_command(min_ios: tuple[int, int, int]) -> bytes:
    version = (min_ios[0] << 16) | (min_ios[1] << 8) | min_ios[2]
    return struct.pack(
        "<IIIIII",
        LC_BUILD_VERSION,
        24,
        PLATFORM_IOS,
        version,
        version,
        0,
    )


class Phase3Translator:
    """Translate static ELF fixups into classic Mach-O dyld rebase/bind info.

    The output is still a research artifact, not a complete iOS port. All
    undefined Android ELF symbols are deliberately rebound to a future
    libbionic_shim.dylib using names of the form _a2i_<original>.
    """

    def __init__(
        self,
        elf: Elf64,
        *,
        image_base: int = IMAGE_BASE,
        header_reserve: int = HEADER_RESERVE,
        page_size: int = IOS_PAGE,
    ):
        if elf.e_machine != EM_AARCH64:
            raise Phase3Error("Phase 3 supports AArch64 only")
        if elf.e_type != ET_DYN:
            raise Phase3Error("Phase 3 expects an ET_DYN shared object")

        self.elf = elf
        self.image_base = image_base
        self.header_reserve = header_reserve
        self.page_size = page_size
        self.slide = image_base + header_reserve
        self.segments = self._layout_segments()

    def _layout_segments(self) -> list[ImageSegment]:
        loads = [x for x in self.elf.program_headers if x.p_type == PT_LOAD]
        if not loads:
            raise Phase3Error("ELF has no PT_LOAD segments")
        if loads[0].vaddr != 0 or not (loads[0].flags & 1):
            raise Phase3Error(
                "current Phase 3 layout expects the first PT_LOAD to be RX at vaddr 0"
            )

        first = loads[0]
        result = [
            ImageSegment(
                name="__TEXT",
                vmaddr=self.image_base,
                vmsize=_align(
                    self.header_reserve + first.memsz,
                    self.page_size,
                ),
                fileoff=0,
                filesize=self.header_reserve + first.filesz,
                protection=VM_PROT_READ | VM_PROT_EXECUTE,
                elf_vaddr=first.vaddr,
                elf_offset=first.offset,
                elf_filesz=first.filesz,
                elf_memsz=first.memsz,
                data_addr=self.slide,
                data_fileoff=self.header_reserve,
                data_size=first.filesz,
                section_name="__elf_rx",
            )
        ]

        cursor = _align(result[0].filesize, self.page_size)
        for index, ph in enumerate(loads[1:], 1):
            new_vm = self.slide + ph.vaddr
            vm_floor = _floor(new_vm, self.page_size)
            delta = new_vm - vm_floor
            fileoff = _align(cursor, self.page_size)
            protection = _mach_prot(ph.flags)

            if ph.flags & 2:
                name = "__ELF_DATA"
                section_name = "__elf_rw"
            else:
                name = f"__ELF_{index}"
                section_name = f"__elf_{index}"

            result.append(
                ImageSegment(
                    name=name,
                    vmaddr=vm_floor,
                    vmsize=_align(delta + ph.memsz, self.page_size),
                    fileoff=fileoff,
                    filesize=delta + ph.filesz,
                    protection=protection,
                    elf_vaddr=ph.vaddr,
                    elf_offset=ph.offset,
                    elf_filesz=ph.filesz,
                    elf_memsz=ph.memsz,
                    data_addr=new_vm,
                    data_fileoff=fileoff + delta,
                    data_size=ph.filesz,
                    section_name=section_name,
                )
            )
            cursor = _align(fileoff + delta + ph.filesz, self.page_size)

        return result

    def _location(self, vaddr: int) -> tuple[int, int, int]:
        for segment_index, seg in enumerate(self.segments):
            if seg.elf_vaddr <= vaddr < seg.elf_vaddr + seg.elf_filesz:
                delta = vaddr - seg.elf_vaddr
                output_offset = seg.data_fileoff + delta
                segment_offset = (self.slide + vaddr) - seg.vmaddr
                return segment_index, output_offset, segment_offset
        raise Phase3Error(
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

    @staticmethod
    def _rebase_stream(locations: list[tuple[int, int]]) -> bytes:
        stream = bytearray([
            REBASE_OPCODE_SET_TYPE_IMM | REBASE_TYPE_POINTER
        ])
        for segment_index, segment_offset in sorted(locations):
            if segment_index > 15:
                raise Phase3Error("classic dyld rebase opcode supports <= 16 segments")
            stream.append(
                REBASE_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB | segment_index
            )
            stream += _uleb128(segment_offset)
            stream.append(REBASE_OPCODE_DO_REBASE_IMM_TIMES | 1)
        stream.append(REBASE_OPCODE_DONE)
        return bytes(stream)

    @staticmethod
    def _bind_stream(bindings: list[dict], shim_ordinal: int = 1) -> bytes:
        if not 1 <= shim_ordinal <= 15:
            raise Phase3Error("immediate dylib ordinal must be in 1..15")

        stream = bytearray([
            BIND_OPCODE_SET_DYLIB_ORDINAL_IMM | shim_ordinal,
            BIND_OPCODE_SET_TYPE_IMM | BIND_TYPE_POINTER,
        ])

        for item in sorted(
            bindings,
            key=lambda x: (x["segment_index"], x["segment_offset"]),
        ):
            segment_index = item["segment_index"]
            if segment_index > 15:
                raise Phase3Error("classic dyld bind opcode supports <= 16 segments")

            stream.append(BIND_OPCODE_SET_SYMBOL_TRAILING_FLAGS_IMM)
            stream += item["shim_symbol"].encode("utf-8") + b"\0"

            if item["addend"]:
                stream.append(BIND_OPCODE_SET_ADDEND_SLEB)
                stream += _sleb128(item["addend"])

            stream.append(
                BIND_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB | segment_index
            )
            stream += _uleb128(item["segment_offset"])
            stream.append(BIND_OPCODE_DO_BIND)

        stream.append(BIND_OPCODE_DONE)
        return bytes(stream)

    def translate(
        self,
        output: str | Path,
        *,
        shim_install_name: str = "@rpath/libbionic_shim.dylib",
        install_name: str = "@rpath/libil2cpp_ported.dylib",
        shim_prefix: str = "_a2i_",
        min_ios: tuple[int, int, int] = (12, 0, 0),
    ) -> dict:
        output = Path(output)

        data_end = max(
            _align(x.fileoff + x.filesize, self.page_size)
            for x in self.segments
        )
        image = bytearray(data_end)

        for seg in self.segments:
            image[
                seg.data_fileoff:seg.data_fileoff + seg.elf_filesz
            ] = self.elf.data[
                seg.elf_offset:seg.elf_offset + seg.elf_filesz
            ]

        symbols = self.elf.dynamic_symbols()
        rebases: list[tuple[int, int]] = []
        bindings: list[dict] = []

        patched = {
            "relative": 0,
            "internal_abs64": 0,
            "internal_glob_dat": 0,
            "internal_jump_slot": 0,
        }

        for rel in self._iter_relocations():
            r_type = rel["type"]
            segment_index, dst, segment_offset = self._location(rel["offset"])

            if r_type == R_AARCH64_RELATIVE:
                struct.pack_into(
                    "<Q",
                    image,
                    dst,
                    (self.slide + rel["addend"]) & 0xFFFFFFFFFFFFFFFF,
                )
                rebases.append((segment_index, segment_offset))
                patched["relative"] += 1
                continue

            if r_type not in (
                R_AARCH64_ABS64,
                R_AARCH64_GLOB_DAT,
                R_AARCH64_JUMP_SLOT,
            ):
                raise Phase3Error(
                    f"unsupported relocation {r_type} at {rel['offset']:#x}"
                )

            symbol_index = rel["symbol_index"]
            if symbol_index >= len(symbols):
                raise Phase3Error(f"bad dynamic symbol index {symbol_index}")
            symbol = symbols[symbol_index]

            if symbol["shndx"] == SHN_UNDEF:
                existing = struct.unpack_from("<Q", image, dst)[0]
                addend = rel["addend"]
                if r_type == R_AARCH64_ABS64:
                    addend += existing

                symbol_type = symbol["info"] & 0x0F
                bindings.append(
                    {
                        "offset": rel["offset"],
                        "type": r_type,
                        "symbol": symbol["name"],
                        "shim_symbol": shim_prefix + symbol["name"],
                        "symbol_type": symbol_type,
                        "segment_index": segment_index,
                        "segment_offset": segment_offset,
                        "addend": addend,
                    }
                )
                continue

            sym_addr = self.slide + symbol["value"]
            value = sym_addr + rel["addend"]

            if r_type == R_AARCH64_ABS64:
                value += struct.unpack_from("<Q", image, dst)[0]
                patched["internal_abs64"] += 1
            elif r_type == R_AARCH64_GLOB_DAT:
                patched["internal_glob_dat"] += 1
            else:
                patched["internal_jump_slot"] += 1

            struct.pack_into(
                "<Q",
                image,
                dst,
                value & 0xFFFFFFFFFFFFFFFF,
            )
            rebases.append((segment_index, segment_offset))

        rebase_stream = self._rebase_stream(rebases)
        bind_stream = self._bind_stream(bindings)

        linkedit_fileoff = _align(len(image), self.page_size)
        rebase_off = linkedit_fileoff
        # Keep __LINKEDIT payloads byte-contiguous for modern codesign.
        bind_off = rebase_off + len(rebase_stream)
        linkedit_size = (
            bind_off - linkedit_fileoff
            + len(bind_stream)
        )

        linkedit_vmaddr = _align(
            max(x.vmaddr + x.vmsize for x in self.segments),
            self.page_size,
        )
        final_size = _align(
            linkedit_fileoff + linkedit_size,
            self.page_size,
        )
        final = bytearray(final_size)
        final[:len(image)] = image
        final[
            rebase_off:rebase_off + len(rebase_stream)
        ] = rebase_stream
        final[
            bind_off:bind_off + len(bind_stream)
        ] = bind_stream

        commands: list[bytes] = [
            _segment_with_data_section(seg)
            for seg in self.segments
        ]
        commands.append(
            _segment_no_sections(
                "__LINKEDIT",
                linkedit_vmaddr,
                _align(linkedit_size, self.page_size),
                linkedit_fileoff,
                linkedit_size,
                VM_PROT_READ,
            )
        )
        commands.append(
            _dylib_command(LC_LOAD_DYLIB, shim_install_name)
        )
        commands.append(
            _dylib_command(LC_ID_DYLIB, install_name)
        )
        commands.append(_build_version_command(min_ios))
        commands.append(
            struct.pack(
                "<IIIIIIIIIIII",
                LC_DYLD_INFO_ONLY,
                48,
                rebase_off,
                len(rebase_stream),
                bind_off,
                len(bind_stream),
                0,
                0,
                0,
                0,
                0,
                0,
            )
        )

        load_commands = b"".join(commands)
        header = struct.pack(
            "<IiiIIIII",
            MH_MAGIC_64,
            CPU_TYPE_ARM64,
            CPU_SUBTYPE_ARM64_ALL,
            MH_DYLIB,
            len(commands),
            len(load_commands),
            MH_DYLDLINK | MH_TWOLEVEL,
            0,
        )

        if len(header) + len(load_commands) > self.header_reserve:
            raise Phase3Error("Mach-O load commands exceed header reserve")

        final[:len(header)] = header
        final[
            len(header):len(header) + len(load_commands)
        ] = load_commands
        output.write_bytes(final)

        shim_symbols = sorted({
            item["shim_symbol"] for item in bindings
        })
        type_names = {0: "notype", 1: "object", 2: "func"}
        by_symbol = {}
        for item in bindings:
            entry = by_symbol.setdefault(
                item["symbol"],
                {
                    "symbol": item["symbol"],
                    "shim_symbol": item["shim_symbol"],
                    "symbol_type": type_names.get(
                        item["symbol_type"],
                        f"type_{item['symbol_type']}",
                    ),
                    "relocation_count": 0,
                },
            )
            entry["relocation_count"] += 1
        shim_imports = [
            by_symbol[name] for name in sorted(by_symbol)
        ]

        return {
            "output": str(output),
            "output_size": len(final),
            "image_base": hex(self.image_base),
            "elf_slide": hex(self.slide),
            "patched": patched,
            "rebase_count": len(rebases),
            "rebase_stream_size": len(rebase_stream),
            "bind_count": len(bindings),
            "bind_stream_size": len(bind_stream),
            "shim_install_name": shim_install_name,
            "shim_symbol_count": len(shim_symbols),
            "shim_symbols": shim_symbols,
            "shim_imports": shim_imports,
            "segments": [
                {
                    "name": seg.name,
                    "vmaddr": hex(seg.vmaddr),
                    "vmsize": hex(seg.vmsize),
                    "fileoff": hex(seg.fileoff),
                    "filesize": hex(seg.filesize),
                    "data_addr": hex(seg.data_addr),
                    "data_fileoff": hex(seg.data_fileoff),
                }
                for seg in self.segments
            ],
            "linkedit": {
                "vmaddr": hex(linkedit_vmaddr),
                "fileoff": hex(linkedit_fileoff),
                "size": linkedit_size,
            },
            "note": (
                "dyld rebase/bind metadata is emitted, but the dylib is still "
                "not runnable without libbionic_shim.dylib, code signing, "
                "constructor/unwind handling, and Unity integration."
            ),
        }


def translate_phase3(
    input_elf: str | Path,
    output: str | Path,
    **kwargs,
) -> dict:
    return Phase3Translator(
        Elf64.from_path(input_elf)
    ).translate(output, **kwargs)
