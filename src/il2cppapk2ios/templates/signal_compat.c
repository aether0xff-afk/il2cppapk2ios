#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef uint64_t a2i_sigset_t;
typedef void (*a2i_sighandler_t)(int);
typedef void (*a2i_android_sigaction_handler_t)(int, void *, void *);

typedef struct {
    int sa_flags;
    int _pad;
    union {
        a2i_sighandler_t handler;
        a2i_android_sigaction_handler_t sigaction_handler;
    } action;
    a2i_sigset_t sa_mask;
    void (*sa_restorer)(void);
} a2i_android_sigaction;

typedef struct {
    int si_signo;
    int si_errno;
    int si_code;
    int _pad;
    void *si_addr;
    unsigned char reserved[128 - 24];
} a2i_siginfo;

_Static_assert(sizeof(a2i_android_sigaction) == 32, "Android LP64 sigaction size");
_Static_assert(sizeof(a2i_siginfo) == 128, "Linux siginfo size");

enum {
    A2I_SA_NOCLDSTOP = 0x00000001,
    A2I_SA_NOCLDWAIT = 0x00000002,
    A2I_SA_SIGINFO = 0x00000004,
    A2I_SA_ONSTACK = 0x08000000,
    A2I_SA_RESTART = 0x10000000,
    A2I_SA_NODEFER = 0x40000000,
    A2I_SA_RESETHAND = (int)0x80000000u,
};

static a2i_android_sigaction g_actions[65];

static int a2i_host_signal(int android_signal) {
    switch (android_signal) {
        case 1: return SIGHUP;
        case 2: return SIGINT;
        case 3: return SIGQUIT;
        case 4: return SIGILL;
        case 5: return SIGTRAP;
        case 6: return SIGABRT;
        case 7: return SIGBUS;
        case 8: return SIGFPE;
        case 9: return SIGKILL;
        case 10: return SIGUSR1;
        case 11: return SIGSEGV;
        case 12: return SIGUSR2;
        case 13: return SIGPIPE;
        case 14: return SIGALRM;
        case 15: return SIGTERM;
#ifdef SIGCHLD
        case 17: return SIGCHLD;
#endif
#ifdef SIGCONT
        case 18: return SIGCONT;
#endif
#ifdef SIGSTOP
        case 19: return SIGSTOP;
#endif
#ifdef SIGTSTP
        case 20: return SIGTSTP;
#endif
#ifdef SIGTTIN
        case 21: return SIGTTIN;
#endif
#ifdef SIGTTOU
        case 22: return SIGTTOU;
#endif
#ifdef SIGURG
        case 23: return SIGURG;
#endif
#ifdef SIGXCPU
        case 24: return SIGXCPU;
#endif
#ifdef SIGXFSZ
        case 25: return SIGXFSZ;
#endif
#ifdef SIGVTALRM
        case 26: return SIGVTALRM;
#endif
#ifdef SIGPROF
        case 27: return SIGPROF;
#endif
#ifdef SIGWINCH
        case 28: return SIGWINCH;
#endif
#ifdef SIGIO
        case 29: return SIGIO;
#endif
#ifdef SIGSYS
        case 31: return SIGSYS;
#endif
        default: return -1;
    }
}

static int a2i_android_signal(int host_signal) {
    for (int i = 1; i < 65; ++i) {
        if (a2i_host_signal(i) == host_signal) return i;
    }
    return host_signal;
}

static void a2i_to_host_sigset(
    a2i_sigset_t android_set,
    sigset_t *host_set
) {
    sigemptyset(host_set);
    for (int android_signal = 1; android_signal < 65; ++android_signal) {
        if ((android_set & (1ULL << (android_signal - 1))) == 0) continue;
        int host_signal = a2i_host_signal(android_signal);
        if (host_signal > 0) sigaddset(host_set, host_signal);
    }
}

static int a2i_host_flags(int android_flags) {
    int flags = 0;
#ifdef SA_NOCLDSTOP
    if (android_flags & A2I_SA_NOCLDSTOP) flags |= SA_NOCLDSTOP;
#endif
#ifdef SA_NOCLDWAIT
    if (android_flags & A2I_SA_NOCLDWAIT) flags |= SA_NOCLDWAIT;
#endif
#ifdef SA_SIGINFO
    if (android_flags & A2I_SA_SIGINFO) flags |= SA_SIGINFO;
#endif
#ifdef SA_ONSTACK
    if (android_flags & A2I_SA_ONSTACK) flags |= SA_ONSTACK;
#endif
#ifdef SA_RESTART
    if (android_flags & A2I_SA_RESTART) flags |= SA_RESTART;
#endif
#ifdef SA_NODEFER
    if (android_flags & A2I_SA_NODEFER) flags |= SA_NODEFER;
#endif
#ifdef SA_RESETHAND
    if (android_flags & A2I_SA_RESETHAND) flags |= SA_RESETHAND;
#endif
    return flags;
}

static void a2i_signal_trampoline(
    int host_signal,
    siginfo_t *host_info,
    void *host_context
) {
    (void)host_context;
    int android_signal = a2i_android_signal(host_signal);
    if (android_signal <= 0 || android_signal >= 65) return;

    a2i_android_sigaction action = g_actions[android_signal];

    if (action.sa_flags & A2I_SA_SIGINFO) {
        a2i_siginfo info;
        memset(&info, 0, sizeof(info));
        info.si_signo = android_signal;
        if (host_info != NULL) {
            info.si_errno = host_info->si_errno;
            info.si_code = host_info->si_code;
            info.si_addr = host_info->si_addr;
        }

        // Normal execution never consumes this object. On a crash, provide
        // a zeroed Android-sized scratch context rather than a Darwin ucontext
        // whose layout would be actively misleading.
        unsigned char android_context[8192] __attribute__((aligned(16)));
        memset(android_context, 0, sizeof(android_context));

        if (action.action.sigaction_handler != NULL) {
            action.action.sigaction_handler(
                android_signal, &info, android_context
            );
        }
    } else if (
        action.action.handler != SIG_DFL &&
        action.action.handler != SIG_IGN &&
        action.action.handler != NULL
    ) {
        action.action.handler(android_signal);
    }
}

int a2i_sigaction(
    int android_signal,
    const a2i_android_sigaction *new_action,
    a2i_android_sigaction *old_action
) {
    int host_signal = a2i_host_signal(android_signal);
    if (host_signal < 0) {
        errno = EINVAL;
        return -1;
    }

    if (old_action != NULL) {
        *old_action = g_actions[android_signal];
    }
    if (new_action == NULL) return 0;

    g_actions[android_signal] = *new_action;

    struct sigaction host_action;
    memset(&host_action, 0, sizeof(host_action));
    a2i_to_host_sigset(new_action->sa_mask, &host_action.sa_mask);
    host_action.sa_flags = a2i_host_flags(new_action->sa_flags);

    if (
        new_action->action.handler == SIG_DFL ||
        new_action->action.handler == SIG_IGN
    ) {
        host_action.sa_handler = new_action->action.handler;
        host_action.sa_flags &= ~SA_SIGINFO;
    } else {
        host_action.sa_sigaction = a2i_signal_trampoline;
        host_action.sa_flags |= SA_SIGINFO;
    }

    return sigaction(host_signal, &host_action, NULL);
}

a2i_sighandler_t a2i_signal(
    int android_signal,
    a2i_sighandler_t handler
) {
    a2i_android_sigaction old_action;
    a2i_android_sigaction new_action;
    memset(&new_action, 0, sizeof(new_action));
    new_action.action.handler = handler;
    if (a2i_sigaction(android_signal, &new_action, &old_action) != 0) {
        return SIG_ERR;
    }
    return old_action.action.handler;
}

int a2i_sigfillset(a2i_sigset_t *set) {
    if (set == NULL) {
        errno = EINVAL;
        return -1;
    }
    *set = UINT64_MAX;
    return 0;
}

int a2i_sigdelset(a2i_sigset_t *set, int android_signal) {
    if (set == NULL || android_signal <= 0 || android_signal > 64) {
        errno = EINVAL;
        return -1;
    }
    *set &= ~(1ULL << (android_signal - 1));
    return 0;
}

int a2i_sigsuspend(const a2i_sigset_t *set) {
    if (set == NULL) {
        errno = EINVAL;
        return -1;
    }
    sigset_t host;
    a2i_to_host_sigset(*set, &host);
    return sigsuspend(&host);
}

int a2i_tgkill(int tgid, int tid, int android_signal) {
    (void)tgid;
    uint64_t current_tid = 0;
    pthread_threadid_np(NULL, &current_tid);
    if ((int)(current_tid & 0x7fffffffU) != tid) {
        errno = ESRCH;
        return -1;
    }

    int host_signal = a2i_host_signal(android_signal);
    if (host_signal < 0) {
        errno = EINVAL;
        return -1;
    }
    return raise(host_signal);
}
