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
xcrun --sdk iphoneos clang   -arch arm64   -isysroot "$SDK"   -miphoneos-version-min=12.0   -fobjc-arc   ios_host/main.m   -framework Foundation   -framework UIKit   -framework CoreGraphics   -Wl,-rpath,@executable_path/Frameworks   -o "$OUT/A2IHost"

echo "[5/6] Assembling unsigned .app"
APP="$OUT/A2IHost.app"
mkdir -p "$APP"
cp "$OUT/A2IHost" "$APP/A2IHost"
cp ios_host/Info.plist "$APP/Info.plist"
mkdir -p "$APP/Frameworks"
cp "$OUT/libbionic_shim.dylib" "$APP/Frameworks/libbionic_shim.dylib"
cp "$OUT/libil2cpp_ported.dylib" "$APP/Frameworks/libil2cpp_ported.dylib"

# Deliberately leave both embedded dylibs unsigned here.  The IPA itself is
# unsigned, so the user's IPA signer must sign the app executable and every
# Mach-O under Frameworks with the same identity.  Keeping them in Frameworks
# makes recursive resigners discover them instead of treating them as opaque
# bundle resources.

if [ -f "$OUT/extracted/global-metadata.dat" ]; then
  cp "$OUT/extracted/global-metadata.dat" "$APP/global-metadata.dat"
fi

echo "[6/6] Structural validation"
file "$APP/A2IHost" "$APP/Frameworks/libbionic_shim.dylib" "$APP/Frameworks/libil2cpp_ported.dylib"
xcrun llvm-objdump --macho --exports-trie "$APP/Frameworks/libil2cpp_ported.dylib"   | grep -E 'il2cpp_init|il2cpp_shutdown' || true
xcrun llvm-objdump --macho --bind "$APP/Frameworks/libil2cpp_ported.dylib"   > "$OUT/ported-bind.txt"
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
