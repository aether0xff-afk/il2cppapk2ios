# iOS runtime probe

The host intentionally does one thing: load the generated Bionic shim, load the
translated IL2CPP dylib, resolve il2cpp_init, and call it while emitting [a2i]
logs.

## Build from a real APK

On macOS with Xcode installed:

    sh scripts/build_real_ios_bundle.sh "/path/to/game.apk"

The result is:

    work-real/A2IHost.app

It contains:

- A2IHost
- libbionic_shim.dylib
- libil2cpp_ported.dylib
- global-metadata.dat when present in the APK

## Sign

Use an Apple Development identity available in your keychain:

    security find-identity -v -p codesigning
    sh scripts/sign_ios_bundle.sh work-real/A2IHost.app "Apple Development: ..."

The app still needs a valid provisioning/install path for the target iPad.

## Expected device log milestones

    [a2i] iOS host started
    [a2i] shim loaded
    [a2i] translated libil2cpp loaded
    [a2i] il2cpp_init=...
    [a2i] metadata present at ...
    [a2i] calling il2cpp_init
    [a2i] il2cpp_init returned

The first missing line is the next concrete blocker. A crash before the host
starts is a Mach-O/signing/loader issue. A failure at the translated dylib
dlopen is a dyld/import/constructor issue. A crash after calling il2cpp_init is
an IL2CPP runtime/metadata/ABI issue.

This host does not include Unity rendering, input, audio, or the Android
libunity player.
