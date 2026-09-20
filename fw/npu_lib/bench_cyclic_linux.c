/* 주기 태스크의 **깨어나는 시각 지터**를 잰다 — 실시간성의 표준 지표.
 *
 * 앞서 만든 bench_slip_linux.c 는 연산 시간만 쟀다. 그런데 연산이 86 ns 로 워낙 짧아서
 * 리눅스에서도 거의 선점당하지 않는다 — 그래서 꼬리가 잘 안 보인다.
 * 실시간 제어에서 진짜 문제는 "계산이 오래 걸린다"가 아니라 **"제때 깨워 주지 않는다"** 다.
 * 2차 방어는 20 ms 주기로 IMU 를 받아 판정해야 하는데, 그 깨어남이 늦으면 판정 자체가 늦는다.
 *
 * cyclictest 와 같은 방식: 절대시각 기준으로 다음 주기를 자고, 깨어난 실제 시각에서
 * 의도한 시각을 뺀 값(지터)을 기록한다. 매 주기 보드와 같은 연산도 실제로 수행한다.
 *
 * 빌드: gcc -O2 -I. bench_cyclic_linux.c -o bench_cyclic -lm
 * 사용: ./bench_cyclic <주기us> <반복수> <출력파일>
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <math.h>

#include "slip_core.h"

int main(int argc, char **argv)
{
  long period_us = (argc > 1) ? atol(argv[1]) : 1000;
  long n         = (argc > 2) ? atol(argv[2]) : 20000;
  const char *out = (argc > 3) ? argv[3] : "/tmp/bench_cyclic.txt";

  slipdet_t d;
  slip_reset(&d, 0.02f, 8, 5.0f);

  long *jit = (long *)malloc(sizeof(long) * (size_t)n);
  if (!jit) { fprintf(stderr, "malloc 실패\n"); return 1; }

  struct timespec next;
  clock_gettime(CLOCK_MONOTONIC, &next);

  for (long i = 0; i < n; i++)
  {
    /* 다음 주기의 절대시각 */
    next.tv_nsec += period_us * 1000L;
    while (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }

    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);

    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    long late = (long)(now.tv_sec - next.tv_sec) * 1000000000L + (now.tv_nsec - next.tv_nsec);
    jit[i] = late;      /* 양수 = 늦게 깨어남 */

    /* 깨어난 뒤에는 보드와 같은 일을 한다 (현실적인 주기 부하) */
    float t = (float)i * 0.02f;
    int slipz = ((i / 200) % 4) == 3;
    float speed = 10.7f, steer = 0.04f * sinf(t);
    float ay = slipz ? -3.6f : 0.2f * sinf(t * 2.f);
    float gz = slipz ? -0.30f : 0.02f;
    uint8_t trig; float ay_g, ye, brk, stc;
    if (slip_step2(&d, ay, gz, speed, steer, slipz ? -0.6f : -0.05f, slipz ? 0.9f : 0.f,
                   &trig, &ay_g, &ye)) d.emerg = 1;
    if (d.emerg)
      slip_control2(&d, speed, gz, 0.05f, 0.3f, 40.f, 5.f, -1.f, d.kax.x0,
                    slipz ? 0.9f : 0.f, 0.f, 0.f, &brk, &stc);
    if (!slipz && (i % 200) == 0) slip_reset(&d, 0.02f, 8, 5.0f);
  }

  FILE *f = fopen(out, "w");
  if (!f) { fprintf(stderr, "출력 열기 실패\n"); return 1; }
  for (long i = 0; i < n; i++) fprintf(f, "%ld\n", jit[i]);
  fclose(f);
  free(jit);
  return 0;
}
