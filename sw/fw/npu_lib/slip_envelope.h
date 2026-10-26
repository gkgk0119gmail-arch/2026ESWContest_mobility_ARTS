/* slip_envelope.h — 2차 방어 확장: 마찰 인식 제동 시점 (SLIP_ENVELOPE, 기본 0 → 기존 동작 그대로)

   지금의 간격 유지·긴급제동은 마른 노면 감속을 가정해 '언제부터 서야 하나'를 계산한다. 빙판에서는 그 시점이 늦다.
   이 모듈은 2차 방어가 아는 마찰 μ 로 같은 계산을 다시 한다. 20 ms 마다 고정 횟수 연산만 한다 (표 9칸 보간, sqrtf 1).
     · 정지 거리   d_stop = v·T + v² / (2·a(μ))                (T: 시스템 지연, a(μ): 표의 제동 1.0 실측 감속 × 여유율)
     · 필요 거리   d_need = v·T + (v² − v_lead²) / (2·a(μ)) + 여유
     · 권장 속도   v_max  : 지금 간격에서 d_need 를 넘지 않는 최대 속도
     · 노면 확인 탭: 빙판이 의심되면(1차 방어가 '애매'하거나 외기 온도가 낮을 때 호출 쪽이 시작) 가속 페달을 0.2 s 뗀 뒤 제동 0.3 을 0.4 s 걸고
                    마지막 0.25 s 평균 감속을 표(제동 0.3 실측)로 되돌려 μ 를 잰다. 마른 노면이면 μ 를 바꾸지 않는다.
   모드: 0 순항 · 1 노면 확인 · 2 감속 권고(가속 금지) · 3 예비 제동 · 4 전력 제동 · 5 정지 유지
   표: slip_env_table.h (build_env_table.py 가 CARLA 보정에서 생성) */
#ifndef SLIP_ENVELOPE_H
#define SLIP_ENVELOPE_H
#include <math.h>
#include "slip_env_table.h"

#define ENV_T_REACT      0.30f   /* s  판정→액추에이터 지연 + 측정 지연 */
#define ENV_MARGIN_M     4.0f    /* m  앞차 범퍼까지 남길 거리 */
#define ENV_A_USE        0.90f   /*    표 감속의 90 % 만 믿는다 */
#define ENV_PRE_S        1.5f    /* s  필요 거리 + 이만큼 앞에서 예비 제동 */
#define ENV_PROBE_BRAKE  0.30f
#define ENV_PROBE_SETTLE 0.20f   /* s  탭 전에 가속 페달만 뗀다 (헛돌던 바퀴가 먼저 노면 속도로 돌아오게) */
#define ENV_PROBE_S      0.60f   /* s  뗌 0.2 + 제동 0.4 */
#define ENV_PROBE_WIN_S  0.25f
#define ENV_DRY_FRAC     0.92f   /* 탭 감속이 마른 노면 값의 92 % 이상이면 '이상 없음' */
#define ENV_NO_GAP       900.f
#define ENV_PRE_KI       0.8f    /* 1/(m/s²·s)  예비 제동 적분 이득 */
#define ENV_PRE_KP       0.25f   /* 1/(m/s²)    예비 제동 비례 이득 */
#define ENV_PRE_THR      0.30f   /*             예비 제동 중 허용하는 최대 가속 페달 */

enum { ENV_CRUISE = 0, ENV_PROBE = 1, ENV_CAUTION = 2, ENV_PREBRAKE = 3, ENV_BRAKE = 4, ENV_HOLD = 5 };

typedef struct {
  float mu;                 /* 지금 쓰는 마찰 */
  int   mode;
  float probe_t, probe_sum; int probe_n;
  float mu_probe;           /* 마지막 탭 결과 (-1 = 아직 없음) */
  int   probe_retry;        /* 측정이 표 범위 밖이면 한 번 다시 잰다 */
  float d_stop, d_need, v_max, a_used;
  float b_pre;              /* 예비 제동 세기 (실측 감속을 필요 감속에 맞춰 적분) */
} env_t;

static float env_interp(const float *x, const float *y, float q)
{
  if (q <= x[0]) return y[0];
  for (int i = 1; i < ENV_NMU; i++)
    if (q <= x[i]) { float w = (q - x[i - 1]) / (x[i] - x[i - 1]); return y[i - 1] + w * (y[i] - y[i - 1]); }
  return y[ENV_NMU - 1];
}
static inline float env_decel(float mu) { return env_interp(env_mu, env_dec_full, mu); }
static inline float env_mu_from_probe(float dec) { return env_interp(env_dec_probe, env_mu, dec); }

static void env_reset(env_t *e, float mu0)
{
  e->mu = mu0; e->mode = ENV_CRUISE; e->probe_t = 0.f; e->probe_sum = 0.f; e->probe_n = 0; e->mu_probe = -1.f; e->probe_retry = 0;
  e->d_stop = e->d_need = 0.f; e->v_max = 99.f; e->a_used = 0.f; e->b_pre = 0.f;
}
static void env_probe_start(env_t *e) { if (e->mode != ENV_PROBE) { e->mode = ENV_PROBE; e->probe_t = 0.f; e->probe_sum = 0.f; e->probe_n = 0; e->probe_retry = 0; } }
/* 2차 방어의 다른 경로(미끄러짐 확정 후 마찰 추정기)가 더 낮은 μ 를 알면 받아 쓴다 */
static void env_set_mu(env_t *e, float mu) { e->mu = mu; }

