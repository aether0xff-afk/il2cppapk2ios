import struct

from il2cppapk2ios.elf import ProgramHeader, SectionHeader, EM_AARCH64, ET_DYN, SHT_RELA
from il2cppapk2ios.phase3 import (
    Phase3Translator,
    R_AARCH64_RELATIVE,
    R_AARCH64_GLOB_DAT,
    R_AARCH64_JUMP_SLOT,
    MH_MAGIC_64,
)


class FakeElf:
    e_machine = EM_AARCH64
    e_type = ET_DYN

    def __init__(self):
        self.data = bytearray(0x400)
        self.program_headers = [
            ProgramHeader(1, 5, 0x000, 0x0000, 0x100, 0x100, 0x1000),
            ProgramHeader(1, 6, 0x100, 0x2000, 0x100, 0x180, 0x1000),
        ]

        relocs = [
            struct.pack("<QQq", 0x2020, R_AARCH64_RELATIVE, 0x50),
            struct.pack("<QQq", 0x2028, (1 << 32) | R_AARCH64_GLOB_DAT, 0),
            struct.pack("<QQq", 0x2030, (2 << 32) | R_AARCH64_JUMP_SLOT, 0),
        ]
        blob = b"".join(relocs)
        self.data[0x300:0x300 + len(blob)] = blob
        self.sections = [
            SectionHeader(
                name_off=0,
                sh_type=SHT_RELA,
                flags=0,
                addr=0,
                offset=0x300,
                size=len(blob),
                link=0,
                info=0,
                addralign=8,
                entsize=24,
                name=".rela.dyn",
            )
        ]

    def dynamic_symbols(self):
        return [
            {"name": "", "info": 0, "other": 0, "shndx": 0, "value": 0, "size": 0},
            {"name": "inside", "info": 2, "other": 0, "shndx": 1, "value": 0x80, "size": 0},
            {"name": "malloc", "info": 2, "other": 0, "shndx": 0, "value": 0, "size": 0},
        ]


def test_phase3_emits_rebases_and_external_bind(tmp_path):
    elf = FakeElf()
    translator = Phase3Translator(elf)
    out = tmp_path / "ported.dylib"
    report = translator.translate(out)

    data = out.read_bytes()
    magic = struct.unpack_from("<I", data, 0)[0]

    assert magic == MH_MAGIC_64
    assert report["rebase_count"] == 2
    assert report["bind_count"] == 1
    assert report["shim_symbol_count"] == 1
    assert report["shim_symbols"] == ["_a2i_malloc"]
    assert b"@rpath/libbionic_shim.dylib\0" in data
    assert b"_a2i_malloc\0" in data
