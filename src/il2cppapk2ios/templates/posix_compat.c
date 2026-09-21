#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <locale.h>
#include <os/lock.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <wchar.h>

typedef struct {
    uint64_t st_dev;
    uint64_t st_ino;
    uint32_t st_mode;
    uint32_t st_nlink;
    uint32_t st_uid;
    uint32_t st_gid;
    uint64_t st_rdev;
    uint64_t __pad1;
    int64_t st_size;
    int32_t st_blksize;
    int32_t __pad2;
    int64_t st_blocks;
    struct timespec st_atim;
    struct timespec st_mtim;
    struct timespec st_ctim;
    uint32_t __unused4;
    uint32_t __unused5;
} a2i_android_stat;

_Static_assert(sizeof(a2i_android_stat) == 128, "Android AArch64 struct stat must be 128 bytes");

static void a2i_copy_stat(a2i_android_stat *out, const struct stat *in) {
    memset(out, 0, sizeof(*out));
    out->st_dev = (uint64_t)in->st_dev;
    out->st_ino = (uint64_t)in->st_ino;
    out->st_mode = (uint32_t)in->st_mode;
    out->st_nlink = (uint32_t)in->st_nlink;
    out->st_uid = (uint32_t)in->st_uid;
    out->st_gid = (uint32_t)in->st_gid;
    out->st_rdev = (uint64_t)in->st_rdev;
    out->st_size = (int64_t)in->st_size;
    out->st_blksize = (int32_t)in->st_blksize;
    out->st_blocks = (int64_t)in->st_blocks;
    out->st_atim.tv_sec = in->st_atimespec.tv_sec;
    out->st_atim.tv_nsec = in->st_atimespec.tv_nsec;
    out->st_mtim.tv_sec = in->st_mtimespec.tv_sec;
    out->st_mtim.tv_nsec = in->st_mtimespec.tv_nsec;
    out->st_ctim.tv_sec = in->st_ctimespec.tv_sec;
    out->st_ctim.tv_nsec = in->st_ctimespec.tv_nsec;
}

int a2i_stat(const char *path, a2i_android_stat *out) {
    struct stat value;
    int rc = stat(path, &value);
    if (rc == 0 && out != NULL) {
        a2i_copy_stat(out, &value);
    }
    return rc;
}

int a2i_lstat(const char *path, a2i_android_stat *out) {
    struct stat value;
    int rc = lstat(path, &value);
    if (rc == 0 && out != NULL) {
        a2i_copy_stat(out, &value);
    }
    return rc;
}

int a2i_fstat(int fd, a2i_android_stat *out) {
    struct stat value;
    int rc = fstat(fd, &value);
    if (rc == 0 && out != NULL) {
        a2i_copy_stat(out, &value);
    }
    return rc;
}

enum {
    A2I_O_ACCMODE = 00000003,
    A2I_O_CREAT = 00000100,
    A2I_O_EXCL = 00000200,
    A2I_O_NOCTTY = 00000400,
    A2I_O_TRUNC = 00001000,
    A2I_O_APPEND = 00002000,
    A2I_O_NONBLOCK = 00004000,
    A2I_O_DSYNC = 00010000,
    A2I_O_DIRECT = 00040000,
    A2I_O_LARGEFILE = 00100000,
    A2I_O_DIRECTORY = 00200000,
    A2I_O_NOFOLLOW = 00400000,
    A2I_O_CLOEXEC = 02000000,
    A2I_O_SYNC = 04010000,
};

