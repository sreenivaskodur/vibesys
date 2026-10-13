// F_FULLFSYNC latency for 8 KiB appends into a 16 MiB WAL-segment-sized file created
// three ways, interleaved round-robin so background load affects all equally:
//   sparse   ftruncate() only (what the safekeeper's set_len() does; sparse on APFS)
//   zeroed   zeros physically written and F_FULLFSYNC'd up front (what Postgres does)
//   prealloc fcntl(F_PREALLOCATE) contiguous allocation + ftruncate
// Usage: fullfsync_prealloc <dir> [rounds]
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define SEG (16 * 1024 * 1024)
#define BLK 8192

static double now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e6 + t.tv_nsec / 1e3;
}
static int cmp(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static int make_file(const char *dir, int kind) {
    char p[1024];
    snprintf(p, sizeof p, "%s/prealloc_%d.dat", dir, kind);
    unlink(p);
    int fd = open(p, O_CREAT | O_RDWR, 0600);
    if (fd < 0) { perror("open"); exit(1); }
    if (kind == 1) {
        static char zero[1 << 20];
        for (int i = 0; i < SEG / (int)sizeof zero; i++)
            if (write(fd, zero, sizeof zero) != sizeof zero) { perror("write"); exit(1); }
    } else if (kind == 2) {
        fstore_t fs = {F_ALLOCATECONTIG | F_ALLOCATEALL, F_PEOFPOSMODE, 0, SEG, 0};
        if (fcntl(fd, F_PREALLOCATE, &fs) == -1) {
            fs.fst_flags = F_ALLOCATEALL;
            if (fcntl(fd, F_PREALLOCATE, &fs) == -1) { perror("F_PREALLOCATE"); exit(1); }
        }
    }
    if (ftruncate(fd, SEG) != 0) { perror("ftruncate"); exit(1); }
    fcntl(fd, F_FULLFSYNC);  // file creation durable before measuring appends
    return fd;
}

int main(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : ".";
    int rounds = argc > 2 ? atoi(argv[2]) : 400;
    if (rounds > SEG / BLK) rounds = SEG / BLK;
    const char *names[3] = {"sparse", "zeroed", "prealloc"};
    int fds[3];
    double *lat[3];
    for (int k = 0; k < 3; k++) { fds[k] = make_file(dir, k); lat[k] = malloc(sizeof(double) * rounds); }
    char buf[BLK];
    memset(buf, 'w', sizeof buf);
    for (int i = 0; i < rounds; i++)
        for (int k = 0; k < 3; k++) {
            double t0 = now_us();
            if (pwrite(fds[k], buf, BLK, (off_t)i * BLK) != BLK) { perror("pwrite"); exit(1); }
            fcntl(fds[k], F_FULLFSYNC);
            lat[k][i] = now_us() - t0;
        }
    for (int k = 0; k < 3; k++) {
        qsort(lat[k], rounds, sizeof(double), cmp);
        double sum = 0;
        for (int i = 0; i < rounds; i++) sum += lat[k][i];
        printf("%-9s avg=%7.2fms p50=%7.2fms p95=%7.2fms p99=%7.2fms\n", names[k], sum / rounds / 1000,
               lat[k][rounds / 2] / 1000, lat[k][rounds * 95 / 100] / 1000, lat[k][rounds * 99 / 100] / 1000);
        char p[1024];
        snprintf(p, sizeof p, "%s/prealloc_%d.dat", dir, k);
        close(fds[k]);
        unlink(p);
    }
    return 0;
}
