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
xcrun --sdk iphoneos clang   -arch arm64   -isysroot "$SDK"   -miphoneos-version-min=12.0   -fobjc-arc   ios_host/main.m   -framework Foundation   -o "$OUT/A2IHost"

echo "[5/6] Assembling unsigned .app"
APP="$OUT/A2IHost.app"
mkdir -p "$APP"
cp "$OUT/A2IHost" "$APP/A2IHost"
cp ios_host/Info.plist "$APP/Info.plist"
cp "$OUT/libbionic_shim.dylib" "$APP/libbionic_shim.dylib"
cp "$OUT/libil2cpp_ported.dylib" "$APP/libil2cpp_ported.dylib"
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
echo "Next step: sign the executable and both dylibs with the same iOS identity, install, then capture [a2i] device logs."
