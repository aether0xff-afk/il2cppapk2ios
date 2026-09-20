from __future__ import annotations

import argparse
import json

from .apk import UnityApk
from .elf import Elf64
from .macho import build_arm64_macho_skeleton
from .translate import translate_phase2


def dump(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            print(f"{k}: {v}")
    else:
        print(obj)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="il2cppapk2ios")
    sub = parser.add_subparsers(dest="cmd", required=True)

    apk = sub.add_parser("apk-report", help="inspect a Unity APK")
    apk.add_argument("apk")
    apk.add_argument("--json", action="store_true")

    elf = sub.add_parser("elf-report", help="inspect an ELF64 shared object")
    elf.add_argument("elf")
    elf.add_argument("--json", action="store_true")

    extract = sub.add_parser("extract", help="extract ARM64 Unity/IL2CPP payload")
    extract.add_argument("apk")
    extract.add_argument("output")

    macho = sub.add_parser("macho-smoke", help="emit unsigned ARM64 Mach-O skeleton")
    macho.add_argument("output")

    phase2 = sub.add_parser(
        "translate-phase2",
        help="copy AArch64 PT_LOAD segments into Mach-O and resolve internal relocations",
    )
    phase2.add_argument("elf")
    phase2.add_argument("output")
    phase2.add_argument("--json", action="store_true")
    phase2.add_argument(
        "--install-name",
        default="@rpath/libil2cpp_ported.dylib",
    )

    args = parser.parse_args(argv)

    if args.cmd == "apk-report":
        dump(UnityApk(args.apk).report(), args.json)
    elif args.cmd == "elf-report":
        dump(Elf64.from_path(args.elf).report(), args.json)
    elif args.cmd == "extract":
        for path in UnityApk(args.apk).extract_arm64(args.output):
            print(path)
    elif args.cmd == "macho-smoke":
        print(build_arm64_macho_skeleton(args.output))
    elif args.cmd == "translate-phase2":
        report = translate_phase2(
            args.elf,
            args.output,
            install_name=args.install_name,
        )
        dump(report, args.json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
