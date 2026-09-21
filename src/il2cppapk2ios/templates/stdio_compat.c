#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

extern unsigned char a2i___sF[152 * 3];

static FILE *a2i_host_stream(void *stream) {
    if (stream == NULL) {
        return NULL;
    }

    uintptr_t p = (uintptr_t)stream;
    uintptr_t base = (uintptr_t)&a2i___sF[0];

    if (p == base) return stdin;
    if (p == base + 152) return stdout;
    if (p == base + 304) return stderr;

    // fopen() is currently a direct Darwin call, so ordinary returned FILE*
    // values are already native and can pass through unchanged.
    return (FILE *)stream;
}

int a2i_fclose(void *stream) {
    return fclose(a2i_host_stream(stream));
}

int a2i_fileno(void *stream) {
    return fileno(a2i_host_stream(stream));
}

int a2i_fputc(int ch, void *stream) {
    return fputc(ch, a2i_host_stream(stream));
}

int a2i_fputs(const char *text, void *stream) {
    return fputs(text, a2i_host_stream(stream));
}

int a2i_fscanf(void *stream, const char *format, ...) {
    va_list ap;
    va_start(ap, format);
    int rc = vfscanf(a2i_host_stream(stream), format, ap);
    va_end(ap);
    return rc;
}

size_t a2i_fwrite(
    const void *ptr,
    size_t size,
    size_t count,
    void *stream
) {
    return fwrite(ptr, size, count, a2i_host_stream(stream));
}
