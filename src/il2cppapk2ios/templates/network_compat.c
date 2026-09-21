#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

enum {
    A2I_AF_UNSPEC = 0,
    A2I_AF_UNIX = 1,
    A2I_AF_INET = 2,
    A2I_AF_INET6 = 10,

    A2I_SOCK_STREAM = 1,
    A2I_SOCK_DGRAM = 2,
    A2I_SOCK_RAW = 3,
    A2I_SOCK_RDM = 4,
    A2I_SOCK_SEQPACKET = 5,
    A2I_SOCK_NONBLOCK = 00004000,
    A2I_SOCK_CLOEXEC = 02000000,

    A2I_SOL_SOCKET = 1,
    A2I_SO_REUSEADDR = 2,
    A2I_SO_ERROR = 4,
    A2I_SO_BROADCAST = 6,
    A2I_SO_SNDBUF = 7,
    A2I_SO_RCVBUF = 8,
    A2I_SO_KEEPALIVE = 9,
    A2I_SO_LINGER = 13,
    A2I_SO_RCVTIMEO = 20,
    A2I_SO_SNDTIMEO = 21,

    A2I_FIONREAD = 0x541B,
    A2I_FIONBIO = 0x5421,
};

typedef struct {
    uint16_t sa_family;
    char sa_data[14];
} a2i_sockaddr;

typedef struct a2i_addrinfo {
    int ai_flags;
    int ai_family;
    int ai_socktype;
    int ai_protocol;
    uint32_t ai_addrlen;
    char *ai_canonname;
    a2i_sockaddr *ai_addr;
    struct a2i_addrinfo *ai_next;
} a2i_addrinfo;

static int a2i_host_family(int family) {
    switch (family) {
        case A2I_AF_UNSPEC: return AF_UNSPEC;
        case A2I_AF_UNIX: return AF_UNIX;
        case A2I_AF_INET: return AF_INET;
        case A2I_AF_INET6: return AF_INET6;
        default: return -1;
    }
}

static int a2i_android_family(int family) {
    switch (family) {
        case AF_UNSPEC: return A2I_AF_UNSPEC;
        case AF_UNIX: return A2I_AF_UNIX;
        case AF_INET: return A2I_AF_INET;
        case AF_INET6: return A2I_AF_INET6;
        default: return -1;
    }
}

static int a2i_host_socktype(int type, int *extra_flags) {
    int base = type & 0xf;
    int out = 0;
    switch (base) {
        case A2I_SOCK_STREAM: out = SOCK_STREAM; break;
        case A2I_SOCK_DGRAM: out = SOCK_DGRAM; break;
        case A2I_SOCK_RAW: out = SOCK_RAW; break;
#ifdef SOCK_RDM
        case A2I_SOCK_RDM: out = SOCK_RDM; break;
#endif
        case A2I_SOCK_SEQPACKET: out = SOCK_SEQPACKET; break;
        default:
            errno = EPROTOTYPE;
            return -1;
    }
    if (extra_flags != NULL) {
        *extra_flags = type & (A2I_SOCK_NONBLOCK | A2I_SOCK_CLOEXEC);
    }
    return out;
}

static socklen_t a2i_to_host_sockaddr(
    const void *android_addr,
    uint32_t android_len,
    struct sockaddr_storage *host
) {
    if (android_addr == NULL || host == NULL || android_len < 2) {
        errno = EINVAL;
        return 0;
    }

    const uint8_t *src = (const uint8_t *)android_addr;
    uint16_t family = 0;
    memcpy(&family, src, sizeof(family));
    memset(host, 0, sizeof(*host));

    if (family == A2I_AF_INET && android_len >= 16) {
        struct sockaddr_in *dst = (struct sockaddr_in *)host;
        dst->sin_len = sizeof(*dst);
        dst->sin_family = AF_INET;
        memcpy(&dst->sin_port, src + 2, 2);
        memcpy(&dst->sin_addr, src + 4, 4);
        return sizeof(*dst);
    }

    if (family == A2I_AF_INET6 && android_len >= 28) {
        struct sockaddr_in6 *dst = (struct sockaddr_in6 *)host;
        dst->sin6_len = sizeof(*dst);
        dst->sin6_family = AF_INET6;
        memcpy(&dst->sin6_port, src + 2, 2);
        memcpy(&dst->sin6_flowinfo, src + 4, 4);
        memcpy(&dst->sin6_addr, src + 8, 16);
        memcpy(&dst->sin6_scope_id, src + 24, 4);
        return sizeof(*dst);
    }

    errno = EAFNOSUPPORT;
    return 0;
}

