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
- Phase 2 PT_LOAD -> Mach-O segment mapping with one constant virtual-address slide
- AOT application of R_AARCH64_RELATIVE and defined-symbol ABS64/GLOB_DAT/JUMP_SLOT relocations
- Phase 3 Mach-O dyld rebase/bind tables for ASLR-safe pointers and external imports
- automatic Bionic-to-Darwin shim scaffold generation from the target ELF

Not implemented yet:

- semantic Bionic -> Darwin adapters for pthread/stat/socket/stdio/etc.
- real implementations for Bionic object imports (__sF and _ctype_)
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

## Phase 2 translation

```bash
il2cppapk2ios translate-phase2 work/libil2cpp.so work/libil2cpp-phase2.dylib --json
```

For the Squad Strike 3 2.1 target, Phase 2 currently resolves **86,486 / 86,681 relocations** ahead of time:

- 86,105 R_AARCH64_RELATIVE
- 213 defined-symbol R_AARCH64_ABS64
- 31 defined-symbol R_AARCH64_GLOB_DAT
- 137 defined-symbol R_AARCH64_JUMP_SLOT

195 external relocations remain, covering 188 unique undefined symbols. The generated artifact is recognized by LLVM/file as an ARM64 Mach-O dylib, but is intentionally **not runnable yet** because those external imports and Darwin ABI shims have not been emitted.

On Windows, the target flow is reproducible with:

```powershell
.\scripts\squadstrike_phase2.ps1 -Apk "C:\path\to\game.apk"
```

## Phase 3: dyld rebase/bind + shim scaffold

Phase 3 converts all statically-known pointers into Mach-O dyld rebase entries,
so iOS ASLR can slide them. Undefined Android imports are emitted as dyld binds
against @rpath/libbionic_shim.dylib.

```bash
il2cppapk2ios translate-phase3 work/libil2cpp.so work/libil2cpp-phase3.dylib --json
il2cppapk2ios generate-shim work/libil2cpp.so work/shim --json
```

On macOS with Xcode, the generated shim scaffold can then be built with:

```bash
sh work/shim/build.sh
```

For Squad Strike 3 2.1, the translated dylib contains **86,486 dyld rebase
entries** and **195 bind entries** targeting **188 unique imports**. LLVM's
Mach-O parser accepts the complete rebase and bind tables.

The current generated shim classifies the 188 imports as:

- 79 conservative direct Darwin tail-calls
- 6 hand-written special adapters
- 2 Bionic object symbols that still need real semantics: _ctype_ and __sF
- 101 fail-fast trap stubs that still require ABI/semantic adapters

Trap stubs are intentional: an unsupported API should fail at the exact import
name rather than silently corrupting Android data structures on Darwin.

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
3. Phase 2 — translate loadable segments + statically resolvable relocations ✅
4. Phase 3A — Mach-O dyld rebase/bind translation ✅
5. Phase 3B — generated Bionic shim scaffold ✅; semantic adapters in progress
6. Phase 4 — constructors, unwind metadata, IL2CPP initialization + global-metadata.dat
7. Phase 5 — Unity player strategy
8. Phase 6 — signing, IPA packaging, real-device test

## Prior art

- Cloudef/android2gnulinux
- zhkl0228/unidbg
- libhybris/libhybris
- NikkaGames/ELFLoaderARM

These solve different host/runtime problems; none is a drop-in APK-to-IPA converter.
