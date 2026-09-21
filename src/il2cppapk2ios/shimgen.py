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
    "btowc", "clock", "closedir", "dladdr", "dlclose", "fclose", "fileno",
    "fopen", "fputc", "fputs", "fscanf", "ftruncate", "fwrite", "gmtime",
    "inet_ntop", "inet_pton", "iswctype", "localtime", "mbrtowc", "mkdir",
    "mktime", "mprotect", "munmap", "opendir", "send", "setlocale",
    "shutdown", "sprintf", "strerror", "strftime", "sysconf", "vsnprintf",
    "vsprintf", "wcrtomb", "wcscoll", "wcsftime", "wcsxfrm", "wctob",
    "wctype", "writev", "poll",
}

SPECIAL_SYMBOLS = {
    "__errno",
    "__google_potentially_blocking_region_begin",
    "__google_potentially_blocking_region_end",
    "__system_property_get",
    "clock_getres",
    "clock_gettime",
    "gettid",
    "memalign",
}

POSIX_SYMBOLS = {
    "__ctype_get_mb_cur_max",
    "fstat",
    "lstat",
    "mmap",
    "open",
    "readdir",
    "sem_getvalue",
    "sem_init",
    "sem_post",
    "sem_wait",
    "stat",
    "uname",
}

ELF_SIGNAL_SYMBOLS = {
    "sigaction",
    "sigdelset",
    "sigfillset",
    "signal",
    "sigsuspend",
    "tgkill",
}

ELF_RUNTIME_SYMBOLS = {
    "dl_iterate_phdr",
}

RUNTIME_SYMBOLS = {
    "setjmp",
    "syscall",
}

NETWORK_SYMBOLS = {
    "connect",
    "freeaddrinfo",
    "getaddrinfo",
    "getnameinfo",
    "getsockopt",
    "ioctl",
    "recvfrom",
    "setsockopt",
    "socket",
}

PTHREAD_SYMBOLS = {
    "pthread_attr_destroy",
    "pthread_attr_getstack",
    "pthread_attr_init",
    "pthread_cond_broadcast",
    "pthread_cond_destroy",
    "pthread_cond_init",
    "pthread_cond_signal",
    "pthread_cond_timedwait",
    "pthread_cond_wait",
    "pthread_condattr_destroy",
    "pthread_condattr_init",
    "pthread_condattr_setclock",
    "pthread_create",
    "pthread_detach",
    "pthread_getattr_np",
    "pthread_getspecific",
    "pthread_key_create",
    "pthread_key_delete",
    "pthread_mutex_destroy",
    "pthread_mutex_init",
    "pthread_mutex_lock",
    "pthread_mutex_unlock",
    "pthread_mutexattr_destroy",
    "pthread_mutexattr_init",
    "pthread_mutexattr_settype",
    "pthread_once",
    "pthread_self",
    "pthread_setspecific",
}