static uint32_t a2i_from_host_sockaddr(
    const struct sockaddr *host,
    void *android_addr,
    uint32_t capacity
) {
    if (host == NULL || android_addr == NULL) {
        return 0;
    }

    uint8_t *dst = (uint8_t *)android_addr;
    if (host->sa_family == AF_INET) {
        if (capacity < 16) return 16;
        const struct sockaddr_in *src = (const struct sockaddr_in *)host;
        uint16_t family = A2I_AF_INET;
        memset(dst, 0, 16);
        memcpy(dst, &family, 2);
        memcpy(dst + 2, &src->sin_port, 2);
        memcpy(dst + 4, &src->sin_addr, 4);
        return 16;
    }

    if (host->sa_family == AF_INET6) {
        if (capacity < 28) return 28;
        const struct sockaddr_in6 *src = (const struct sockaddr_in6 *)host;
        uint16_t family = A2I_AF_INET6;
        memset(dst, 0, 28);
        memcpy(dst, &family, 2);
        memcpy(dst + 2, &src->sin6_port, 2);
        memcpy(dst + 4, &src->sin6_flowinfo, 4);
        memcpy(dst + 8, &src->sin6_addr, 16);
        memcpy(dst + 24, &src->sin6_scope_id, 4);
        return 28;
    }

    return 0;
}

int a2i_socket(int family, int type, int protocol) {
    int host_family = a2i_host_family(family);
    int extra = 0;
    int host_type = a2i_host_socktype(type, &extra);
    if (host_family < 0 || host_type < 0) {
        errno = EAFNOSUPPORT;
        return -1;
    }

    int fd = socket(host_family, host_type, protocol);
    if (fd < 0) {
        return fd;
    }

    if (extra & A2I_SOCK_NONBLOCK) {
        int flags = fcntl(fd, F_GETFL, 0);
        if (flags >= 0) fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }
#ifdef FD_CLOEXEC
    if (extra & A2I_SOCK_CLOEXEC) {
        int flags = fcntl(fd, F_GETFD, 0);
        if (flags >= 0) fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
    }
#endif
    return fd;
}

int a2i_connect(int fd, const void *address, uint32_t length) {
    struct sockaddr_storage host;
    socklen_t host_len = a2i_to_host_sockaddr(address, length, &host);
    if (host_len == 0) return -1;
    return connect(fd, (struct sockaddr *)&host, host_len);
}

ssize_t a2i_recvfrom(
    int fd,
    void *buffer,
    size_t length,
    int flags,
    void *address,
    uint32_t *address_length
) {
    if (address == NULL || address_length == NULL) {
        return recvfrom(fd, buffer, length, flags, NULL, NULL);
    }

    struct sockaddr_storage host;
    socklen_t host_len = sizeof(host);
    ssize_t rc = recvfrom(
        fd, buffer, length, flags,
        (struct sockaddr *)&host, &host_len
    );
    if (rc >= 0) {
        uint32_t needed = a2i_from_host_sockaddr(
            (struct sockaddr *)&host,
            address,
            *address_length
        );
        *address_length = needed;
    }
    return rc;
}

static int a2i_host_sockopt(int level, int option, int *host_level) {
    if (level != A2I_SOL_SOCKET) {
        *host_level = level;
        return option;
    }

    *host_level = SOL_SOCKET;
    switch (option) {
        case A2I_SO_REUSEADDR: return SO_REUSEADDR;
        case A2I_SO_ERROR: return SO_ERROR;
        case A2I_SO_BROADCAST: return SO_BROADCAST;
        case A2I_SO_SNDBUF: return SO_SNDBUF;
        case A2I_SO_RCVBUF: return SO_RCVBUF;
        case A2I_SO_KEEPALIVE: return SO_KEEPALIVE;
        case A2I_SO_LINGER: return SO_LINGER;
        case A2I_SO_RCVTIMEO: return SO_RCVTIMEO;
        case A2I_SO_SNDTIMEO: return SO_SNDTIMEO;
        default:
            errno = ENOPROTOOPT;
            return -1;
    }
}

int a2i_getsockopt(
    int fd,
    int level,
    int option,
    void *value,
    uint32_t *length
) {
    if (length == NULL) {
        errno = EFAULT;
        return -1;
    }
    int host_level = 0;
    int host_option = a2i_host_sockopt(level, option, &host_level);
    if (host_option < 0) return -1;
    socklen_t host_len = (socklen_t)*length;
    int rc = getsockopt(fd, host_level, host_option, value, &host_len);
    *length = (uint32_t)host_len;
    return rc;
}

int a2i_setsockopt(
    int fd,
    int level,
    int option,
    const void *value,
    uint32_t length
) {
    int host_level = 0;
    int host_option = a2i_host_sockopt(level, option, &host_level);
    if (host_option < 0) return -1;
    return setsockopt(
        fd, host_level, host_option, value, (socklen_t)length
    );
}

int a2i_ioctl(int fd, unsigned long request, ...) {
    void *arg = NULL;
    va_list ap;
    va_start(ap, request);
    arg = va_arg(ap, void *);
    va_end(ap);

    unsigned long host_request;
    switch (request) {
        case A2I_FIONREAD: host_request = FIONREAD; break;
        case A2I_FIONBIO: host_request = FIONBIO; break;
        default:
            errno = ENOTTY;
            return -1;
    }
    return ioctl(fd, host_request, arg);
}

