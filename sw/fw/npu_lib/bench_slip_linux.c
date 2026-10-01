/* 보드와 **같은 C 코드**(slip_core.h)를 리눅스에서 돌려 연산 지연 분포를 잰다.
 *
 * 왜 필요한가: 보드는 DWT 사이클 카운터로 slip_step2 + slip_control2 의 연산 구간을 재고
 * 그 값이 14,162 샘플에서 평균 11.8 us / 최대 17.9 us 였다. "RTOS 가 왜 필요한가"에 답하려면
 * **같은 코드**를 범용 OS 에서 재서 분포의 꼬리를 비교해야 한다. 평균 속도는 Pi 5(2.4 GHz A76)가
 * 당연히 빠르다 — 쟁점은 속도가 아니라 **예측 가능성**이다.
 *
 * 빌드: gcc -O2 -I. bench_slip_linux.c -o bench_slip -lm
 * 사용: ./bench_slip <반복수> <출력파일>
 *       출력은 한 줄에 하나씩 나노초 정수.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <math.h>

#include "slip_core.h"

static inline uint64_t now_ns(void)
{
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

int main(int argc, char **argv)
{
  long n = (argc > 1) ? atol(argv[1]) : 20000;
  const char *out = (argc > 2) ? argv[2] : "/tmp/bench_slip.txt";

  slipdet_t d;
  slip_reset(&d, 0.02f, 8, 5.0f);

  uint64_t *lat = (uint64_t *)malloc(sizeof(uint64_t) * (size_t)n);
  if (!lat) { fprintf(stderr, "malloc 실패\n"); return 1; }

  /* 데모 주행과 비슷한 입력: 대부분 정상 주행, 주기적으로 미끄러짐 구간.
   * 분기(정상/확정/비상 제어)를 모두 지나가야 실제와 같은 연산량이 된다. */
  volatile float sink = 0.f;
  for (long i = 0; i < n; i++)
  {
    float t     = (float)i * 0.02f;
    int   slipz = ((i / 500) % 4) == 3;               /* 25 % 구간은 미끄러짐 */
    float speed = 10.7f + 0.5f * sinf(t * 0.3f);
    float steer = 0.04f * sinf(t * 0.7f);
    float ay    = slipz ? -3.6f + 0.3f * sinf(t * 5.f) : 0.2f * sinf(t * 2.f);
    float gz    = slipz ? -0.30f : 0.02f * sinf(t * 1.3f);
    float ax    = slipz ? -0.6f : -0.05f;
    float brake = slipz ? 0.9f : 0.0f;

    uint8_t trig; float ay_g, ye, brk, stc;

    uint64_t t0 = now_ns();
    int fired = slip_step2(&d, ay, gz, speed, steer, ax, brake, &trig, &ay_g, &ye);
    if (fired) d.emerg = 1;
    if (d.emerg)
      slip_control2(&d, speed, gz, 0.05f, 0.3f, 40.f, 5.f, -1.f,
                    d.kax.x0, brake, 0.f, 0.f, &brk, &stc);
    uint64_t t1 = now_ns();

    lat[i] = t1 - t0;
    sink += ay_g + ye;

    if (!slipz && (i % 500) == 0) { slip_reset(&d, 0.02f, 8, 5.0f); }   /* 주기적으로 정상 복귀 */
  }

  FILE *f = fopen(out, "w");
  if (!f) { fprintf(stderr, "출력 열기 실패\n"); return 1; }
  for (long i = 0; i < n; i++) fprintf(f, "%llu\n", (unsigned long long)lat[i]);
  fclose(f);
  free(lat);
  (void)sink;
  return 0;
}
