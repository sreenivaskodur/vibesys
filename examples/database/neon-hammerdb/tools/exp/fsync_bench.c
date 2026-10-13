// Measures append+flush latency for the flush primitives available on macOS.
// Usage: fsync_bench <dir> [iterations]
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static double now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}

static int cmp(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void run(const char *dir, const char *name, int mode, int iters) {
    char path[1024];
    snprintf(path, sizeof path, "%s/fsync_bench_%s.dat", dir, name);
    int flags = O_CREAT | O_TRUNC | O_WRONLY | (mode == 3 ? O_DSYNC : 0);
    int fd = open(path, flags, 0600);
    if (fd < 0) { perror("open"); exit(1); }
    char buf[8192];
    memset(buf, 'x', sizeof buf);
    double *lat = malloc(sizeof(double) * iters);
    for (int i = 0; i < iters; i++) {
        double t0 = now_us();
        if (write(fd, buf, sizeof buf) != sizeof buf) { perror("write"); exit(1); }
        switch (mode) {
        case 0: fsync(fd); break;                     // plain fsync: no drive-cache flush on macOS
        case 1: fcntl(fd, F_FULLFSYNC); break;        // what Rust sync_data()/sync_all() use on macOS
        case 2: fcntl(fd, F_BARRIERFSYNC); break;     // write barrier (ordering), Apple's recommended middle ground
        case 3: break;                                // O_DSYNC write (Postgres open_datasync)
        }
        lat[i] = now_us() - t0;
    }
    qsort(lat, iters, sizeof(double), cmp);
    double sum = 0;
    for (int i = 0; i < iters; i++) sum += lat[i];
    printf("%-14s avg=%8.1fus p50=%8.1fus p95=%8.1fus p99=%8.1fus\n", name, sum / iters,
           lat[iters / 2], lat[(int)(iters * 0.95)], lat[(int)(iters * 0.99)]);
    close(fd);
    unlink(path);
    free(lat);
}

int main(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : ".";
    int iters = argc > 2 ? atoi(argv[2]) : 500;
    run(dir, "fsync", 0, iters);
    run(dir, "F_FULLFSYNC", 1, iters);
    run(dir, "F_BARRIERFSYNC", 2, iters);
    run(dir, "O_DSYNC", 3, iters);
    return 0;
}
