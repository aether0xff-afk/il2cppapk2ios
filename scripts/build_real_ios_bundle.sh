#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: $0 /path/to/game.apk [output-dir]" >&2
  exit 2
fi

APK="$1"
OUT="${2:-work-real}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

python3 -m pip install -e .
rm -rf "$OUT"
mkdir -p "$OUT"

echo "[1/6] Extracting target APK"
il2cppapk2ios extract "$APK" "$OUT/extracted"

echo "[2/6] Translating real libil2cpp.so to Phase 4 Mach-O"
il2cppapk2ios translate-phase4   "$OUT/extracted/libil2cpp.so"   "$OUT/libil2cpp_ported.dylib"   --json > "$OUT/phase4-report.json"

echo "[3/6] Generating Bionic -> Darwin shim"
il2cppapk2ios generate-shim   "$OUT/extracted/libil2cpp.so"   "$OUT/shim"   --json > "$OUT/shim-report.json"
sh "$OUT/shim/build.sh"
cp "$OUT/shim/build/libbionic_shim.dylib" "$OUT/libbionic_shim.dylib"

echo "[4/6] Compiling iPhoneOS host"
SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
xcrun --sdk iphoneos clang   -arch arm64   -isysroot "$SDK"   -miphoneos-version-min=12.0   -fobjc-arc   ios_host/main.m   -framework Foundation   -framework UIKit   -framework CoreGraphics   -Wl,-rpath,@executable_path   -o "$OUT/A2IHost"

echo "[5/6] Assembling unsigned .app"
APP="$OUT/A2IHost.app"
mkdir -p "$APP"
cp "$OUT/A2IHost" "$APP/A2IHost"
cp ios_host/Info.plist "$APP/Info.plist"
cp "$OUT/libbionic_shim.dylib" "$APP/libbionic_shim.dylib"
cp "$OUT/libil2cpp_ported.dylib" "$APP/libil2cpp_ported.dylib"

# Keep probe dylibs at the app root so IPA resigners do not eagerly treat the
# translated runtime as a launch-time embedded framework.  Pre-sign both files
# here; the host dlopens them only after UIKit is alive.
codesign --force --sign - --timestamp=none --no-strict "$APP/libbionic_shim.dylib"

# Apple's codesign_allocate still rejects the translated image even though
# dyld/llvm accept it.  ldid can emit an ad-hoc SuperBlob for non-ld64 Mach-O
# images and, more importantly, leaves a real LC_CODE_SIGNATURE for IPA
# resigners to replace in-place.
if ! command -v ldid >/dev/null 2>&1; then
  brew install ldid
fi
ldid -S "$APP/libil2cpp_ported.dylib"

codesign --display --verbose=4 "$APP/libbionic_shim.dylib" || true
codesign --display --verbose=4 "$APP/libil2cpp_ported.dylib" || true
if [ -f "$OUT/extracted/global-metadata.dat" ]; then
  cp "$OUT/extracted/global-metadata.dat" "$APP/global-metadata.dat"
fi

echo "[6/6] Structural validation"
file "$APP/A2IHost" "$APP/libbionic_shim.dylib" "$APP/libil2cpp_ported.dylib"
xcrun llvm-objdump --macho --exports-trie "$APP/libil2cpp_ported.dylib"   | grep -E 'il2cpp_init|il2cpp_shutdown' || true
xcrun llvm-objdump --macho --bind "$APP/libil2cpp_ported.dylib"   > "$OUT/ported-bind.txt"
plutil -lint "$APP/Info.plist"

echo
echo "Unsigned real-target bundle: $APP"
echo "[7/7] Packaging unsigned IPA payload"
IPA_ROOT="$OUT/ipa"
rm -rf "$IPA_ROOT"
mkdir -p "$IPA_ROOT/Payload"
cp -R "$APP" "$IPA_ROOT/Payload/A2IHost.app"
(
  cd "$IPA_ROOT"
  /usr/bin/zip -qry "../A2IHost-unsigned.ipa" Payload
)

echo
echo "Unsigned real-target bundle: $APP"
echo "Unsigned IPA payload: $OUT/A2IHost-unsigned.ipa"
echo "Next step: sign with an iOS development/distribution identity + provisioning profile, install, then capture [a2i] device logs."
