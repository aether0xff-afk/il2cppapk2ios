from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from .elf import Elf64, SHN_UNDEF


DIRECT_SYMBOLS = {
    "__cxa_atexit", "__cxa_finalize", "abort", "access", "acos", "acosf",
    "atan2", "atan2f", "atoi", "atol", "bsearch", "calloc", "close",
    "cos", "cosf", "difftime", "exit", "exp2f", "fmod", "fmodf", "free",
    "getcwd", "getenv", "gethostname", "getpid", "gettimeofday", "isalpha",
    "isspace", "log", "lseek", "malloc", "memchr", "memcmp", "memcpy",
    "memmove", "memset", "modf", "nanosleep", "pipe", "pow", "powf",
    "read", "readlink", "realloc", "sched_yield", "setenv", "sin", "sinf",
    "sqrt", "sqrtf", "strchr", "strcmp", "strcoll", "strcpy", "strlcpy",
    "strlen", "strncmp", "strncpy", "strrchr", "strtod", "strtof", "strtol",
    "strtold", "strtoul", "strxfrm", "tan", "time", "tolower", "towlower",
    "towupper", "unlink", "unsetenv", "usleep", "wcslen", "wmemchr",
    "wmemcpy", "wmemmove", "wmemset", "write",
}

SPECIAL_SYMBOLS = {
    "__errno",
    "__google_potentially_blocking_region_begin",
    "__google_potentially_blocking_region_end",
    "__system_property_get",
    "gettid",
    "memalign",
}

OBJECT_SYMBOLS = {"__sF", "_ctype_"}
TYPE_NAMES = {0: "notype", 1: "object", 2: "func"}


def _asm_name(c_symbol: str) -> str:
    return "_" + c_symbol


def _shim_c_name(original: str) -> str:
    return "a2i_" + original


def _direct_assembly(symbols: list[str]) -> str:
    lines = [
        ".text",
        ".p2align 2",
        "",
        "// ABI-compatible first-pass tail calls.",
        "",
    ]
    for name in sorted(symbols):
        shim = _asm_name(_shim_c_name(name))
        host = _asm_name(name)
        lines += [
            f".globl {shim}",
            f"{shim}:",
            f"    b {host}",
            "",
        ]
    return "\n".join(lines)


def _trap_assembly(symbols: list[str]) -> str:
    lines = [
        ".text",
        ".p2align 2",
        "",
        "// Unsupported imports fail fast and print their symbol name.",
        "",
    ]
    string_lines = [".section __TEXT,__cstring,cstring_literals", ""]
    for index, name in enumerate(sorted(symbols)):
        shim = _asm_name(_shim_c_name(name))
        label = f"L_a2i_name_{index}"
        lines += [
            f".globl {shim}",
            f"{shim}:",
            f"    adrp x0, {label}@PAGE",
            f"    add x0, x0, {label}@PAGEOFF",
            "    b _a2i_unimplemented",
            "",
        ]
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        string_lines += [f"{label}:", f'    .asciz "{escaped}"', ""]
    return "\n".join(lines + string_lines)


SPECIAL_C = r"""#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef PROP_VALUE_MAX
#define PROP_VALUE_MAX 92
#endif

__attribute__((noreturn))
void a2i_unimplemented(const char *symbol) {
    fprintf(stderr, "[il2cppapk2ios] unimplemented Android import: %s\n",
            symbol ? symbol : "<unknown>");
    abort();
}

int *a2i___errno(void) {
    return __error();
}

int a2i___system_property_get(const char *name, char *value) {
    const char *answer = "";

    if (name == NULL || value == NULL) {
        return 0;
    }

    if (strcmp(name, "ro.product.cpu.abi") == 0) {
        answer = "arm64-v8a";
    } else if (strcmp(name, "ro.product.cpu.abilist") == 0) {
        answer = "arm64-v8a";
    } else if (strcmp(name, "ro.build.version.sdk") == 0) {
        answer = "28";
    }

    size_t len = strlen(answer);
    if (len >= PROP_VALUE_MAX) {
        len = PROP_VALUE_MAX - 1;
    }
    memcpy(value, answer, len);
    value[len] = '\0';
    return (int)len;
}

int a2i_gettid(void) {
    uint64_t tid = 0;
    if (pthread_threadid_np(NULL, &tid) != 0) {
        return (int)getpid();
    }
    return (int)(tid & 0x7fffffffU);
}

void *a2i_memalign(size_t alignment, size_t size) {
    void *ptr = NULL;
    int rc = posix_memalign(&ptr, alignment, size);
    if (rc != 0) {
        errno = rc;
        return NULL;
    }
    return ptr;
}

void a2i___google_potentially_blocking_region_begin(void) {}
void a2i___google_potentially_blocking_region_end(void) {}
"""


