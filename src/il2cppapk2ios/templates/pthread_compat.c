#include <errno.h>
#include <os/lock.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef PTHREAD_STACK_MIN
#define PTHREAD_STACK_MIN 16384
#endif

typedef int64_t a2i_pthread_t;
typedef int32_t a2i_pthread_key_t;
typedef int32_t a2i_pthread_once_t;
typedef int64_t a2i_pthread_mutexattr_t;
typedef int64_t a2i_pthread_condattr_t;

typedef struct {
    uint32_t flags;
    void *stack_base;
    size_t stack_size;
    size_t guard_size;
    int32_t sched_policy;
    int32_t sched_priority;
    char reserved[16];
} a2i_pthread_attr_t;

typedef struct {
    int32_t private_[10];
} a2i_pthread_mutex_t;

typedef struct {
    int32_t private_[12];
} a2i_pthread_cond_t;

_Static_assert(sizeof(a2i_pthread_attr_t) == 56, "Android LP64 pthread_attr_t size");
_Static_assert(sizeof(a2i_pthread_mutex_t) == 40, "Android LP64 pthread_mutex_t size");
_Static_assert(sizeof(a2i_pthread_cond_t) == 48, "Android LP64 pthread_cond_t size");

enum {
    A2I_OBJ_MUTEX = 1,
    A2I_OBJ_COND = 2,
};

typedef struct a2i_obj_node {
    const void *android_address;
    int kind;
    int android_clock;
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    struct a2i_obj_node *next;
} a2i_obj_node;

static os_unfair_lock g_objects_lock = OS_UNFAIR_LOCK_INIT;
static a2i_obj_node *g_objects = NULL;

static int a2i_android_mutex_type(const a2i_pthread_mutex_t *mutex) {
    uint32_t value = (uint32_t)mutex->private_[0];
    return (int)((value >> 14) & 3U);
}

static int a2i_host_mutex_type(int android_type) {
    switch (android_type) {
        case 1:
            return PTHREAD_MUTEX_RECURSIVE;
        case 2:
            return PTHREAD_MUTEX_ERRORCHECK;
        default:
            return PTHREAD_MUTEX_NORMAL;
    }
}

static a2i_obj_node *a2i_find_object_locked(const void *address, int kind) {
    for (a2i_obj_node *node = g_objects; node != NULL; node = node->next) {
        if (node->android_address == address && node->kind == kind) {
            return node;
        }
    }
    return NULL;
}

static a2i_obj_node *a2i_create_mutex_locked(
    const a2i_pthread_mutex_t *android_mutex,
    int android_type
) {
    a2i_obj_node *node = calloc(1, sizeof(*node));
    if (node == NULL) {
        return NULL;
    }

    pthread_mutexattr_t attr;
    if (pthread_mutexattr_init(&attr) != 0) {
        free(node);
        return NULL;
    }
    pthread_mutexattr_settype(&attr, a2i_host_mutex_type(android_type));
    int rc = pthread_mutex_init(&node->mutex, &attr);
    pthread_mutexattr_destroy(&attr);
    if (rc != 0) {
        free(node);
        return NULL;
    }

    node->android_address = android_mutex;
    node->kind = A2I_OBJ_MUTEX;
    node->next = g_objects;
    g_objects = node;
    return node;
}

static a2i_obj_node *a2i_get_mutex(a2i_pthread_mutex_t *mutex) {
    if (mutex == NULL) {
        return NULL;
    }

    os_unfair_lock_lock(&g_objects_lock);
    a2i_obj_node *node = a2i_find_object_locked(mutex, A2I_OBJ_MUTEX);
    if (node == NULL) {
        node = a2i_create_mutex_locked(
            mutex,
            a2i_android_mutex_type(mutex)
        );
    }
    os_unfair_lock_unlock(&g_objects_lock);
    return node;
}

static a2i_obj_node *a2i_create_cond_locked(
    const a2i_pthread_cond_t *android_cond,
    int android_clock
) {
    a2i_obj_node *node = calloc(1, sizeof(*node));
    if (node == NULL) {
        return NULL;
    }

    int rc = pthread_cond_init(&node->cond, NULL);
    if (rc != 0) {
        free(node);
        return NULL;
    }

    node->android_address = android_cond;
    node->kind = A2I_OBJ_COND;
    node->android_clock = android_clock;
    node->next = g_objects;
    g_objects = node;
    return node;
}

static a2i_obj_node *a2i_get_cond(a2i_pthread_cond_t *cond) {
    if (cond == NULL) {
        return NULL;
    }

    os_unfair_lock_lock(&g_objects_lock);
    a2i_obj_node *node = a2i_find_object_locked(cond, A2I_OBJ_COND);
    if (node == NULL) {
        node = a2i_create_cond_locked(cond, 0);
    }
    os_unfair_lock_unlock(&g_objects_lock);
    return node;
}

static a2i_obj_node *a2i_remove_object(const void *address, int kind) {
    os_unfair_lock_lock(&g_objects_lock);

    a2i_obj_node **cursor = &g_objects;
    while (*cursor != NULL) {
        a2i_obj_node *node = *cursor;
        if (node->android_address == address && node->kind == kind) {
            *cursor = node->next;
            os_unfair_lock_unlock(&g_objects_lock);
            return node;
        }
        cursor = &node->next;
    }

    os_unfair_lock_unlock(&g_objects_lock);
    return NULL;
}

int a2i_pthread_mutexattr_init(a2i_pthread_mutexattr_t *attr) {
    if (attr == NULL) {
        return EINVAL;
    }
    *attr = 0;
    return 0;
}

int a2i_pthread_mutexattr_destroy(a2i_pthread_mutexattr_t *attr) {
    if (attr == NULL) {
        return EINVAL;
    }
    *attr = -1;
    return 0;
}

int a2i_pthread_mutexattr_settype(
    a2i_pthread_mutexattr_t *attr,
    int type
) {
    if (attr == NULL || type < 0 || type > 2) {
        return EINVAL;
    }
    *attr = (*attr & ~0x0fLL) | type;
    return 0;
}

int a2i_pthread_mutex_init(
    a2i_pthread_mutex_t *mutex,
    const a2i_pthread_mutexattr_t *attr
) {
    if (mutex == NULL) {
        return EINVAL;
    }

    a2i_obj_node *old = a2i_remove_object(mutex, A2I_OBJ_MUTEX);
    if (old != NULL) {
        pthread_mutex_destroy(&old->mutex);
        free(old);
    }

    int type = attr == NULL ? 0 : (int)(*attr & 0x0fLL);
    memset(mutex, 0, sizeof(*mutex));
    mutex->private_[0] = (int32_t)((type & 3) << 14);

    os_unfair_lock_lock(&g_objects_lock);
    a2i_obj_node *node = a2i_create_mutex_locked(mutex, type);
    os_unfair_lock_unlock(&g_objects_lock);
    return node == NULL ? ENOMEM : 0;
}

int a2i_pthread_mutex_destroy(a2i_pthread_mutex_t *mutex) {
    if (mutex == NULL) {
        return EINVAL;
    }
    a2i_obj_node *node = a2i_remove_object(mutex, A2I_OBJ_MUTEX);
    if (node == NULL) {
        memset(mutex, 0, sizeof(*mutex));
        return 0;
    }
    int rc = pthread_mutex_destroy(&node->mutex);
    free(node);
    memset(mutex, 0, sizeof(*mutex));
    return rc;
}

int a2i_pthread_mutex_lock(a2i_pthread_mutex_t *mutex) {
    a2i_obj_node *node = a2i_get_mutex(mutex);
    return node == NULL ? ENOMEM : pthread_mutex_lock(&node->mutex);
}

int a2i_pthread_mutex_unlock(a2i_pthread_mutex_t *mutex) {
    a2i_obj_node *node = a2i_get_mutex(mutex);
    return node == NULL ? EINVAL : pthread_mutex_unlock(&node->mutex);
}

int a2i_pthread_condattr_init(a2i_pthread_condattr_t *attr) {
    if (attr == NULL) {
        return EINVAL;
    }
    *attr = 0;
    return 0;
}

int a2i_pthread_condattr_destroy(a2i_pthread_condattr_t *attr) {
    if (attr == NULL) {
        return EINVAL;
    }
    *attr = -1;
    return 0;
}

int a2i_pthread_condattr_setclock(
    a2i_pthread_condattr_t *attr,
    int clock_id
) {
    if (attr == NULL || (clock_id != 0 && clock_id != 1)) {
        return EINVAL;
    }
    *attr = clock_id;
    return 0;
}

int a2i_pthread_cond_init(
    a2i_pthread_cond_t *cond,
    const a2i_pthread_condattr_t *attr
) {
    if (cond == NULL) {
        return EINVAL;
    }

    a2i_obj_node *old = a2i_remove_object(cond, A2I_OBJ_COND);
    if (old != NULL) {
        pthread_cond_destroy(&old->cond);
        free(old);
    }

    memset(cond, 0, sizeof(*cond));
    int android_clock = attr == NULL ? 0 : (int)*attr;

    os_unfair_lock_lock(&g_objects_lock);
    a2i_obj_node *node = a2i_create_cond_locked(cond, android_clock);
    os_unfair_lock_unlock(&g_objects_lock);
    return node == NULL ? ENOMEM : 0;
}

