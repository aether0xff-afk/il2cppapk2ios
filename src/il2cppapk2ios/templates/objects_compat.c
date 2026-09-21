#include <stddef.h>
#include <stdint.h>

enum {
    A2I_CTYPE_U = 0x01,
    A2I_CTYPE_L = 0x02,
    A2I_CTYPE_D = 0x04,
    A2I_CTYPE_S = 0x08,
    A2I_CTYPE_P = 0x10,
    A2I_CTYPE_C = 0x20,
    A2I_CTYPE_X = 0x40,
    A2I_CTYPE_B = 0x80,
};

static unsigned char g_a2i_ctype[257];

// Bionic exposes an object symbol whose value is a pointer to a table with one
// leading sentinel byte. The target loads the object, dereferences it, then +1.
const char *a2i__ctype_ = (const char *)g_a2i_ctype;

// NDK r17 LP64 struct __sFILE is opaque 152-byte storage. The target references
// only __sF[2] (stderr), but keep all three legacy slots for compatibility.
unsigned char a2i___sF[152 * 3] __attribute__((aligned(8)));

static unsigned char a2i_ctype_flags(unsigned c) {
    unsigned char f = 0;

    if (c < 0x20 || c == 0x7f) f |= A2I_CTYPE_C;
    if (c == ' ' || (c >= '\t' && c <= '\r')) f |= A2I_CTYPE_S;
    if (c == ' ') f |= A2I_CTYPE_B;

    if (c >= '0' && c <= '9') f |= A2I_CTYPE_D;
    if (c >= 'A' && c <= 'Z') f |= A2I_CTYPE_U;
    if (c >= 'a' && c <= 'z') f |= A2I_CTYPE_L;

    if (
        (c >= '0' && c <= '9') ||
        (c >= 'A' && c <= 'F') ||
        (c >= 'a' && c <= 'f')
    ) {
        f |= A2I_CTYPE_X;
    }

    if (
        (c >= 0x21 && c <= 0x2f) ||
        (c >= 0x3a && c <= 0x40) ||
        (c >= 0x5b && c <= 0x60) ||
        (c >= 0x7b && c <= 0x7e)
    ) {
        f |= A2I_CTYPE_P;
    }

    return f;
}

__attribute__((constructor))
static void a2i_init_ctype_table(void) {
    g_a2i_ctype[0] = 0;
    for (unsigned c = 0; c < 256; ++c) {
        g_a2i_ctype[c + 1] = a2i_ctype_flags(c);
    }
}