/* v: 속도 m/s, ax: 종가속 m/s² (가속 +), gap: 앞 물체 범퍼 간격 m (없으면 ≥ ENV_NO_GAP), v_lead: 앞 물체 속도 m/s
   → *brake 0..1, *thr_cap 0..1 (가속 페달 상한. 예비 제동 중에는 이 값이 곧 가속 페달 명령이다) */
static void env_step(env_t *e, float dt, float v, float ax, float gap, float v_lead, float *brake, float *thr_cap)
{
  float a = ENV_A_USE * env_decel(e->mu); e->a_used = a;
  if (v_lead < 0.f) v_lead = 0.f;
  e->d_stop = v * ENV_T_REACT + v * v / (2.f * a);
  e->d_need = (v > v_lead ? v * ENV_T_REACT + (v * v - v_lead * v_lead) / (2.f * a) : 0.f) + ENV_MARGIN_M;
  if (gap < ENV_NO_GAP) {   /* v·T + v²/2a + 여유 ≤ gap + v_lead²/2a 를 v 에 대해 푼다 */
    float room = gap - ENV_MARGIN_M + v_lead * v_lead / (2.f * a); if (room < 0.f) room = 0.f;
    float aT = a * ENV_T_REACT;
    e->v_max = -aT + sqrtf(aT * aT + 2.f * a * room);
  } else e->v_max = 99.f;

  if (e->mode == ENV_PROBE) {
    e->probe_t += dt;
    if (e->probe_t > ENV_PROBE_S - ENV_PROBE_WIN_S) { e->probe_sum += -ax; e->probe_n++; }
    if (e->probe_t >= ENV_PROBE_S) {
      float dec = e->probe_n ? e->probe_sum / (float)e->probe_n : env_dec_probe[ENV_NMU - 1];
      if (dec < 0.8f * env_dec_probe[0] && e->probe_retry == 0) {      /* 표의 최저 마찰보다도 덜 감속 = 측정 실패 (바퀴 헛돎 등) */
        e->probe_retry = 1; e->probe_t = 0.f; e->probe_sum = 0.f; e->probe_n = 0;
        *brake = 0.f; *thr_cap = 0.f; return;
      }
      if (dec >= ENV_DRY_FRAC * env_dec_probe[ENV_NMU - 1]) e->mu_probe = env_mu[ENV_NMU - 1];
      else { e->mu_probe = env_mu_from_probe(dec); if (e->mu_probe < e->mu) e->mu = e->mu_probe; }
      e->mode = ENV_CRUISE;
    } else { *brake = e->probe_t < ENV_PROBE_SETTLE ? 0.f : ENV_PROBE_BRAKE; *thr_cap = 0.f; return; }
  }
  if (e->mode == ENV_HOLD || (v < 0.3f && gap < 3.f * ENV_MARGIN_M)) { e->mode = ENV_HOLD; *brake = 1.f; *thr_cap = 0.f; return; }
  if (gap <= e->d_need) { e->mode = ENV_BRAKE; *brake = 1.f; *thr_cap = 0.f; return; }
  /* 예비·전력 제동은 한 번 들어가면 정지할 때까지 풀지 않는다 (경계에서 순항↔제동이 떨리지 않게) */
  int latched = (e->mode == ENV_PREBRAKE || e->mode == ENV_BRAKE) && gap < ENV_NO_GAP;
  if (latched || gap <= e->d_need + v * ENV_PRE_S) {
    /* 남은 거리에 딱 맞춰 서는 감속 a_req 를 따라간다. 빙판에서는 작은 제동으로도 감속이 포화되므로
       세기를 계산값으로 바로 넣지 않고, 실측 감속(−ax)과의 차이로 적분한다 (너무 일찍 서지 않게). */
    float room = gap - ENV_MARGIN_M - v * ENV_T_REACT; if (room < 1.f) room = 1.f;
    float a_req = (v * v - v_lead * v_lead) / (2.f * room);
    if (e->mode != ENV_PREBRAKE) e->b_pre = 0.f;
    float err = a_req - (-ax);
    e->b_pre += ENV_PRE_KI * dt * err;
    /* 음수 = 가속 페달: 발을 떼기만 해도 엔진 브레이크(빙판에서도 약 1.2 m/s²)가 필요 감속보다 크면 너무 일찍 선다 */
    if (e->b_pre < -ENV_PRE_THR) e->b_pre = -ENV_PRE_THR; if (e->b_pre > 1.f) e->b_pre = 1.f;
    float u = e->b_pre + ENV_PRE_KP * err;
    if (u > 1.f) u = 1.f; if (u < -ENV_PRE_THR) u = -ENV_PRE_THR;
    e->mode = ENV_PREBRAKE; *brake = u > 0.f ? u : 0.f; *thr_cap = u < 0.f ? -u : 0.f; return;
  }
  if (v > e->v_max) { e->mode = ENV_CAUTION; *brake = 0.f; *thr_cap = 0.f; return; }
  e->mode = ENV_CRUISE; *brake = 0.f; *thr_cap = 1.f;
}
#endif