static int a2i_open_flags(int flags) {
    int out = 0;
    switch (flags & A2I_O_ACCMODE) {
        case 0: out |= O_RDONLY; break;
        case 1: out |= O_WRONLY; break;
        case 2: out |= O_RDWR; break;
        default:
            errno = EINVAL;
            return -1;
    }

    if (flags & A2I_O_CREAT) out |= O_CREAT;
    if (flags & A2I_O_EXCL) out |= O_EXCL;
    if (flags & A2I_O_TRUNC) out |= O_TRUNC;
    if (flags & A2I_O_APPEND) out |= O_APPEND;
    if (flags & A2I_O_NONBLOCK) out |= O_NONBLOCK;
#ifdef O_NOCTTY
    if (flags & A2I_O_NOCTTY) out |= O_NOCTTY;
#endif
#ifdef O_DIRECTORY
    if (flags & A2I_O_DIRECTORY) out |= O_DIRECTORY;
#endif
#ifdef O_NOFOLLOW
    if (flags & A2I_O_NOFOLLOW) out |= O_NOFOLLOW;
#endif
#ifdef O_CLOEXEC
    if (flags & A2I_O_CLOEXEC) out |= O_CLOEXEC;
#endif
#ifdef O_DSYNC
    if (flags & A2I_O_DSYNC) out |= O_DSYNC;
#endif
#ifdef O_SYNC
    if (flags & A2I_O_SYNC) out |= O_SYNC;
#endif
    // O_LARGEFILE is a no-op on LP64. O_DIRECT has no portable iOS equivalent.
    return out;
}

int a2i_open(const char *path, int flags, ...) {
    int host_flags = a2i_open_flags(flags);
    if (host_flags < 0) {
        return -1;
    }

    if (flags & A2I_O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        int mode = va_arg(ap, int);
        va_end(ap);
        return open(path, host_flags, (mode_t)mode);
    }
    return open(path, host_flags);
}

enum {
    A2I_MAP_SHARED = 0x01,
    A2I_MAP_PRIVATE = 0x02,
    A2I_MAP_FIXED = 0x10,
    A2I_MAP_ANONYMOUS = 0x20,
    A2I_MAP_NORESERVE = 0x4000,
    A2I_MAP_POPULATE = 0x8000,
};

void *a2i_mmap(
    void *addr,
    size_t length,
    int prot,
    int flags,
    int fd,
    off_t offset
) {
    int host_flags = 0;
    if (flags & A2I_MAP_SHARED) host_flags |= MAP_SHARED;
    if (flags & A2I_MAP_PRIVATE) host_flags |= MAP_PRIVATE;
    if (flags & A2I_MAP_FIXED) host_flags |= MAP_FIXED;
    if (flags & A2I_MAP_ANONYMOUS) host_flags |= MAP_ANON;
    // NORESERVE/POPULATE are performance hints here and are intentionally ignored.
    return mmap(addr, length, prot, host_flags, fd, offset);
}

typedef struct {
    uint64_t d_ino;
    int64_t d_off;
    uint16_t d_reclen;
    uint8_t d_type;
    char d_name[256];
} a2i_android_dirent;

static _Thread_local a2i_android_dirent g_dirent;

a2i_android_dirent *a2i_readdir(DIR *dir) {
    struct dirent *entry = readdir(dir);
    if (entry == NULL) {
        return NULL;
    }

    memset(&g_dirent, 0, sizeof(g_dirent));
    g_dirent.d_ino = (uint64_t)entry->d_ino;
    g_dirent.d_type = (uint8_t)entry->d_type;
    size_t name_len = strnlen(entry->d_name, sizeof(g_dirent.d_name) - 1);
    memcpy(g_dirent.d_name, entry->d_name, name_len);
    g_dirent.d_name[name_len] = '\0';
    size_t raw = offsetof(a2i_android_dirent, d_name) + name_len + 1;
    g_dirent.d_reclen = (uint16_t)((raw + 7U) & ~7U);
    return &g_dirent;
}

typedef struct {
    uint32_t count;
    int32_t reserved[3];
} a2i_android_sem_t;

_Static_assert(sizeof(a2i_android_sem_t) == 16, "Android LP64 sem_t size");

typedef struct a2i_sem_node {
    const void *android_address;
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    unsigned value;
    struct a2i_sem_node *next;
} a2i_sem_node;

static os_unfair_lock g_sem_lock = OS_UNFAIR_LOCK_INIT;
static a2i_sem_node *g_sems = NULL;

