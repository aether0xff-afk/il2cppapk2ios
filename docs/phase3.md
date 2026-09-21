# Phase 3 validation

Target: Squad Strike 3 2.1, Unity 2018.1.5f1, arm64-v8a IL2CPP.

## What now works

The translator preserves the original AArch64 machine code and converts the
Android ELF loader work into Mach-O loader metadata.

For the target libil2cpp.so:

- total Android AArch64 relocations: 86,681
- internal/static relocations converted to Mach-O rebase entries: 86,486
- external relocations converted to Mach-O bind entries: 195
- unique external symbols: 188

The generated dylib is recognized as:

    Mach-O 64-bit arm64 dynamically linked shared library, flags:<|DYLDLINK|TWOLEVEL>

Full-table validation with LLVM succeeds:

    llvm-objdump --macho --rebase libil2cpp-phase3.dylib
    llvm-objdump --macho --bind libil2cpp-phase3.dylib
    llvm-objdump --macho --private-headers libil2cpp-phase3.dylib

The complete tables contain 86,486 rebase entries and 195 bind entries.

Every external bind currently points at:

    @rpath/libbionic_shim.dylib

using a generated shim name such as:

    malloc          -> _a2i_malloc
    pthread_create  -> _a2i_pthread_create
    __sF            -> _a2i___sF

## Import types

The target has:

- 184 undefined function symbols
- 2 undefined object symbols: _ctype_ and __sF
- 2 weak/notype Google blocking-region hooks

The object symbols matter: a generic function trampoline cannot implement them.
In particular, __sF exposes Bionic stdio object storage and _ctype_ exposes
Bionic ctype data. They require data-layout-compatible emulation or code-path
replacement.

## Generated shim scaffold

The current generator classifies the 188 imports into:

- direct: 79
- special: 6
- object: 2
- trap: 101

Direct entries are intentionally conservative ARM64 tail-calls to Darwin.
Special entries currently include __errno, __system_property_get, gettid,
memalign and the two weak Google hooks.

Trap entries abort with the exact unresolved API name. This is preferable to
silently passing Android structs or constants into incompatible Darwin APIs.

## Next ABI blocks

The largest coherent block is pthread. Android NDK r17 LP64 uses opaque storage
sizes/layouts different from Darwin, including a 40-byte pthread_mutex_t and a
48-byte pthread_cond_t. Calling Darwin pthread APIs directly on these Android
objects is therefore not valid.

The planned bridge stores/maps host pthread objects behind Android-sized
storage and translates Android attr/clock/type values explicitly.

Other major adapters still required:

- stat/fstat/lstat structures
- dirent/opendir/readdir
- socket address and option layouts/constants
- mmap/open/poll flags
- signal/sigset layouts
- sem_t
- stdio FILE/__sF
- ctype table
- Android system-property behavior
- constructors and exception unwind metadata

## Important limitation

Passing LLVM's Mach-O parser does not mean the binary is ready to execute on an
iPhone or iPad. The Phase 3 result is now a structurally valid translated
Mach-O dependency graph, but runtime ABI compatibility is the next problem.