static int a2i_host_ai_flags(int flags) {
    int out = 0;
    if (flags & 0x00000001) out |= AI_PASSIVE;
    if (flags & 0x00000002) out |= AI_CANONNAME;
    if (flags & 0x00000004) out |= AI_NUMERICHOST;
#ifdef AI_NUMERICSERV
    if (flags & 0x00000008) out |= AI_NUMERICSERV;
#endif
#ifdef AI_ALL
    if (flags & 0x00000100) out |= AI_ALL;
#endif
#ifdef AI_ADDRCONFIG
    if (flags & 0x00000400) out |= AI_ADDRCONFIG;
#endif
#ifdef AI_V4MAPPED
    if (flags & 0x00000800) out |= AI_V4MAPPED;
#endif
    return out;
}

static int a2i_android_eai(int host_error) {
    if (host_error == 0) return 0;
    switch (host_error) {
#ifdef EAI_AGAIN
        case EAI_AGAIN: return 2;
#endif
#ifdef EAI_BADFLAGS
        case EAI_BADFLAGS: return 3;
#endif
#ifdef EAI_FAIL
        case EAI_FAIL: return 4;
#endif
#ifdef EAI_FAMILY
        case EAI_FAMILY: return 5;
#endif
#ifdef EAI_MEMORY
        case EAI_MEMORY: return 6;
#endif
#ifdef EAI_NONAME
        case EAI_NONAME: return 8;
#endif
#ifdef EAI_SERVICE
        case EAI_SERVICE: return 9;
#endif
#ifdef EAI_SOCKTYPE
        case EAI_SOCKTYPE: return 10;
#endif
#ifdef EAI_SYSTEM
        case EAI_SYSTEM: return 11;
#endif
#ifdef EAI_OVERFLOW
        case EAI_OVERFLOW: return 14;
#endif
        default: return 4;
    }
}

void a2i_freeaddrinfo(a2i_addrinfo *item);

int a2i_getaddrinfo(
    const char *node,
    const char *service,
    const a2i_addrinfo *hints,
    a2i_addrinfo **result
) {
    if (result == NULL) return 3;
    *result = NULL;

    struct addrinfo host_hints;
    struct addrinfo *host_hints_ptr = NULL;
    memset(&host_hints, 0, sizeof(host_hints));
    if (hints != NULL) {
        host_hints.ai_flags = a2i_host_ai_flags(hints->ai_flags);
        host_hints.ai_family = a2i_host_family(hints->ai_family);
        int ignored = 0;
        host_hints.ai_socktype = hints->ai_socktype
            ? a2i_host_socktype(hints->ai_socktype, &ignored)
            : 0;
        host_hints.ai_protocol = hints->ai_protocol;
        if (host_hints.ai_family < 0 || host_hints.ai_socktype < 0) {
            return 5;
        }
        host_hints_ptr = &host_hints;
    }

    struct addrinfo *host_result = NULL;
    int rc = getaddrinfo(node, service, host_hints_ptr, &host_result);
    if (rc != 0) return a2i_android_eai(rc);

    a2i_addrinfo *head = NULL;
    a2i_addrinfo **tail = &head;

    for (struct addrinfo *it = host_result; it != NULL; it = it->ai_next) {
        int family = a2i_android_family(it->ai_family);
        if (family < 0) continue;

        a2i_addrinfo *item = calloc(1, sizeof(*item));
        if (item == NULL) {
            a2i_freeaddrinfo(head);
            freeaddrinfo(host_result);
            return 6;
        }

        uint8_t converted[128] = {0};
        uint32_t address_len = a2i_from_host_sockaddr(
            it->ai_addr, converted, sizeof(converted)
        );
        if (address_len == 0) {
            free(item);
            continue;
        }

        item->ai_addr = malloc(address_len);
        if (item->ai_addr == NULL) {
            free(item);
            a2i_freeaddrinfo(head);
            freeaddrinfo(host_result);
            return 6;
        }
        memcpy(item->ai_addr, converted, address_len);
        item->ai_addrlen = address_len;
        item->ai_family = family;
        item->ai_socktype = it->ai_socktype;
        item->ai_protocol = it->ai_protocol;
        item->ai_flags = hints ? hints->ai_flags : 0;
        if (it->ai_canonname != NULL) {
            item->ai_canonname = strdup(it->ai_canonname);
        }

        *tail = item;
        tail = &item->ai_next;
    }

    freeaddrinfo(host_result);
    *result = head;
    return head ? 0 : 8;
}

void a2i_freeaddrinfo(a2i_addrinfo *item) {
    while (item != NULL) {
        a2i_addrinfo *next = item->ai_next;
        free(item->ai_canonname);
        free(item->ai_addr);
        free(item);
        item = next;
    }
}

int a2i_getnameinfo(
    const void *address,
    uint32_t address_length,
    char *host,
    size_t host_length,
    char *service,
    size_t service_length,
    int flags
) {
    struct sockaddr_storage converted;
    socklen_t converted_len = a2i_to_host_sockaddr(
        address, address_length, &converted
    );
    if (converted_len == 0) return 5;
    int rc = getnameinfo(
        (struct sockaddr *)&converted,
        converted_len,
        host,
        (socklen_t)host_length,
        service,
        (socklen_t)service_length,
        flags
    );
    return a2i_android_eai(rc);
}
