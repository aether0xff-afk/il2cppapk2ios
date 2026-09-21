import struct

from il2cppapk2ios.elf import ProgramHeader, SectionHeader, EM_AARCH64, ET_DYN, SHT_RELA
from il2cppapk2ios.phase3 import R_AARCH64_RELATIVE
from il2cppapk2ios.phase4 import Phase4Translator


class FakeElf:
    e_machine = EM_AARCH64
    e_type = ET_DYN

    def __init__(self):
        self.data = bytearray(0x800)
        self.program_headers = [
            ProgramHeader(1, 5, 0x000, 0x0000, 0x300, 0x300, 0x1000),
            ProgramHeader(1, 6, 0x300, 0x2000, 0x300, 0x380, 0x1000),
        ]

        rel = struct.pack("<QQq", 0x2020, R_AARCH64_RELATIVE, 0x80)
        self.data[0x700:0x718] = rel

        self.sections = [
            SectionHeader(0, 1, 0, 0x40, 0x40, 0x80, 0, 0, 4, 0, ".text"),
            SectionHeader(0, 1, 0, 0x180, 0x180, 0x40, 0, 0, 8, 0, ".eh_frame"),
            SectionHeader(0, 14, 0, 0x2020, 0x320, 8, 0, 0, 8, 8, ".init_array"),
            SectionHeader(0, SHT_RELA, 0, 0, 0x700, 0x18, 0, 0, 8, 24, ".rela.dyn"),
        ]

    def dynamic_symbols(self):
        return [
            {"name": "", "info": 0, "other": 0, "shndx": 0, "value": 0, "size": 0},
            {
                "name": "il2cpp_init",
                "info": (1 << 4) | 2,
                "other": 0,
                "shndx": 1,
                "value": 0x80,
                "size": 16,
            },
        ]

    def section(self, name):
        return next((s for s in self.sections if s.name == name), None)


def test_phase4_adds_exports_and_init_metadata(tmp_path):
    out = tmp_path / "phase4.dylib"
    report = Phase4Translator(FakeElf()).translate(out)
    data = out.read_bytes()

    assert report["phase"] == 4
    assert report["export_count"] == 1
    assert report["has_il2cpp_init"] is True
    assert report["init_array_entries"] == 1
    assert report["eh_frame_size"] == 0x40
    assert b"__mod_init_func" in data
    assert b"__eh_frame" in data
    assert report["rebase_count"] == 1