OBJECTS_ASM = r""".data
.p2align 4

// Link-only placeholders. These are NOT semantic Bionic implementations.

.globl _a2i___sF
_a2i___sF:
    .zero 4096

.globl _a2i__ctype_
_a2i__ctype_:
    .zero 4096
"""


BUILD_SH = r"""#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
OUT="$ROOT/build"
mkdir -p "$OUT"

SDK="$(xcrun --sdk iphoneos --show-sdk-path)"

xcrun --sdk iphoneos clang \
  -arch arm64 \
  -isysroot "$SDK" \
  -miphoneos-version-min=12.0 \
  -dynamiclib \
  -install_name @rpath/libbionic_shim.dylib \
  "$ROOT/direct.S" \
  "$ROOT/trap.S" \
  "$ROOT/objects.S" \
  "$ROOT/special.c" \
  -o "$OUT/libbionic_shim.dylib"

echo "$OUT/libbionic_shim.dylib"
"""


README_TEMPLATE = """# Generated Bionic to Darwin shim scaffold

Categories:

- direct: conservative ARM64 tail calls to the Darwin symbol with the same C name.
- special: hand-written adapters in special.c.
- object: exported placeholder storage only; semantics are not implemented.
- trap: fail-fast stubs that print the symbol if reached.

Build on macOS with Xcode by running: sh build.sh

This is a link/runtime-probing scaffold, not a complete compatibility layer.
"""


def generate_shim_scaffold(
    input_elf: str | Path,
    output_dir: str | Path,
) -> dict:
    elf = Elf64.from_path(input_elf)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    imports = {}
    for symbol in elf.dynamic_symbols():
        if symbol["shndx"] != SHN_UNDEF or not symbol["name"]:
            continue
        name = symbol["name"]
        imports[name] = {
            "name": name,
            "elf_type": TYPE_NAMES.get(
                symbol["info"] & 0x0F,
                f"type_{symbol['info'] & 0x0F}",
            ),
        }

    records = []
    for name in sorted(imports):
        elf_type = imports[name]["elf_type"]
        if name in OBJECT_SYMBOLS or elf_type == "object":
            category = "object"
        elif name in SPECIAL_SYMBOLS:
            category = "special"
        elif name in DIRECT_SYMBOLS and elf_type in {"func", "notype"}:
            category = "direct"
        else:
            category = "trap"

        records.append({
            "name": name,
            "shim_symbol": "_a2i_" + name,
            "elf_type": elf_type,
            "category": category,
        })

    direct = [x["name"] for x in records if x["category"] == "direct"]
    trap = [
        x["name"] for x in records
        if x["category"] == "trap" and x["elf_type"] != "object"
    ]

    (output_dir / "direct.S").write_text(
        _direct_assembly(direct), encoding="utf-8"
    )
    (output_dir / "trap.S").write_text(
        _trap_assembly(trap), encoding="utf-8"
    )
    (output_dir / "objects.S").write_text(
        OBJECTS_ASM, encoding="utf-8"
    )
    (output_dir / "special.c").write_text(
        SPECIAL_C, encoding="utf-8"
    )
    (output_dir / "build.sh").write_text(
        BUILD_SH, encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        README_TEMPLATE, encoding="utf-8"
    )

    counts = Counter(x["category"] for x in records)
    manifest = {
        "source": str(input_elf),
        "total_imports": len(records),
        "counts": dict(sorted(counts.items())),
        "imports": records,
        "warning": (
            "direct means suitable for the first ABI probe, not guaranteed "
            "semantic compatibility. object and trap entries remain blockers "
            "if execution reaches them."
        ),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest
