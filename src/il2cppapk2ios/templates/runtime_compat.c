#include <errno.h>
#include <os/lock.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>

enum {
    A2I_NR_FUTEX = 98,
    A2I_FUTEX_WAIT = 0,
    A2I_FUTEX_WAKE = 1,
    A2I_FUTEX_PRIVATE_FLAG = 128,
};

typedef struct a2i_futex_node {
    volatile uint32_t *address;
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    struct a2i_futex_node *next;
} a2i_futex_node;

static os_unfair_lock g_futex_lock = OS_UNFAIR_LOCK_INIT;
static a2i_futex_node *g_futexes = NULL;

static a2i_futex_node *a2i_get_futex(volatile uint32_t *address) {
    os_unfair_lock_lock(&g_futex_lock);
    for (a2i_futex_node *node = g_futexes; node != NULL; node = node->next) {
        if (node->address == address) {
            os_unfair_lock_unlock(&g_futex_lock);
            return node;
        }
    }

    a2i_futex_node *node = calloc(1, sizeof(*node));
    if (node != NULL) {
        node->address = address;
        pthread_mutex_init(&node->mutex, NULL);
        pthread_cond_init(&node->cond, NULL);
        node->next = g_futexes;
        g_futexes = node;
    }
    os_unfair_lock_unlock(&g_futex_lock);
    return node;
}

static int a2i_futex_wait(
    volatile uint32_t *address,
    uint32_t expected,
    const struct timespec *relative_timeout
) {
    if (address == NULL) {
        errno = EFAULT;
        return -1;
    }

    a2i_futex_node *node = a2i_get_futex(address);
    if (node == NULL) {
        errno = ENOMEM;
        return -1;
    }

    pthread_mutex_lock(&node->mutex);
    if (__atomic_load_n(address, __ATOMIC_ACQUIRE) != expected) {
        pthread_mutex_unlock(&node->mutex);
        errno = EAGAIN;
        return -1;
    }

    int rc = 0;
    if (relative_timeout == NULL) {
        while (__atomic_load_n(address, __ATOMIC_ACQUIRE) == expected) {
            rc = pthread_cond_wait(&node->cond, &node->mutex);
            if (rc != 0) break;
        }
    } else {
        struct timespec now;
        clock_gettime(CLOCK_REALTIME, &now);
        struct timespec deadline = now;
        deadline.tv_sec += relative_timeout->tv_sec;
        deadline.tv_nsec += relative_timeout->tv_nsec;
        if (deadline.tv_nsec >= 1000000000L) {
            deadline.tv_nsec -= 1000000000L;
            deadline.tv_sec += 1;
        }
        while (__atomic_load_n(address, __ATOMIC_ACQUIRE) == expected) {
            rc = pthread_cond_timedwait(
                &node->cond, &node->mutex, &deadline
            );
            if (rc != 0) break;
        }
    }

    pthread_mutex_unlock(&node->mutex);
    if (rc != 0) {
        errno = rc;
        return -1;
    }
    return 0;
}

static int a2i_futex_wake(volatile uint32_t *address, int count) {
    a2i_futex_node *node = a2i_get_futex(address);
    if (node == NULL) {
        errno = ENOMEM;
        return -1;
    }

    pthread_mutex_lock(&node->mutex);
    if (count <= 1) {
        pthread_cond_signal(&node->cond);
    } else {
        pthread_cond_broadcast(&node->cond);
    }
    pthread_mutex_unlock(&node->mutex);
    return count > 0 ? count : 0;
}

long a2i_syscall(long number, ...) {
    va_list ap;
    va_start(ap, number);

    if (number == A2I_NR_FUTEX) {
        volatile uint32_t *address = va_arg(ap, volatile uint32_t *);
        int op = va_arg(ap, int);
        uint32_t value = va_arg(ap, uint32_t);
        const struct timespec *timeout = va_arg(ap, const struct timespec *);
        va_end(ap);

        int command = op & ~A2I_FUTEX_PRIVATE_FLAG;
        if (command == A2I_FUTEX_WAIT) {
            return a2i_futex_wait(address, value, timeout);
        }
        if (command == A2I_FUTEX_WAKE) {
            return a2i_futex_wake(address, (int)value);
        }
        errno = ENOSYS;
        return -1;
    }

    va_end(ap);
    errno = ENOSYS;
    return -1;
}