int a2i_pthread_cond_destroy(a2i_pthread_cond_t *cond) {
    if (cond == NULL) {
        return EINVAL;
    }
    a2i_obj_node *node = a2i_remove_object(cond, A2I_OBJ_COND);
    if (node == NULL) {
        memset(cond, 0, sizeof(*cond));
        return 0;
    }
    int rc = pthread_cond_destroy(&node->cond);
    free(node);
    memset(cond, 0, sizeof(*cond));
    return rc;
}

int a2i_pthread_cond_wait(
    a2i_pthread_cond_t *cond,
    a2i_pthread_mutex_t *mutex
) {
    a2i_obj_node *c = a2i_get_cond(cond);
    a2i_obj_node *m = a2i_get_mutex(mutex);
    if (c == NULL || m == NULL) {
        return EINVAL;
    }
    return pthread_cond_wait(&c->cond, &m->mutex);
}

static struct timespec a2i_monotonic_to_realtime(
    const struct timespec *android_absolute
) {
    struct timespec mono_now = {0, 0};
    struct timespec real_now = {0, 0};
    clock_gettime(CLOCK_MONOTONIC, &mono_now);
    clock_gettime(CLOCK_REALTIME, &real_now);

    int64_t sec = android_absolute->tv_sec - mono_now.tv_sec;
    int64_t nsec = android_absolute->tv_nsec - mono_now.tv_nsec;
    if (nsec < 0) {
        nsec += 1000000000LL;
        sec -= 1;
    }

    struct timespec result = real_now;
    result.tv_sec += sec;
    result.tv_nsec += nsec;
    if (result.tv_nsec >= 1000000000L) {
        result.tv_nsec -= 1000000000L;
        result.tv_sec += 1;
    }
    return result;
}

int a2i_pthread_cond_timedwait(
    a2i_pthread_cond_t *cond,
    a2i_pthread_mutex_t *mutex,
    const struct timespec *absolute
) {
    if (absolute == NULL) {
        return EINVAL;
    }
    a2i_obj_node *c = a2i_get_cond(cond);
    a2i_obj_node *m = a2i_get_mutex(mutex);
    if (c == NULL || m == NULL) {
        return EINVAL;
    }

    if (c->android_clock == 1) {
        struct timespec real_deadline =
            a2i_monotonic_to_realtime(absolute);
        return pthread_cond_timedwait(
            &c->cond,
            &m->mutex,
            &real_deadline
        );
    }

    return pthread_cond_timedwait(&c->cond, &m->mutex, absolute);
}

int a2i_pthread_cond_signal(a2i_pthread_cond_t *cond) {
    a2i_obj_node *node = a2i_get_cond(cond);
    return node == NULL ? EINVAL : pthread_cond_signal(&node->cond);
}

int a2i_pthread_cond_broadcast(a2i_pthread_cond_t *cond) {
    a2i_obj_node *node = a2i_get_cond(cond);
    return node == NULL ? EINVAL : pthread_cond_broadcast(&node->cond);
}

int a2i_pthread_attr_init(a2i_pthread_attr_t *attr) {
    if (attr == NULL) {
        return EINVAL;
    }
    memset(attr, 0, sizeof(*attr));
    attr->stack_size = 1024 * 1024;
    attr->guard_size = 16384;
    return 0;
}

int a2i_pthread_attr_destroy(a2i_pthread_attr_t *attr) {
    if (attr == NULL) {
        return EINVAL;
    }
    memset(attr, 0x42, sizeof(*attr));
    return 0;
}

int a2i_pthread_attr_getstack(
    const a2i_pthread_attr_t *attr,
    void **stack_base,
    size_t *stack_size
) {
    if (attr == NULL || stack_base == NULL || stack_size == NULL) {
        return EINVAL;
    }
    *stack_base = attr->stack_base;
    *stack_size = attr->stack_size;
    return 0;
}

int a2i_pthread_create(
    a2i_pthread_t *thread_out,
    const a2i_pthread_attr_t *android_attr,
    void *(*start_routine)(void *),
    void *arg
) {
    if (thread_out == NULL || start_routine == NULL) {
        return EINVAL;
    }

    pthread_attr_t attr;
    pthread_attr_init(&attr);

    if (android_attr != NULL) {
        if (android_attr->stack_size >= PTHREAD_STACK_MIN) {
            pthread_attr_setstacksize(&attr, android_attr->stack_size);
        }
        if (android_attr->flags & 1U) {
            pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
        }
    }

    pthread_t thread;
    int rc = pthread_create(&thread, &attr, start_routine, arg);
    pthread_attr_destroy(&attr);
    if (rc == 0) {
        *thread_out = (a2i_pthread_t)(uintptr_t)thread;
    }
    return rc;
}

