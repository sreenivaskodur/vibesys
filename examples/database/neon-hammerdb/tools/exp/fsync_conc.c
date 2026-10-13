// Concurrent append+F_FULLFSYNC on one file from N threads: does a second
// full flush overlap the first, or serialize behind it?
// Usage: fsync_conc <dir> <threads> <seconds>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static int fd, secs, method;
static double now_us(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e6+t.tv_nsec/1e3;}
typedef struct { long n; double sum; } stat_t;
static void *worker(void *arg){
  stat_t *s=arg; char buf[8192]; memset(buf,'y',sizeof buf);
  double end=now_us()+secs*1e6;
  while(now_us()<end){
    double t0=now_us();
    if(write(fd,buf,sizeof buf)!=sizeof buf){perror("write");exit(1);}
    if(method==1) fcntl(fd,F_FULLFSYNC); else if(method==2) fcntl(fd,F_BARRIERFSYNC); else fsync(fd);
    s->sum+=now_us()-t0; s->n++;
  }
  return NULL;
}
int main(int argc,char**argv){
  char p[1024]; snprintf(p,sizeof p,"%s/fsync_conc.dat",argv[1]);
  int nt=atoi(argv[2]); secs=atoi(argv[3]); method=argc>4?atoi(argv[4]):1;
  fd=open(p,O_CREAT|O_TRUNC|O_WRONLY|O_APPEND,0600);
  pthread_t th[64]; stat_t st[64]={0};
  for(int i=0;i<nt;i++) pthread_create(&th[i],NULL,worker,&st[i]);
  long n=0; double sum=0;
  for(int i=0;i<nt;i++){pthread_join(th[i],NULL); n+=st[i].n; sum+=st[i].sum;}
  printf("method=%s threads=%d ops/s=%.0f avg_latency=%.2fms\n", method==1?"F_FULLFSYNC":method==2?"F_BARRIERFSYNC":"fsync", nt, n/(double)secs, sum/n/1000);
  close(fd); unlink(p); return 0;
}