STDIO_SYMBOLS = {
    "fclose",
    "fileno",
    "fputc",
    "fputs",
    "fscanf",
    "fwrite",
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
#include <time.h>
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

static int a2i_host_clock(int android_clock) {
    switch (android_clock) {
        case 0:
            return CLOCK_REALTIME;
        case 1:
            return CLOCK_MONOTONIC;
#ifdef CLOCK_PROCESS_CPUTIME_ID
        case 2:
            return CLOCK_PROCESS_CPUTIME_ID;
#endif
#ifdef CLOCK_THREAD_CPUTIME_ID
        case 3:
            return CLOCK_THREAD_CPUTIME_ID;
#endif
#ifdef CLOCK_MONOTONIC_RAW
        case 4:
            return CLOCK_MONOTONIC_RAW;
#endif
        case 5:
            return CLOCK_REALTIME;
        case 6:
            return CLOCK_MONOTONIC;
#ifdef CLOCK_UPTIME_RAW
        case 7:
            return CLOCK_UPTIME_RAW;
#endif
        default:
            return -1;
    }
}

int a2i_clock_gettime(int android_clock, struct timespec *value) {
    int host_clock = a2i_host_clock(android_clock);
    if (host_clock < 0) {
        errno = EINVAL;
        return -1;
    }
    return clock_gettime((clockid_t)host_clock, value);
}

int a2i_clock_getres(int android_clock, struct timespec *value) {
    int host_clock = a2i_host_clock(android_clock);
    if (host_clock < 0) {
        errno = EINVAL;
        return -1;
    }
    return clock_getres((clockid_t)host_clock, value);
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


OBJECTS_ASM = r"""// Object shims are implemented in objects_compat.c.
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
  "$ROOT/objects_compat.c" \
  "$ROOT/stdio_compat.c" \
  "$ROOT/special.c" \
  "$ROOT/pthread_compat.c" \
  "$ROOT/posix_compat.c" \
  "$ROOT/elf_phdr_compat.c" \
  "$ROOT/network_compat.c" \
  "$ROOT/runtime_compat.c" \
  "$ROOT/runtime_compat.S" \
  "$ROOT/signal_compat.c" \
  -o "$OUT/libbionic_shim.dylib"

echo "$OUT/libbionic_shim.dylib"
"""


README_TEMPLATE = """# Generated Bionic to Darwin shim scaffold

Categories:

- direct: conservative ARM64 tail calls to the Darwin symbol with the same C name.
- special: hand-written adapters in special.c.
- object: Bionic _ctype_ table pointer and legacy __sF[3] storage.
- stdio: maps legacy __sF slots to Darwin stdin/stdout/stderr.
- pthread: Android-sized pthread objects mapped to native Darwin pthread objects.
- posix: translated Android stat/open/mmap/dirent/semaphore/uname ABI.
- elf-runtime: synthetic ELF program-header view for the translated image.
- network: Linux/Android socket-address, option, resolver, and ioctl translation.
- runtime: target-specific AArch64 setjmp spill and Linux futex syscall emulation.
- signal: Android signal numbers, sigset, and sigaction translation.
- trap: fail-fast stubs that print the symbol if reached.

Build on macOS with Xcode by running: sh build.sh

This is a link/runtime-probing scaffold, not a complete compatibility layer.
"""



def _elf_phdr_compat_source(elf: Elf64) -> str:
    entries = []
    for ph in elf.program_headers:
        entries.append(
            "    {"
            f"{ph.p_type}u, {ph.flags}u, "
            f"0x{ph.offset:x}ULL, 0x{ph.vaddr:x}ULL, 0x{ph.vaddr:x}ULL, "
            f"0x{ph.filesz:x}ULL, 0x{ph.memsz:x}ULL, 0x{ph.align:x}ULL"
            "},"
        )

    phdrs = "\n".join(entries)
    return f"""#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef struct {{
    uint32_t p_type;
    uint32_t p_flags;
    uint64_t p_offset;
    uint64_t p_vaddr;
    uint64_t p_paddr;
    uint64_t p_filesz;
    uint64_t p_memsz;
    uint64_t p_align;
}} a2i_elf64_phdr;

typedef struct {{
    uintptr_t dlpi_addr;
    const char *dlpi_name;
    const a2i_elf64_phdr *dlpi_phdr;
    uint16_t dlpi_phnum;
    uint16_t _pad0;
    uint32_t _pad1;
    uint64_t dlpi_adds;
    uint64_t dlpi_subs;
    size_t dlpi_tls_modid;
    void *dlpi_tls_data;
}} a2i_dl_phdr_info;

static const a2i_elf64_phdr k_original_phdrs[] = {{
{phdrs}
}};

int a2i_dl_iterate_phdr(
    int (*callback)(a2i_dl_phdr_info *, size_t, void *),
    void *data
) {{
    if (callback == NULL) {{
        return 0;
    }}

    Dl_info image = {{0}};
    void *caller = __builtin_return_address(0);
    if (dladdr(caller, &image) == 0 || image.dli_fbase == NULL) {{
        return 0;
    }}

    a2i_dl_phdr_info info;
    memset(&info, 0, sizeof(info));
    info.dlpi_addr = (uintptr_t)image.dli_fbase + 0x10000ULL;
    info.dlpi_name = image.dli_fname ? image.dli_fname : "";
    info.dlpi_phdr = k_original_phdrs;
    info.dlpi_phnum = (uint16_t)(
        sizeof(k_original_phdrs) / sizeof(k_original_phdrs[0])
    );

    return callback(&info, sizeof(info), data);
}}
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
        elif name in STDIO_SYMBOLS:
            category = "stdio"
        elif name in SPECIAL_SYMBOLS:
            category = "special"
        elif name in PTHREAD_SYMBOLS:
            category = "pthread"
        elif name in POSIX_SYMBOLS:
            category = "posix"
        elif name in ELF_RUNTIME_SYMBOLS:
            category = "elf-runtime"
        elif name in NETWORK_SYMBOLS:
            category = "network"
        elif name in RUNTIME_SYMBOLS:
            category = "runtime"
        elif name in SIGNAL_SYMBOLS:
            category = "signal"
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
    objects_template = (
        Path(__file__).with_name("templates") / "objects_compat.c"
    )
    stdio_template = (
        Path(__file__).with_name("templates") / "stdio_compat.c"
    )
    (output_dir / "objects_compat.c").write_text(
        objects_template.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (output_dir / "stdio_compat.c").write_text(
        stdio_template.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (output_dir / "special.c").write_text(
        SPECIAL_C, encoding="utf-8"
    )
    template = (
        Path(__file__).with_name("templates") / "pthread_compat.c"
    )
    (output_dir / "pthread_compat.c").write_text(
        template.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    posix_template = (
        Path(__file__).with_name("templates") / "posix_compat.c"
    )
    (output_dir / "posix_compat.c").write_text(
        posix_template.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (output_dir / "elf_phdr_compat.c").write_text(
        _elf_phdr_compat_source(elf),
        encoding="utf-8",
    )
    network_template = (
        Path(__file__).with_name("templates") / "network_compat.c"
    )
    (output_dir / "network_compat.c").write_text(
        network_template.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runtime_c = (
        Path(__file__).with_name("templates") / "runtime_compat.c"
    )
    runtime_s = (
        Path(__file__).with_name("templates") / "runtime_compat.S"
    )
    (output_dir / "runtime_compat.c").write_text(
        runtime_c.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (output_dir / "runtime_compat.S").write_text(
        runtime_s.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    signal_template = (
        Path(__file__).with_name("templates") / "signal_compat.c"
    )
    (output_dir / "signal_compat.c").write_text(
        signal_template.read_text(encoding="utf-8"),
        encoding="utf-8",
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