int a2i_pthread_detach(a2i_pthread_t thread) {
    return pthread_detach((pthread_t)(uintptr_t)thread);
}

a2i_pthread_t a2i_pthread_self(void) {
    return (a2i_pthread_t)(uintptr_t)pthread_self();
}

int a2i_pthread_getattr_np(
    a2i_pthread_t thread,
    a2i_pthread_attr_t *attr
) {
    if (attr == NULL) {
        return EINVAL;
    }

    pthread_t native = (pthread_t)(uintptr_t)thread;
    size_t size = pthread_get_stacksize_np(native);
    void *top = pthread_get_stackaddr_np(native);

    memset(attr, 0, sizeof(*attr));
    attr->stack_size = size;
    attr->stack_base = (char *)top - size;
    attr->guard_size = 16384;
    return 0;
}

#define A2I_MAX_KEYS 256

typedef struct {
    int used;
    pthread_key_t host_key;
} a2i_key_entry;

static os_unfair_lock g_keys_lock = OS_UNFAIR_LOCK_INIT;
static a2i_key_entry g_keys[A2I_MAX_KEYS];

int a2i_pthread_key_create(
    a2i_pthread_key_t *key_out,
    void (*destructor)(void *)
) {
    if (key_out == NULL) {
        return EINVAL;
    }

    os_unfair_lock_lock(&g_keys_lock);
    for (int i = 0; i < A2I_MAX_KEYS; ++i) {
        if (!g_keys[i].used) {
            pthread_key_t host_key;
            int rc = pthread_key_create(&host_key, destructor);
            if (rc == 0) {
                g_keys[i].used = 1;
                g_keys[i].host_key = host_key;
                *key_out = i;
            }
            os_unfair_lock_unlock(&g_keys_lock);
            return rc;
        }
    }
    os_unfair_lock_unlock(&g_keys_lock);
    return EAGAIN;
}

static int a2i_host_key(
    a2i_pthread_key_t key,
    pthread_key_t *host_key
) {
    if (key < 0 || key >= A2I_MAX_KEYS || host_key == NULL) {
        return EINVAL;
    }

    os_unfair_lock_lock(&g_keys_lock);
    if (!g_keys[key].used) {
        os_unfair_lock_unlock(&g_keys_lock);
        return EINVAL;
    }
    *host_key = g_keys[key].host_key;
    os_unfair_lock_unlock(&g_keys_lock);
    return 0;
}

int a2i_pthread_key_delete(a2i_pthread_key_t key) {
    if (key < 0 || key >= A2I_MAX_KEYS) {
        return EINVAL;
    }

    os_unfair_lock_lock(&g_keys_lock);
    if (!g_keys[key].used) {
        os_unfair_lock_unlock(&g_keys_lock);
        return EINVAL;
    }
    pthread_key_t host_key = g_keys[key].host_key;
    g_keys[key].used = 0;
    os_unfair_lock_unlock(&g_keys_lock);
    return pthread_key_delete(host_key);
}

void *a2i_pthread_getspecific(a2i_pthread_key_t key) {
    pthread_key_t host_key;
    if (a2i_host_key(key, &host_key) != 0) {
        return NULL;
    }
    return pthread_getspecific(host_key);
}

int a2i_pthread_setspecific(
    a2i_pthread_key_t key,
    const void *value
) {
    pthread_key_t host_key;
    int rc = a2i_host_key(key, &host_key);
    if (rc != 0) {
        return rc;
    }
    return pthread_setspecific(host_key, value);
}

int a2i_pthread_once(
    a2i_pthread_once_t *once,
    void (*init_routine)(void)
) {
    if (once == NULL || init_routine == NULL) {
        return EINVAL;
    }

    _Atomic int32_t *state = (_Atomic int32_t *)once;
    for (;;) {
        int32_t value = atomic_load_explicit(state, memory_order_acquire);
        if (value == 2) {
            return 0;
        }

        if (value == 0) {
            int32_t expected = 0;
            if (atomic_compare_exchange_strong_explicit(
                    state,
                    &expected,
                    1,
                    memory_order_acq_rel,
                    memory_order_acquire)) {
                init_routine();
                atomic_store_explicit(state, 2, memory_order_release);
                return 0;
            }
        }

        sched_yield();
    }
}
