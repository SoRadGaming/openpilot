// FORK(COMM-ISSUE): LD_PRELOAD shim for fault_injection.py (docs/fork/COMM-ISSUE.md). Not built by scons.
//
// Records every fsync() and every flock(LOCK_EX) the process makes, with the calling thread's TID, when it started and
// how long it took, and can make directory fsyncs slow:
//   FSCOUNT_LOG  the record is written to FSCOUNT_LOG.<pid> at exit, one call per line: "<kind> <tid> <start_ns> <dur_ns>",
//                kind = fsync_dir | fsync | flock_ex, times CLOCK_MONOTONIC (the clock of Python's time.monotonic())
//   FSCOUNT_CTL  file read at every directory fsync; when it holds "<start_ns> <end_ns>", a directory fsync that starts
//                inside [start, end) returns only at end - one slow ext4 journal commit, as Params::put sees it from
//                inside <params>/.lock. Another process writes it to start the stall.
//
//   gcc -shared -fPIC -O2 -o fscount.so fscount.c -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define MAX_EVENTS (1 << 18)

enum { EV_FSYNC_DIR = 0, EV_FSYNC = 1, EV_FLOCK_EX = 2 };
static const char *EV_NAMES[] = {"fsync_dir", "fsync", "flock_ex"};

typedef struct {
  int kind;
  int tid;
  long start_ns;
  long dur_ns;
} event_t;

static event_t events[MAX_EVENTS];
static atomic_long n_events;

static long now_ns(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1000000000L + t.tv_nsec;
}

static void record(int kind, long start, long dur) {
  long i = atomic_fetch_add(&n_events, 1);
  if (i < MAX_EVENTS) {
    events[i] = (event_t){kind, (int)syscall(SYS_gettid), start, dur};
  }
}

// [start, end) from FSCOUNT_CTL, or 0 when there is no window
static int stall_window(long *start, long *end) {
  const char *ctl = getenv("FSCOUNT_CTL");
  if (!ctl) return 0;
  int fd = open(ctl, O_RDONLY);
  if (fd < 0) return 0;
  char buf[64] = {0};
  ssize_t n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  return n > 0 && sscanf(buf, "%ld %ld", start, end) == 2;
}

int fsync(int fd) {
  static int (*real_fsync)(int);
  if (!real_fsync) real_fsync = dlsym(RTLD_NEXT, "fsync");
  long t = now_ns();
  struct stat st;
  int isdir = fstat(fd, &st) == 0 && S_ISDIR(st.st_mode);
  long a, e;
  if (isdir && stall_window(&a, &e) && t >= a && t < e) {
    struct timespec s = {(e - t) / 1000000000L, (e - t) % 1000000000L};
    while (nanosleep(&s, &s) != 0) {}
  }
  int r = real_fsync(fd);
  record(isdir ? EV_FSYNC_DIR : EV_FSYNC, t, now_ns() - t);
  return r;
}

int flock(int fd, int op) {
  static int (*real_flock)(int, int);
  if (!real_flock) real_flock = dlsym(RTLD_NEXT, "flock");
  long t = now_ns();
  int r = real_flock(fd, op);
  if (op & LOCK_EX) record(EV_FLOCK_EX, t, now_ns() - t);
  return r;
}

// The record so far; at exit, or called (ctypes) by a process that leaves with _exit() - the child does, because
// interpreter shutdown can hang for 30 s in a zmq context's term() before it reaches the destructors.
void fscount_dump(void) {
  static atomic_int done;
  if (atomic_exchange(&done, 1)) return;
  const char *base = getenv("FSCOUNT_LOG");
  if (!base) return;
  char path[4096];
  snprintf(path, sizeof(path), "%s.%d", base, (int)getpid());  // per process: the child's git calls inherit the shim
  FILE *f = fopen(path, "w");
  if (!f) return;
  long n = atomic_load(&n_events);
  if (n > MAX_EVENTS) n = MAX_EVENTS;
  for (long i = 0; i < n; i++) {
    fprintf(f, "%s %d %ld %ld\n", EV_NAMES[events[i].kind], events[i].tid, events[i].start_ns, events[i].dur_ns);
  }
  fclose(f);
}

__attribute__((destructor)) static void write_log(void) { fscount_dump(); }
