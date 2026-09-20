# Squad Strike 3 2.1 preflight

Target APK used for the first PoC.

## APK

- size: 60,825,630 bytes
- Unity: 2018.1.5f1
- ABIs: arm64-v8a, armeabi-v7a
- arm64 libil2cpp.so: 11,630,320 bytes
- arm64 libunity.so: 14,155,408 bytes
- arm64 libmain.so: 6,936 bytes
- global-metadata.dat: 2,660,432 bytes

## libil2cpp.so

- ELF64 AArch64
- ET_DYN
- entry: 0x206e70
- undefined imports: 188
- obvious Android-specific imports detected: 0
- DT_NEEDED:
  - liblog.so
  - libstdc++.so
  - libm.so
  - libdl.so
  - libc.so

Relocations:

- R_AARCH64_RELATIVE: 86,105
- R_AARCH64_JUMP_SLOT: 323
- R_AARCH64_ABS64: 217
- R_AARCH64_GLOB_DAT: 36
- unknown relocation types: 0

This makes libil2cpp.so a reasonable first target for a static/AOT translation experiment. It does not prove Darwin ABI compatibility.

## libunity.so

- ELF64 AArch64
- undefined imports: 333
- obvious Android-specific imports detected: 16
- directly depends on libandroid.so, libGLESv2.so and libEGL.so

Detected Android-side symbols include ANativeWindow, ALooper, AInputEvent/AKeyEvent and __android_log functions.

Conclusion: do not start by trying to reuse Android libunity.so unchanged. First attack libil2cpp.so relocation/import translation and Darwin shims.
