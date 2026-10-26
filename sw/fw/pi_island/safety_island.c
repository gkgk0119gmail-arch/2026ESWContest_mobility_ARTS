/* safety_island.c — 라즈베리파이 5 의 격리 코어에서 2차 방어를 돌리는 '소프트웨어 안전 섬'.

   STM32N6 보드가 없을 때의 대체 구성이다. 보드 펌웨어와 **같은 C 코어**(slip_core.h, 보드 호출 순서는
   slip_core_shim.c 의 sc_board)를 그대로 포함하고, 다음만 리눅스 쪽으로 바꾼다.
     · 정해진 CPU 코어 하나에 고정 (sched_setaffinity) + 실시간 우선순위 (SCHED_FIFO) + 메모리 잠금 (mlockall)
     · 보드의 'IMU 인터럽트로 깨어남' → UDP 패킷 도착으로 깨어남
     · 파이 메인 프로그램의 심장박동(UDP) 감시 — 끊기면 SLIP_WDT 축소 모드
   한계: 리눅스 커널 자체가 멈추면 같이 멈춘다. 실차 설계는 별도 MCU 다 (19번 문서).

   모드
     safety_island --udp PORT [--bind 0.0.0.0] [--cpu 3] [--prio 80]
         IMU 패킷(ISL1)마다 판정하고 보낸 곳으로 답한다. 심장박동(HB01)이 직전 IMU 이후에 왔으면 host_fresh=1.
     safety_island --selftest SEC [--cpu 3] [--prio 80] [--json out.json]
         20 ms 절대 주기 타이머로 깨어나 합성 IMU 로 같은 코어를 돌리고, 깨어남 지연과 계산 시간을 잰다.
   빌드: make -C sw/fw/pi_island */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <math.h>
#include <netinet/in.h>
#include <poll.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "slip_core_shim.c"   /* slip_core.h + 보드와 같은 호출 순서 sc_board() */

#define MAGIC_IMU  0x314C5349u   /* "ISL1" */
#define MAGIC_HB   0x31304248u   /* "HB01" */
#define MAGIC_OUT  0x314F5349u   /* "ISO1" */
#define PERIOD_NS  20000000L
#define NHIST      2000           /* 10 µs 칸 × 2000 = 20 ms */

typedef struct { uint32_t magic, seq; float in[13]; } imu_pkt_t;
typedef struct { uint32_t magic, seq; float out[12]; float compute_us; uint32_t degraded; } out_pkt_t;

static int64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000000000LL + t.tv_nsec; }

static void make_rt(int cpu, int prio)
{
  if (cpu >= 0) {
    cpu_set_t s; CPU_ZERO(&s); CPU_SET(cpu, &s);
    if (sched_setaffinity(0, sizeof s, &s)) perror("[island] sched_setaffinity");
  }
  if (prio > 0) {
    struct sched_param p = { .sched_priority = prio };
    if (sched_setscheduler(0, SCHED_FIFO, &p)) perror("[island] SCHED_FIFO (sudo 필요)");
  }
  if (mlockall(MCL_CURRENT | MCL_FUTURE)) perror("[island] mlockall");
  fprintf(stderr, "[island] cpu %d, 정책 %s, 우선순위 %d\n", sched_getcpu(),
          sched_getscheduler(0) == SCHED_FIFO ? "SCHED_FIFO" : "기본", prio);
}

typedef struct { uint32_t h[NHIST]; uint64_t n; double max_us, sum_us; } hist_t;
static void hist_add(hist_t *h, double us)
{
  int k = (int)(us / 10.0); if (k < 0) k = 0; if (k >= NHIST) k = NHIST - 1;
  h->h[k]++; h->n++; h->sum_us += us; if (us > h->max_us) h->max_us = us;
}
static double hist_q(const hist_t *h, double q)
{
  uint64_t need = (uint64_t)ceil(q * (double)h->n), c = 0;
  for (int k = 0; k < NHIST; k++) { c += h->h[k]; if (c >= need) return (k + 1) * 10.0; }
  return NHIST * 10.0;
}

