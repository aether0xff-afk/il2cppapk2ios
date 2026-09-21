#!/bin/sh
set -eu

if [ "$#" -lt 2 ]; then
  echo "usage: $0 /path/to/A2IHost.app 'Apple Development: Name (TEAMID)'" >&2
  exit 2
fi

APP="$1"
IDENTITY="$2"

codesign --force --sign "$IDENTITY" --timestamp=none "$APP/libbionic_shim.dylib"
codesign --force --sign "$IDENTITY" --timestamp=none "$APP/libil2cpp_ported.dylib"
codesign --force --sign "$IDENTITY" --timestamp=none "$APP/A2IHost"
codesign --force --sign "$IDENTITY" --timestamp=none "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"
echo "Signed and verified: $APP"
