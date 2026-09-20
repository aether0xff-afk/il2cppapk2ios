import struct

from il2cppapk2ios.elf import ProgramHeader, SectionHeader, EM_AARCH64, ET_DYN, SHT_RELA
from il2cppapk2ios.translate import (
    Phase2Translator,
    ELF_SLIDE,
    R_AARCH64_RELATIVE,
    R_AARCH64_GLOB_DAT,
)


class FakeElf:
    e_machine = EM_AARCH64
    e_type = ET_DYN

    def __init__(self):
        self.data = bytearray(0x240)
        self.program_headers = [
            ProgramHeader(
                p_type=1,
                flags=6,
                offset=0,
                vaddr=0x1000,
                filesz=0x200,
                memsz=0x300,
                align=0x1000,
            )
        ]

        rel1 = struct.pack("<QQq", 0x1080, R_AARCH64_RELATIVE, 0x1050)
        rel2 = struct.pack(
            "<QQq",
            0x1088,
            (1 << 32) | R_AARCH64_GLOB_DAT,
            0,
        )
        self.data[0x200:0x218] = rel1
        self.data[0x218:0x230] = rel2
        self.sections = [
            SectionHeader(
                name_off=0,
                sh_type=SHT_RELA,
                flags=0,
                addr=0,
                offset=0x200,
                size=0x30,
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
            {"name": "inside", "info": 0, "other": 0, "shndx": 1, "value": 0x1100, "size": 0},
        ]


def test_phase2_patches_relative_and_internal_symbol(tmp_path):
    elf = FakeElf()
    translator = Phase2Translator(elf)
    out = tmp_path / "ported.dylib"
    report = translator.translate(out)

    image = out.read_bytes()
    rel_off = translator._vaddr_to_output_offset(0x1080)
    glob_off = translator._vaddr_to_output_offset(0x1088)

    assert struct.unpack_from("<Q", image, rel_off)[0] == ELF_SLIDE + 0x1050
    assert struct.unpack_from("<Q", image, glob_off)[0] == ELF_SLIDE + 0x1100
    assert report["patched_total"] == 2
    assert report["unresolved_relocations"] == 0