static int selftest(double sec, const char *json)
{
  static slipdet_t d; sc_reset(&d, 0.02f, 3, 2.0f);
  static hist_t wake, comp; memset(&wake, 0, sizeof wake); memset(&comp, 0, sizeof comp);
  struct timespec next; clock_gettime(CLOCK_MONOTONIC, &next);
  long n = (long)(sec * 50.0), miss = 0;
  float in[13] = {0}, out[12];
  for (long i = 0; i < n; i++) {
    next.tv_nsec += PERIOD_NS; while (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) == EINTR) {}
    int64_t t0 = now_ns();
    double late = (double)(t0 - ((int64_t)next.tv_sec * 1000000000LL + next.tv_nsec)) / 1000.0;
    /* 합성 IMU: 15 m/s 순항, 6 s 마다 2 s 저마찰 제동 (확정·비상 제어 경로를 모두 지나게) */
    double t = i * 0.02; int brk = fmod(t, 6.0) > 4.0;
    in[0] = brk ? -0.5f : 0.f; in[1] = 0.01f * (float)sin(t); in[2] = 15.f; in[3] = 0.02f * (float)sin(0.7 * t);
    in[4] = brk ? 0.6f : 0.f; in[5] = 0.f; in[6] = 0.f; in[7] = 0.f; in[8] = 999.f; in[9] = 999.f; in[10] = 999.f; in[11] = 0.f; in[12] = 0.f;
    sc_set_host_fresh(1);
    sc_board(&d, in, out);
    if (!brk && d.emerg && fmod(t, 6.0) < 0.1) sc_reset(&d, 0.02f, 3, 2.0f);
    double c = (double)(now_ns() - t0) / 1000.0;
    hist_add(&wake, late); hist_add(&comp, c);
    if (late + c > 20000.0) miss++;
  }
  printf("{\"samples\": %ld, \"deadline_miss\": %ld, "
         "\"wake_us\": {\"mean\": %.1f, \"p99\": %.0f, \"p999\": %.0f, \"max\": %.1f}, "
         "\"compute_us\": {\"mean\": %.2f, \"p99\": %.0f, \"max\": %.1f}, \"cpu\": %d, \"fifo\": %d}\n",
         n, miss, wake.sum_us / wake.n, hist_q(&wake, 0.99), hist_q(&wake, 0.999), wake.max_us,
         comp.sum_us / comp.n, hist_q(&comp, 0.99), comp.max_us, sched_getcpu(), sched_getscheduler(0) == SCHED_FIFO);
  if (json) {
    FILE *f = fopen(json, "w");
    if (f) {
      fprintf(f, "{\"samples\": %ld, \"deadline_miss\": %ld, \"wake_us\": {\"mean\": %.1f, \"p99\": %.0f, \"p999\": %.0f, \"max\": %.1f}, "
                 "\"compute_us\": {\"mean\": %.2f, \"p99\": %.0f, \"max\": %.1f}, \"fifo\": %d, \"wake_hist_10us\": [",
              n, miss, wake.sum_us / wake.n, hist_q(&wake, 0.99), hist_q(&wake, 0.999), wake.max_us,
              comp.sum_us / comp.n, hist_q(&comp, 0.99), comp.max_us, sched_getscheduler(0) == SCHED_FIFO);
      int last = 0; for (int k = 0; k < NHIST; k++) if (wake.h[k]) last = k;
      for (int k = 0; k <= last; k++) fprintf(f, "%s%u", k ? "," : "", wake.h[k]);
      fprintf(f, "]}\n"); fclose(f);
    }
  }
  return 0;
}

static int udp_loop(int port, const char *bind_ip)
{
  int s = socket(AF_INET, SOCK_DGRAM, 0);
  struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons((uint16_t)port), .sin_addr.s_addr = inet_addr(bind_ip) };
  if (bind(s, (struct sockaddr *)&a, sizeof a)) { perror("[island] bind"); return 1; }
  static slipdet_t d; float min_speed = 25.f / 3.6f; sc_reset(&d, 0.02f, 3, min_speed);   /* 데모 기본 --min-detect-kph 25 */
  int hb_since = 1; uint64_t n_imu = 0, n_hb = 0; double max_c = 0; int64_t t_rep = now_ns();
  fprintf(stderr, "[island] UDP %s:%d 대기 — IMU(ISL1)마다 판정, 심장박동(HB01) 감시\n", bind_ip, port);
  for (;;) {
    unsigned char buf[256]; struct sockaddr_in from; socklen_t fl = sizeof from;
    ssize_t r = recvfrom(s, buf, sizeof buf, 0, (struct sockaddr *)&from, &fl);
    if (r < 8) continue;
    int64_t t0 = now_ns();
    uint32_t mg; memcpy(&mg, buf, 4);
    if (mg == MAGIC_HB) { hb_since = 1; n_hb++; continue; }
    if (mg == 0x54455352u /* "RSET" [+ float 판정 최소 속도 m/s] */) {
      if (r >= 12) memcpy(&min_speed, buf + 8, 4);
      sc_reset(&d, 0.02f, 3, min_speed); hb_since = 1; continue;
    }
    if (mg != MAGIC_IMU || r < (ssize_t)sizeof(imu_pkt_t)) continue;
    imu_pkt_t p; memcpy(&p, buf, sizeof p);
    out_pkt_t o = { .magic = MAGIC_OUT, .seq = p.seq };
    sc_set_host_fresh(hb_since); hb_since = 0;
    sc_board(&d, p.in, o.out);
    o.degraded = (uint32_t)sc_degraded(&d);
    o.compute_us = (float)((now_ns() - t0) / 1000.0); if (o.compute_us > max_c) max_c = o.compute_us;
    sendto(s, &o, sizeof o, 0, (struct sockaddr *)&from, fl);
    n_imu++;
    if (now_ns() - t_rep > 5000000000LL) {
      fprintf(stderr, "[island] IMU %llu, 심장박동 %llu, 축소 모드 %d, 최대 처리 %.1f µs\n",
              (unsigned long long)n_imu, (unsigned long long)n_hb, sc_degraded(&d), max_c);
      t_rep = now_ns();
    }
  }
}

int main(int argc, char **argv)
{
  int cpu = 3, prio = 80, port = 0; double st = 0; const char *json = NULL, *bind_ip = "127.0.0.1";
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--cpu") && i + 1 < argc) cpu = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--prio") && i + 1 < argc) prio = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--udp") && i + 1 < argc) port = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--selftest") && i + 1 < argc) st = atof(argv[++i]);
    else if (!strcmp(argv[i], "--json") && i + 1 < argc) json = argv[++i];
    else if (!strcmp(argv[i], "--bind") && i + 1 < argc) bind_ip = argv[++i];
    else { fprintf(stderr, "사용: %s (--udp PORT | --selftest SEC [--json F]) [--cpu N] [--prio P (0=기본 스케줄러)]\n", argv[0]); return 2; }
  }
  fprintf(stderr, "[island] 코어 플래그 %d (1 타원, 2 마찰 추정, 8 감시)\n", sc_flags());
  make_rt(cpu, prio);
  if (st > 0) return selftest(st, json);
  if (port > 0) return udp_loop(port, bind_ip);
  fprintf(stderr, "--udp 또는 --selftest 가 필요하다\n"); return 2;
}
