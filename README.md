# il2cppapk2ios

Research PoC for testing whether **ARM64 Unity IL2CPP Android machine code can be carried toward iOS without reconstructing the original C# project**.

This repository deliberately does **not** claim that wrapping an APK as an IPA works. Android ARM64 Unity binaries are ELF/Bionic binaries while iOS executes signed Mach-O/Darwin binaries. The first milestone is to measure the ABI gap and prove the pieces independently.

## Current milestone

Implemented:

- Unity APK inspection (ABIs, native libraries, global-metadata.dat, Unity version)
- extraction of arm64-v8a/libil2cpp.so, libunity.so, libmain.so and metadata
- dependency/import/relocation inspection for ELF64
- AArch64 relocation classification for ABS64, GLOB_DAT, JUMP_SLOT and RELATIVE
- detection of obvious Android-only native imports
- generation of a tiny **unsigned ARM64 iOS Mach-O smoke executable**

Not implemented yet:

- ELF relocation rewriting into Mach-O fixups
- Bionic -> Darwin ABI shims
- JNI bridge
- Unity Android player -> iOS graphics/input/audio bridge
- code signing / IPA packaging
- libil2cpp.so execution on iOS

The project fails loudly rather than pretending an ELF-to-Mach-O conversion is already solved.

## Install

```bash
python -m pip install -e .
```

## Inspect a Unity APK

```bash
il2cppapk2ios apk-report game.apk --json
il2cppapk2ios extract game.apk work
il2cppapk2ios elf-report work/libil2cpp.so --json
il2cppapk2ios elf-report work/libunity.so --json
```

On Windows:

```powershell
.\scripts\squadstrike_preflight.ps1 -Apk "C:\path\to\game.apk"
```

## Mach-O writer smoke test

```bash
il2cppapk2ios macho-smoke smoke-arm64-ios
file smoke-arm64-ios
```

Expected host-side identification:

```text
Mach-O 64-bit arm64 executable
```

It is intentionally unsigned. Device execution requires Apple signing/package work; this command only proves the binary writer.

## Squad Strike 3 2.1 preflight

The initial target APK contains Unity 2018.1.5f1, arm64-v8a/libil2cpp.so, libunity.so, libmain.so, and global-metadata.dat.

libil2cpp.so is an AArch64 ET_DYN ELF. Its relocation set uses R_AARCH64_RELATIVE, ABS64, GLOB_DAT and JUMP_SLOT. The Android-specific import scan found none in libil2cpp.so, while libunity.so directly imports Android window/input/logging APIs. That makes libil2cpp.so the better first target.

## Roadmap

1. Phase 1 — ELF64/AArch64 parser + reproducible reports ✅
2. Phase 1.5 — Mach-O writer smoke binary ✅
3. Phase 2 — translate loadable segments + R_AARCH64_RELATIVE
4. Phase 3 — import thunk table and Darwin shims for libc/pthread/math/dl
5. Phase 4 — IL2CPP initialization + global-metadata.dat
6. Phase 5 — Unity player strategy
7. Phase 6 — signing, IPA packaging, real-device test

## Prior art

- Cloudef/android2gnulinux
- zhkl0228/unidbg
- libhybris/libhybris
- NikkaGames/ELFLoaderARM

These solve different host/runtime problems; none is a drop-in APK-to-IPA converter.
