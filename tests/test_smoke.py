import struct
from pathlib import Path

from il2cppapk2ios.macho import (
    build_arm64_macho_skeleton,
    MH_MAGIC_64,
    CPU_TYPE_ARM64,
)


def test_macho_smoke(tmp_path: Path):
    out = build_arm64_macho_skeleton(tmp_path / "smoke")
    data = out.read_bytes()
    magic, cputype = struct.unpack_from("<Ii", data, 0)
    assert magic == MH_MAGIC_64
    assert cputype == CPU_TYPE_ARM64
    assert len(data) > 0x4000
