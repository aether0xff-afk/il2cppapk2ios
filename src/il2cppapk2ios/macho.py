from __future__ import annotations

from pathlib import Path
import struct

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64_ALL = 0
MH_EXECUTE = 2
LC_SEGMENT_64 = 0x19
LC_MAIN = 0x80000028
PAGE = 0x4000


def build_arm64_macho_skeleton(path: str | Path) -> Path:
    """Emit an unsigned ARM64 Mach-O skeleton for format validation.

    This is not an ELF translator and does not contain ported game code.
    """
    code = b"\x00\x00\x80\x52\xc0\x03\x5f\xd6"

    seg_cmdsize = 72 + 80
    segment = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64, seg_cmdsize, b"__TEXT" + b"\0" * 10,
        0x100000000, PAGE + len(code), 0, PAGE + len(code),
        5, 5, 1, 0,
    )
    section = struct.pack(
        "<16s16sQQIIIIIIII",
        b"__text" + b"\0" * 10, b"__TEXT" + b"\0" * 10,
        0x100000000 + PAGE, len(code), PAGE, 2,
        0, 0, 0x80000400, 0, 0, 0,
    )
    main = struct.pack("<IIQQ", LC_MAIN, 24, PAGE, 0)
    commands = segment + section + main
    header = struct.pack(
        "<IiiIIIII",
        MH_MAGIC_64, CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL,
        MH_EXECUTE, 2, len(commands), 0, 0,
    )

    image = header + commands
    image += b"\0" * (PAGE - len(image))
    image += code

    out = Path(path)
    out.write_bytes(image)
    return out