static a2i_sem_node *a2i_find_sem_locked(const void *address) {
    for (a2i_sem_node *node = g_sems; node != NULL; node = node->next) {
        if (node->android_address == address) {
            return node;
        }
    }
    return NULL;
}

static a2i_sem_node *a2i_get_sem(a2i_android_sem_t *sem) {
    if (sem == NULL) {
        return NULL;
    }
    os_unfair_lock_lock(&g_sem_lock);
    a2i_sem_node *node = a2i_find_sem_locked(sem);
    os_unfair_lock_unlock(&g_sem_lock);
    return node;
}

int a2i_sem_init(a2i_android_sem_t *sem, int pshared, unsigned value) {
    if (sem == NULL || pshared != 0) {
        errno = pshared ? ENOSYS : EINVAL;
        return -1;
    }

    a2i_sem_node *node = calloc(1, sizeof(*node));
    if (node == NULL) {
        errno = ENOMEM;
        return -1;
    }
    pthread_mutex_init(&node->mutex, NULL);
    pthread_cond_init(&node->cond, NULL);
    node->android_address = sem;
    node->value = value;

    os_unfair_lock_lock(&g_sem_lock);
    if (a2i_find_sem_locked(sem) != NULL) {
        os_unfair_lock_unlock(&g_sem_lock);
        pthread_mutex_destroy(&node->mutex);
        pthread_cond_destroy(&node->cond);
        free(node);
        errno = EBUSY;
        return -1;
    }
    node->next = g_sems;
    g_sems = node;
    os_unfair_lock_unlock(&g_sem_lock);

    memset(sem, 0, sizeof(*sem));
    sem->count = value;
    return 0;
}

int a2i_sem_wait(a2i_android_sem_t *sem) {
    a2i_sem_node *node = a2i_get_sem(sem);
    if (node == NULL) {
        errno = EINVAL;
        return -1;
    }

    pthread_mutex_lock(&node->mutex);
    while (node->value == 0) {
        pthread_cond_wait(&node->cond, &node->mutex);
    }
    node->value -= 1;
    sem->count = node->value;
    pthread_mutex_unlock(&node->mutex);
    return 0;
}

int a2i_sem_post(a2i_android_sem_t *sem) {
    a2i_sem_node *node = a2i_get_sem(sem);
    if (node == NULL) {
        errno = EINVAL;
        return -1;
    }

    pthread_mutex_lock(&node->mutex);
    node->value += 1;
    sem->count = node->value;
    pthread_cond_signal(&node->cond);
    pthread_mutex_unlock(&node->mutex);
    return 0;
}

int a2i_sem_getvalue(a2i_android_sem_t *sem, int *value) {
    a2i_sem_node *node = a2i_get_sem(sem);
    if (node == NULL || value == NULL) {
        errno = EINVAL;
        return -1;
    }

    pthread_mutex_lock(&node->mutex);
    *value = (int)node->value;
    pthread_mutex_unlock(&node->mutex);
    return 0;
}

typedef struct {
    char sysname[65];
    char nodename[65];
    char release[65];
    char version[65];
    char machine[65];
    char domainname[65];
} a2i_android_utsname;

int a2i_uname(a2i_android_utsname *out) {
    if (out == NULL) {
        errno = EFAULT;
        return -1;
    }
    memset(out, 0, sizeof(*out));
    strlcpy(out->sysname, "Linux", sizeof(out->sysname));
    if (gethostname(out->nodename, sizeof(out->nodename)) != 0) {
        strlcpy(out->nodename, "localhost", sizeof(out->nodename));
    }
    strlcpy(out->release, "4.14.0-a2i", sizeof(out->release));
    strlcpy(out->version, "#1 SMP PREEMPT", sizeof(out->version));
    strlcpy(out->machine, "aarch64", sizeof(out->machine));
    strlcpy(out->domainname, "localdomain", sizeof(out->domainname));
    return 0;
}

size_t a2i___ctype_get_mb_cur_max(void) {
    return (size_t)MB_CUR_MAX;
}
