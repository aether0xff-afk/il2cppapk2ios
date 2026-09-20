from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from collections import Counter

ELF_MAGIC = b"\x7fELF"
EM_AARCH64 = 183
ET_DYN = 3
SHT_DYNSYM = 11
SHT_RELA = 4
SHN_UNDEF = 0
DT_NEEDED = 1

AARCH64_RELOCS = {
    257: "R_AARCH64_ABS64",
    1025: "R_AARCH64_GLOB_DAT",
    1026: "R_AARCH64_JUMP_SLOT",
    1027: "R_AARCH64_RELATIVE",
}

ANDROID_IMPORT_PREFIXES = (
    "__android_", "ANativeWindow_", "ALooper_", "AInput", "AAsset",
    "AMotionEvent_", "AKeyEvent_", "android_",
)

@dataclass(frozen=True)
class ProgramHeader:
    p_type: int
    flags: int
    offset: int
    vaddr: int
    filesz: int
    memsz: int
    align: int

@dataclass(frozen=True)
class SectionHeader:
    name_off: int
    sh_type: int
    flags: int
    addr: int
    offset: int
    size: int
    link: int
    info: int
    addralign: int
    entsize: int
    name: str = ""

class ElfError(ValueError):
    pass

class Elf64:
    def __init__(self, data: bytes, source: str = "<memory>"):
        self.data = data
        self.source = source
        self._parse_header()
        self.program_headers = self._parse_program_headers()
        self.sections = self._parse_sections()

    @classmethod
    def from_path(cls, path: str | Path) -> "Elf64":
        p = Path(path)
        return cls(p.read_bytes(), str(p))

    def _parse_header(self) -> None:
        if len(self.data) < 64 or self.data[:4] != ELF_MAGIC:
            raise ElfError("not an ELF file")
        if self.data[4] != 2:
            raise ElfError("only ELF64 is supported")
        if self.data[5] != 1:
            raise ElfError("only little-endian ELF is supported")
        hdr = struct.unpack_from("<16sHHIQQQIHHHHHH", self.data, 0)
        (_, self.e_type, self.e_machine, self.e_version, self.e_entry,
         self.e_phoff, self.e_shoff, self.e_flags, self.e_ehsize,
         self.e_phentsize, self.e_phnum, self.e_shentsize, self.e_shnum,
         self.e_shstrndx) = hdr

    def _parse_program_headers(self) -> list[ProgramHeader]:
        out = []
        for i in range(self.e_phnum):
            off = self.e_phoff + i * self.e_phentsize
            vals = struct.unpack_from("<IIQQQQQQ", self.data, off)
            p_type, flags, p_offset, vaddr, _paddr, filesz, memsz, align = vals
            out.append(ProgramHeader(p_type, flags, p_offset, vaddr, filesz, memsz, align))
        return out

    def _raw_sections(self) -> list[SectionHeader]:
        out = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            out.append(SectionHeader(*struct.unpack_from("<IIQQQQIIQQ", self.data, off)))
        return out

    @staticmethod
    def _cstring(buf: bytes, off: int) -> str:
        if off < 0 or off >= len(buf):
            return ""
        end = buf.find(b"\0", off)
        if end < 0:
            end = len(buf)
        return buf[off:end].decode("utf-8", "replace")

    def _parse_sections(self) -> list[SectionHeader]:
        raw = self._raw_sections()
        if not raw or self.e_shstrndx >= len(raw):
            return raw
        shstr = raw[self.e_shstrndx]
        names = self.data[shstr.offset:shstr.offset + shstr.size]
        return [SectionHeader(**{**s.__dict__, "name": self._cstring(names, s.name_off)}) for s in raw]

    def section(self, name: str) -> SectionHeader | None:
        return next((s for s in self.sections if s.name == name), None)

    def section_data(self, sec: SectionHeader) -> bytes:
        return self.data[sec.offset:sec.offset + sec.size]

    def dynamic_symbols(self) -> list[dict]:
        sec = next((s for s in self.sections if s.sh_type == SHT_DYNSYM), None)
        if not sec or sec.link >= len(self.sections):
            return []
        strtab = self.section_data(self.sections[sec.link])
        entsize = sec.entsize or 24
        out = []
        for off in range(sec.offset, sec.offset + sec.size, entsize):
            if off + 24 > len(self.data):
                break
            st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", self.data, off)
            out.append({
                "name": self._cstring(strtab, st_name),
                "info": st_info,
                "other": st_other,
                "shndx": st_shndx,
                "value": st_value,
                "size": st_size,
            })
        return out

    def undefined_symbols(self) -> list[str]:
        return sorted({s["name"] for s in self.dynamic_symbols() if s["shndx"] == SHN_UNDEF and s["name"]})

    def needed_libraries(self) -> list[str]:
        dyn = self.section(".dynamic")
        if not dyn or dyn.link >= len(self.sections):
            return []
        strtab = self.section_data(self.sections[dyn.link])
        out = []
        for off in range(dyn.offset, dyn.offset + dyn.size, 16):
            if off + 16 > len(self.data):
                break
            tag, val = struct.unpack_from("<qQ", self.data, off)
            if tag == DT_NEEDED:
                out.append(self._cstring(strtab, val))
            if tag == 0:
                break
        return out

    def relocation_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for sec in self.sections:
            if sec.sh_type != SHT_RELA:
                continue
            entsize = sec.entsize or 24
            for off in range(sec.offset, sec.offset + sec.size, entsize):
                if off + 24 > len(self.data):
                    break
                _r_offset, r_info, _r_addend = struct.unpack_from("<QQq", self.data, off)
                r_type = r_info & 0xffffffff
                counts[AARCH64_RELOCS.get(r_type, f"AARCH64_RELOC_{r_type}")] += 1
        return dict(sorted(counts.items()))

    def android_specific_imports(self) -> list[str]:
        return [s for s in self.undefined_symbols() if s.startswith(ANDROID_IMPORT_PREFIXES)]

    def report(self) -> dict:
        imports = self.undefined_symbols()
        android_imports = self.android_specific_imports()
        reloc_counts = self.relocation_counts()
        unknown_relocs = [k for k in reloc_counts if k not in set(AARCH64_RELOCS.values())]
        return {
            "source": self.source,
            "elf64": True,
            "machine": self.e_machine,
            "is_aarch64": self.e_machine == EM_AARCH64,
            "type": self.e_type,
            "is_shared_object": self.e_type == ET_DYN,
            "entry": hex(self.e_entry),
            "program_headers": len(self.program_headers),
            "sections": len(self.sections),
            "needed": self.needed_libraries(),
            "undefined_import_count": len(imports),
            "android_specific_import_count": len(android_imports),
            "android_specific_imports": android_imports,
            "relocations": reloc_counts,
            "unknown_relocation_types": unknown_relocs,
            "phase1_candidate": self.e_machine == EM_AARCH64 and self.e_type == ET_DYN and not unknown_relocs,
        }
