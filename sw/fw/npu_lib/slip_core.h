/* IcePredict 2차 방어 — IMU 미끄러짐 감지 + 비상 제어 코어 (순수 C, HAL/OS 의존 없음).
   sw/src/icepredict/pi/imu_slip.py 의 참조 구현과 같은 알고리즘·상수:
     - 2상태 칼만 필터(값, 변화율) 상수속도 모델, H=[1 0]
     - 자전거 모델 기대 yaw rate = v·tan(δ)/L 로 정상 코너링 성분을 뺀 잔차
     - (ay_g/0.3)² + (yaw_err/0.35)² >= 1 이 N샘플 연속이면 확정, 정상 복귀 전 재발화 금지
       (SLIP_RULE_ELLIPSE=0 이면 예전처럼 |ay_g|>=0.3 또는 |yaw_err|>=0.35 인 직사각형 판정)
     - (추가) 저마찰 감지: 제동 명령 > 0.3 인데 감속이 1.2 m/s² 미만이면 바퀴가 미끄러지는 것 (직선 빙판에서 제동할 때)
   비상 제어(정지까지 매 샘플): 차선 유지 조향 + 잠김 방지 펄스 제동, 앞차 간격 대비 정지 가능 거리로 회피 판단.
   보드(Cortex-M55, ThreadX) 와 호스트 테스트(gcc)에서 그대로 컴파일된다. 부동소수점 단정도. */
#ifndef SLIP_CORE_H
#define SLIP_CORE_H
#include <math.h>
#include <string.h>
#include <stdint.h>

typedef struct { float x0, x1, p00, p01, p10, p11, dt, q, r; int init; } kf2_t;

static inline void kf2_init(kf2_t *k, float dt, float q, float r)
{ memset(k, 0, sizeof(*k)); k->p00 = 1.f; k->p11 = 1.f; k->dt = dt; k->q = q; k->r = r; }

static inline float kf2_step(kf2_t *k, float z)
{
  if (!k->init) { k->x0 = z; k->init = 1; return z; }
  const float dt = k->dt, d2 = dt * dt, d3 = d2 * dt, d4 = d3 * dt;
  /* 예측: x = F x, P = F P Fᵀ + Q   (F = [[1,dt],[0,1]]) */
  float x0 = k->x0 + dt * k->x1, x1 = k->x1;
  float p00 = k->p00 + dt * (k->p10 + k->p01) + d2 * k->p11 + k->q * d4 * 0.25f;
  float p01 = k->p01 + dt * k->p11 + k->q * d3 * 0.5f;
  float p10 = k->p10 + dt * k->p11 + k->q * d3 * 0.5f;
  float p11 = k->p11 + k->q * d2;
  /* 갱신: K = P Hᵀ / S,  x += K y,  P = (I - K H) P */
  float y = z - x0, S = p00 + k->r, k0 = p00 / S, k1 = p10 / S;
  k->x0 = x0 + k0 * y;  k->x1 = x1 + k1 * y;
  k->p00 = (1.f - k0) * p00;  k->p01 = (1.f - k0) * p01;
  k->p10 = p10 - k1 * p00;    k->p11 = p11 - k1 * p01;
  return k->x0;
}

#define SLIP_WHEELBASE_M   2.7f
#define SLIP_V_CH_MPS      17.0f         /* 특성속도: 고속에서 언더스티어로 실제 yaw rate 가 운동학값보다 작다 (1/(1+(v/v_ch)²) 보정).
                                            60 km/h 곡선에서 운동학 모델만 쓰면 잔차 0.3 g 로 오탐이 났다. 값은 주행 로그로 보정한다. */
#define SLIP_MAX_STEER_RAD 0.61086524f   /* 35° : CARLA steer(-1~1) → 조향각 */
/* 자전거 모델은 조향에 차량이 **즉시** 반응한다고 가정한다. 실제로는 타이어·관성 때문에
   요 응답이 뒤따라온다. 빙판 없는 대조군 50 km/h 주행에서 조향이 80 ms 만에 0.07 → 0.66 으로
   튀자 모델은 yaw 1.02 rad/s 를 기대했는데 실제는 0.35 였고, 그 지연이 잔차로 잡혀
   **2차 방어가 정상 노면에서 발화했다**. 미끄러진 게 아니라 아직 안 돌아간 것뿐이다.
   그래서 기대 yaw 를 1차 지연으로 통과시켜 차량의 실제 응답 속도에 맞춘다.
   0.06 s 는 실측으로 고른 값이다 (대조군 5주행 여유 0.59, 빙판 탐지 지연 +0.02~0.56 s).
   더 키우면 여유가 줄고 탐지가 느려진다 — 0.08 부터 35 km/h 가 4.56 → 5.06 s 로 뛴다. */
#define SLIP_YAWEXP_TAU_S  0.06f
#define SLIP_LAT_THR_G     0.3f
#define SLIP_YAW_THR       0.35f
#define SLIP_LOWMU_BRAKE   0.3f          /* 이 이상 제동 중인데 */
#define SLIP_LOWMU_DECEL   1.2f          /* 감속이 이보다 작으면 저마찰 (m/s²) */
#define SLIP_G             9.80665f
#define SLIP_TRIG_LAT      1u
#define SLIP_TRIG_YAW      2u
#define SLIP_TRIG_LOWMU    3u
#define SLIP_TRIG_COMBO    4u         /* 둘 다 임계 아래지만 합쳐서 넘었다 (타원 판정) */

/* 판정 형태: 0 = 직사각형(|ay_g|≥thr 또는 |yaw_err|≥thr), 1 = 타원.
   왜 바꿨나: 35 km/h 빙판 주행에서 ay_g 가 0.287 까지 올라가고 멈췄다. 임계 0.300 을 4 % 차이로
   못 넘어, 훨씬 느린 yaw 경로가 0.353 에 닿을 때까지 3.2 s 를 더 기다렸다. 직사각형 판정의
   모서리에 딱 걸린 것이다. 타원은 한쪽만 넘어도 발화하는 성질은 그대로 두고(정의상 절대 늦어지지
   않는다) 둘 다 0.71 쯤인 구간을 추가로 잡는다. 새 상수도, 속도 보정도 필요 없다.
   비용: 곱 2 + 합 1. 분기 수는 그대로다. */
#define SLIP_RULE_ELLIPSE  1

/* ---- 마찰 추정 (SLIP_MU_EST, 기본 0 = 기존 동작 그대로) ----
   마찰원: 바퀴가 한계에 있으면 낼 수 있는 가속도 합이 곧 μ·g 다 → μ ≈ √(ax_f² + ay_f²)/g.
   한계에 있다는 보장이 있는 샘플만 쓴다: 미끄러짐 확정 이후 + 펄스 '강(high)' 구간 + v > 2 m/s.
   시작값 0.05(가장 미끄러운 쪽), 범위 [0.02, 1.0]. 과대추정이 더 위험하므로(정지거리를 짧게 잡아 회피가 늦어진다)
   올라갈 때는 느리게(α_up), 내려갈 때는 빠르게(α_dn) 따라간다. 연산: 곱 6 + 제곱근 1 + 비교 몇 개, 고정 횟수. */
#ifndef SLIP_MU_EST
#define SLIP_MU_EST        0
#endif
#define SLIP_MU_INIT       0.05f
#define SLIP_MU_MIN        0.02f
#define SLIP_MU_MAX        1.0f
#define SLIP_MU_ALPHA_UP   0.08f        /* 샘플당 — 50 Hz 에서 시상수 약 0.25 s */
#define SLIP_MU_ALPHA_DN   0.35f
#define SLIP_MU_VMIN       2.0f
#define SLIP_MU_SKIP       2            /* 펄스가 강으로 바뀐 직후 몇 샘플은 제동력이 오르는 중이라 뺀다 */
#ifndef SLIP_GAIN_SCHED
#define SLIP_GAIN_SCHED    0
#endif
/* ---- 호스트 감시 (SLIP_WDT, 기본 0) ----
   보드는 라즈베리파이(리눅스)를 지키는 안전 전용 RTOS가다. IMU 는 보드에 직결되고(실차 설계), 차선·앞차 간격은 파이가 카메라로 만들어 보낸다.
   파이 패킷이 SLIP_WDT_TIMEOUT 샘플(50 Hz 에서 100 ms) 넘게 끊기면 축소 모드: 낡은 차선·간격 입력을 버리고,
   조향 명령을 내지 않으며(운전자 몫), 회피 판단 없이 마찰 추정에 맞춘 제동 조절만 계속한다. 패킷이 다시 오면 정상으로 돌아간다.
   추가 연산: 카운터 증가·비교 1회. */
#ifndef SLIP_WDT
#define SLIP_WDT           0
#endif
#define SLIP_WDT_TIMEOUT   5

/* ---- 마찰별 제어표 (SLIP_GAIN_SCHED, 기본 0 = 기존 고정 게인 그대로) ----
   μ̂(마찰 추정)·속도로 slip_gain_lut.h 의 표를 쌍선형 보간해 이번 샘플의 제어 파라미터 10개를 얻는다.
   표는 시뮬레이션(대리 모델 탐색 → CARLA 검증)에서 미리 만들고 보드는 찾아 쓰기만 한다.
   추가 연산: 구간 찾기 비교 ≤ 14, 보간 곱 30·합 30 (파라미터 10개 × 3), 고정 횟수 — 동적 메모리·가변 루프 없음.
   값의 순서: Kp, Kl, Kd, steer_max, B_HI, B_LO, PULSE_HALF, B_EVADE, a_stop_scale, margin */
#if SLIP_GAIN_SCHED
#if !SLIP_MU_EST
#error "SLIP_GAIN_SCHED 는 SLIP_MU_EST 가 있어야 한다 (표의 축이 μ̂ 다)"
#endif
#include "slip_gain_lut.h"
enum { SG_KP, SG_KL, SG_KD, SG_SMAX, SG_BHI, SG_BLO, SG_PH, SG_BEV, SG_ASC, SG_MARGIN, SG_N };
#endif

/* 비상 제어 모드 */
#define SLIP_MODE_NONE     0u
#define SLIP_MODE_LANEKEEP 1u            /* 차선 유지 + 펄스 제동 */
#define SLIP_MODE_EVADE_L  2u            /* 왼쪽 빈 차로로 회피 */
#define SLIP_MODE_EVADE_R  3u            /* 오른쪽 빈 차로로 회피 */
#define SLIP_MODE_HARDSTOP 4u            /* 갈 곳 없음: 차선 유지 + 최대 제동 */
#define SLIP_MODE_STOPPED  5u

#define SLIP_LOWMU_HOLD    15            /* 제동 명령이 이만큼 연속(50Hz→0.3s)된 뒤에만 저마찰 평가 — 필터 지연 구간 오탐 방지 */
#define SLIP_LOWMU_CONFIRM 8
typedef struct {
  kf2_t kay, kyaw, kax;
  int hits, armed, confirm; float dt, min_speed; int ready;
  int brake_n, lowmu_hits;
  int emerg; uint32_t n_emerg; uint8_t mode; float lat_shift;   /* lat_shift: 회피 시 차선 중심 목표 이동(m) */
  float ye; int ye_init;                                         /* 기대 yaw 의 1차 지연 상태 */
  int grip_n, grip;                                              /* 접지 회복: 제동 중 감속이 살아난 샘플 수 / 플래그 */
  float a_stop_used;                                             /* 진단용: 이번 샘플 정지거리 계산에 쓴 감속 (판단에는 안 쓰인다) */
#if SLIP_MU_EST
  float mu_hat; int mu_n; int pulse_hi_n;                        /* 마찰 추정값, 쓴 샘플 수, 펄스 강 구간 연속 샘플 수 */
  float b_hi_last;                                               /* 직전 샘플 모드의 '강' 펄스 제동값 — 지금 걸린 제동이 강인지 판단 */
#endif
#if SLIP_WDT
  uint32_t host_age; int degraded;                               /* 마지막 호스트 패킷 이후 샘플 수, 축소 모드 */
#endif
#if SLIP_GAIN_SCHED
  float g[10];                                                   /* 이번 샘플에 쓴 제어 파라미터 (진단·로그) */
  int g_ovr; float g_ovr_v[10];                                  /* 시험용: 표 대신 고정값을 쓴다 (CARLA 에서 후보 하나를 검증할 때) */
#endif
} slipdet_t;

static inline void slip_reset(slipdet_t *d, float dt, int confirm, float min_speed_mps)
{
  kf2_init(&d->kay,  dt, 2000.f, 0.16f);     /* R = ay σ0.4² , Q 크게 = 빠른 추종 (파이썬과 동일) */
  kf2_init(&d->kyaw, dt, 3000.f, 0.0005f);   /* R = gyro σ0.02² */
  kf2_init(&d->kax,  dt, 2000.f, 0.16f);
  d->hits = 0; d->armed = 1; d->confirm = confirm > 0 ? confirm : 3;
  d->dt = dt; d->min_speed = min_speed_mps > 0.f ? min_speed_mps : 2.0f; d->ready = 1;
  d->emerg = 0; d->n_emerg = 0; d->mode = SLIP_MODE_NONE; d->lat_shift = 0.f; d->brake_n = 0; d->lowmu_hits = 0;
  d->grip_n = 0; d->grip = 0; d->a_stop_used = 0.f;
#if SLIP_MU_EST
  d->mu_hat = SLIP_MU_INIT; d->mu_n = 0; d->pulse_hi_n = 0; d->b_hi_last = 0.7f;
#endif
#if SLIP_GAIN_SCHED
  memset(d->g, 0, sizeof(d->g)); d->g_ovr = 0;
#endif
#if SLIP_WDT
  d->host_age = 0; d->degraded = 0;
#endif
  d->ye = 0.f; d->ye_init = 0;
}

/* 한 샘플 처리. 확정 시 1을 돌려주고 trig/ay_g/yaw_err 를 채운다. (ax, brake_cmd 는 저마찰 감지용; 없으면 0) */
static inline int slip_step2(slipdet_t *d, float ay, float gz, float speed, float steer, float ax, float brake_cmd,
                             uint8_t *trig, float *ay_g_out, float *yaw_err_out)
{
  float ay_f = kf2_step(&d->kay, ay), yaw_f = kf2_step(&d->kyaw, gz), ax_f = kf2_step(&d->kax, ax);
  *trig = 0u; *ay_g_out = 0.f; *yaw_err_out = 0.f;
  if (speed < d->min_speed) { d->hits = 0; return 0; }
  float delta = steer * SLIP_MAX_STEER_RAD;
  float yaw_raw = speed * tanf(delta) / SLIP_WHEELBASE_M / (1.f + (speed / SLIP_V_CH_MPS) * (speed / SLIP_V_CH_MPS));
  /* 차량 요 응답 지연 보정 — 급조향 순간의 모델 오차를 미끄러짐으로 오인하지 않게 한다 */
  if (!d->ye_init) { d->ye = yaw_raw; d->ye_init = 1; }
  else { float k = d->dt / (SLIP_YAWEXP_TAU_S + d->dt); d->ye += k * (yaw_raw - d->ye); }
  float yaw_exp = d->ye;
  float ay_g = (ay_f - speed * yaw_exp) / SLIP_G;      /* 조향으로 설명되는 원심가속도 제거 → 잔차 */
  float yaw_err = yaw_f - yaw_exp;
  *ay_g_out = ay_g; *yaw_err_out = yaw_err;
  int lat = fabsf(ay_g) >= SLIP_LAT_THR_G, yaw = fabsf(yaw_err) >= SLIP_YAW_THR;
#if SLIP_RULE_ELLIPSE
  float nl = ay_g / SLIP_LAT_THR_G, ny = yaw_err / SLIP_YAW_THR;
  int hit = (nl * nl + ny * ny) >= 1.0f;
#else
  int hit = lat || yaw;
#endif
  d->brake_n = (brake_cmd >= SLIP_LOWMU_BRAKE) ? d->brake_n + 1 : 0;
  int lowmu_now = (d->brake_n >= SLIP_LOWMU_HOLD) && (fabsf(ax_f) < SLIP_LOWMU_DECEL);
  d->lowmu_hits = lowmu_now ? d->lowmu_hits + 1 : 0;
  if (hit) d->hits++; else { d->hits = 0; if (!lowmu_now) d->armed = 1; }
  if (d->armed && (d->hits >= d->confirm || d->lowmu_hits >= SLIP_LOWMU_CONFIRM)) {
    d->armed = 0;                                       /* 정상 복귀 전까지 재발화 금지 */
    *trig = lat ? SLIP_TRIG_LAT : (yaw ? SLIP_TRIG_YAW : (hit ? SLIP_TRIG_COMBO : SLIP_TRIG_LOWMU));
    return 1;
  }
  return 0;
}

/* 참조 구현과의 동치 검증용 (ax/brake 없음). 확정 시 파이썬 emergency_command() 와 같은 초기 명령도 돌려준다. */
static inline int slip_step(slipdet_t *d, float ay, float gz, float speed, float steer,
                            uint8_t *trig, float *ay_g_out, float *yaw_err_out, float *brake, float *steer_cmd)
{
  int r = slip_step2(d, ay, gz, speed, steer, 0.f, 0.f, trig, ay_g_out, yaw_err_out);
  *brake = 0.f; *steer_cmd = 0.f;
  if (r) { *brake = 0.6f; *steer_cmd = (*yaw_err_out != 0.f) ? -copysignf(5.0f / 35.0f, *yaw_err_out) : 0.f; }
  return r;
}

/* ---- 비상 제어기: 미끄러짐 확정 후 정지까지 매 샘플 호출 ----
   입력(차량 센서를 대신하는 값): lane_err 차선 진행 방향 − 차량 방향 (rad, CARLA yaw 시계방향 양수 → 양수면 우조향)
                                 lat_off  차선 중심의 차량 기준 횡위치 (m, 오른쪽 양수)
                                 gap_front 같은 차로 앞차까지 (m, 없으면 999)
                                 gap_left/right 옆 차로 가장 가까운 차까지 (m, 없으면 999, 차로 없음 -1)
   판단: 빙판 위 정지 가능 거리 d_stop = v²/(2·a_ice) (a_ice ≈ 1.0 m/s²). 앞차 간격이 그보다 짧으면
         비어 있는 옆 차로(간격 ≥ 20 m)로 회피 — 오른쪽 우선. 갈 곳이 없으면 차선 유지 + 최대 제동.
   조향: Kp·lane_err + Kl·(lat_off + 회피 이동) − Kd·yaw_rate  → steer 정규화(/35°), ±0.5
   제동: 잠김 방지 펄스(ABS 흉내). 잠긴 바퀴는 조향이 안 듣는다. 회피 중에는 조향권 확보를 위해 약하게. */
#define SLIP_KP        1.4f
#define SLIP_KL        0.15f
#define SLIP_KD        0.30f
#define SLIP_STEER_MAX 0.5f
#define SLIP_A_ICE     1.0f
#define SLIP_GAP_FREE  20.f
#define SLIP_LANE_W    3.5f
/* v7 추가 입력:
     ax_f          제동 중 종가속(필터) — 감속이 살아나면(-2.5 m/s² 이상, 5샘플) '접지 회복' = 빙판을 벗어났다 → 회피 금지, 마른 노면 제동거리 사용
     ice_lane_off  미끄러짐이 시작된 차로 중심의 현재 횡위치 (m, 오른쪽 양수) — 그쪽으로는 회피하지 않는다 (빙판으로 되돌아가는 결함 방지)
     front_rel_v   앞차 상대속도 (앞차−자차, m/s) — 멀어지는 차(≥ +1)에는 회피하지 않는다. gap_front<0(겹침)이면 회피 대신 최대 제동 */
#define SLIP_A_DRY     6.0f
#define SLIP_GRIP_DECEL 2.5f
#define SLIP_GRIP_N    5
#if SLIP_GAIN_SCHED
/* 축 위치 찾기: x 가 axis[i]..axis[i+1] 사이면 i 와 비율 t. 축 밖은 끝값으로 고정 (외삽하지 않는다). */
static inline void slip_axis_find(const float *axis, int n, float x, int *i, float *t)
{
  if (x <= axis[0]) { *i = 0; *t = 0.f; return; }
  if (x >= axis[n - 1]) { *i = n - 2; *t = 1.f; return; }
  int k = 0;
  for (int j = 1; j < n - 1; j++) k += (x >= axis[j]);       /* 고정 횟수 비교 (n−2 번) */
  *i = k; *t = (x - axis[k]) / (axis[k + 1] - axis[k]);
}
/* μ 축은 로그로 보간한다 (0.02 ~ 0.8 로 40 배 넓다). logf 1 회. */
static inline void slip_gain_lookup(slipdet_t *d, float speed_mps)
{
  if (d->g_ovr) { for (int k = 0; k < SG_N; k++) d->g[k] = d->g_ovr_v[k]; return; }
  int im, iv; float tm, tv;
  float lm = logf(d->mu_hat > SLIP_GS_MU[0] ? d->mu_hat : SLIP_GS_MU[0]);
  slip_axis_find(SLIP_GS_LOGMU, SLIP_GS_NMU, lm, &im, &tm);
  slip_axis_find(SLIP_GS_KPH, SLIP_GS_NV, speed_mps * 3.6f, &iv, &tv);
  for (int k = 0; k < SG_N; k++) {
    float a = SLIP_GS_TABLE[im][iv][k],     b = SLIP_GS_TABLE[im][iv + 1][k];
    float c = SLIP_GS_TABLE[im + 1][iv][k], e = SLIP_GS_TABLE[im + 1][iv + 1][k];
    float lo = a + (b - a) * tv, hi = c + (e - c) * tv;
    d->g[k] = lo + (hi - lo) * tm;
  }
}
#endif

#if SLIP_MU_EST
/* 비상 제어 중 매 샘플: 이번 샘플의 제동이 '강' 펄스면 마찰원으로 μ 를 갱신한다.
   ax_f, ay_f 는 칼만 필터 값(m/s²), brake_cmd 는 지금 차에 걸려 있는 제동(직전 샘플 명령). */
static inline void slip_mu_update(slipdet_t *d, float speed, float ax_f, float ay_f, float brake_cmd, float b_hi)
{
  if (brake_cmd >= b_hi - 1e-3f) d->pulse_hi_n++; else d->pulse_hi_n = 0;
  if (d->pulse_hi_n <= SLIP_MU_SKIP || speed <= SLIP_MU_VMIN) return;
  float m = sqrtf(ax_f * ax_f + ay_f * ay_f) / SLIP_G;
  if (m < SLIP_MU_MIN) m = SLIP_MU_MIN;
  if (m > SLIP_MU_MAX) m = SLIP_MU_MAX;
  float a = (m > d->mu_hat) ? SLIP_MU_ALPHA_UP : SLIP_MU_ALPHA_DN;
  d->mu_hat += a * (m - d->mu_hat);
  d->mu_n++;
}
#endif

#if SLIP_WDT
/* 매 샘플 제어 전에 부른다. host_fresh = 이번 샘플에 파이의 차선·간격 패킷이 새로 왔는가. */
static inline void slip_host_watchdog(slipdet_t *d, int host_fresh)
{
  d->host_age = host_fresh ? 0u : d->host_age + 1u;
  d->degraded = d->host_age > (uint32_t)SLIP_WDT_TIMEOUT;
}
#endif

static inline void slip_control2(slipdet_t *d, float speed, float gz, float lane_err, float lat_off,
                                 float gap_front, float gap_left, float gap_right,
                                 float ax_f, float brake_cmd, float ice_lane_off, float front_rel_v, float *brake, float *steer)
{
  d->n_emerg++;
#if SLIP_WDT
  if (d->degraded) {                    /* 낡은 차선·간격은 버린다: 조향 없음, 앞차 모름 → 회피 없이 최대제동형 제동 조절 */
    lane_err = 0.f; lat_off = 0.f; gz = 0.f; gap_front = -1.f; gap_left = -1.f; gap_right = -1.f; d->lat_shift = 0.f;
  }
#endif
#if SLIP_MU_EST
  slip_mu_update(d, speed, ax_f, d->kay.x0, brake_cmd, d->b_hi_last);
#endif
  if (brake_cmd >= 0.3f && ax_f <= -SLIP_GRIP_DECEL) { if (++d->grip_n >= SLIP_GRIP_N) d->grip = 1; } else d->grip_n = 0;
#if SLIP_GAIN_SCHED
  slip_gain_lookup(d, speed);
  float a_ice = d->g[SG_ASC] * d->mu_hat * SLIP_G;
  if (a_ice < 0.15f) a_ice = 0.15f;
  float a_stop = d->grip ? SLIP_A_DRY : a_ice;
  float d_stop = speed * speed / (2.f * a_stop) + d->g[SG_MARGIN];
#else
  float a_stop = d->grip ? SLIP_A_DRY : SLIP_A_ICE;
  float d_stop = speed * speed / (2.f * a_stop) + 4.f;
#endif
  d->a_stop_used = a_stop;
  int overlap = gap_front < 0.f;
  int cannot_stop = !overlap && gap_front < d_stop && front_rel_v < 1.0f;
  if (d->mode == SLIP_MODE_NONE || d->mode == SLIP_MODE_LANEKEEP || d->mode == SLIP_MODE_HARDSTOP) {
    if (overlap) { d->mode = SLIP_MODE_HARDSTOP; d->lat_shift = 0.f; }
    else if (cannot_stop && !d->grip) {
      /* 회피 후보: 비어 있고(≥20 m), 미끄러진 차로로 되돌아가지 않는 쪽. 빙판 차로에서 먼 쪽을 먼저 본다. */
      int r_ok = gap_right >= SLIP_GAP_FREE && fabsf(+SLIP_LANE_W - ice_lane_off) > 1.75f;
      int l_ok = gap_left  >= SLIP_GAP_FREE && fabsf(-SLIP_LANE_W - ice_lane_off) > 1.75f;
      int prefer_left = ice_lane_off > 0.5f;              /* 빙판 차로가 오른쪽에 있으면 왼쪽 우선 */
      if (prefer_left ? l_ok : r_ok)      { d->mode = prefer_left ? SLIP_MODE_EVADE_L : SLIP_MODE_EVADE_R; d->lat_shift = prefer_left ? -SLIP_LANE_W : +SLIP_LANE_W; }
      else if (prefer_left ? r_ok : l_ok) { d->mode = prefer_left ? SLIP_MODE_EVADE_R : SLIP_MODE_EVADE_L; d->lat_shift = prefer_left ? +SLIP_LANE_W : -SLIP_LANE_W; }
      else                                { d->mode = SLIP_MODE_HARDSTOP; d->lat_shift = 0.f; }
    }
    else if (cannot_stop && d->grip) { d->mode = SLIP_MODE_HARDSTOP; d->lat_shift = 0.f; }   /* 접지가 있으면 세게 밟는 게 회피보다 낫다 */
    else { d->mode = SLIP_MODE_LANEKEEP; d->lat_shift = 0.f; }
  }
#if SLIP_GAIN_SCHED
  /* 제어표 경로: 같은 구조, 값만 표에서. 최대제동의 강 펄스는 B_HI × 1.357 (보드 고정값 0.95/0.70 의 비율), 상한 1. */
  float st = (d->g[SG_KP] * lane_err + d->g[SG_KL] * (lat_off + d->lat_shift) - d->g[SG_KD] * gz) / SLIP_MAX_STEER_RAD;
  if (st > d->g[SG_SMAX]) st = d->g[SG_SMAX];
  if (st < -d->g[SG_SMAX]) st = -d->g[SG_SMAX];
  int ph = (int)(d->g[SG_PH] + 0.5f); if (ph < 1) ph = 1;
  int pulse = (int)(d->n_emerg / (uint32_t)ph) & 1;
  float b_hi = d->g[SG_BHI], b_lo = d->g[SG_BLO], hs_hi = b_hi * 1.357f; if (hs_hi > 1.f) hs_hi = 1.f;
  float b;
  switch (d->mode) {
    case SLIP_MODE_EVADE_L: case SLIP_MODE_EVADE_R:
      b = fabsf(lat_off + d->lat_shift) > 0.8f ? d->g[SG_BEV] : (pulse ? b_hi : b_lo); break;
    case SLIP_MODE_HARDSTOP: b = d->grip ? 1.0f : (pulse ? hs_hi : b_lo); break;
    default:                 b = d->grip ? 0.9f : (pulse ? b_hi : b_lo); break;
  }
  d->b_hi_last = (d->mode == SLIP_MODE_HARDSTOP) ? hs_hi : b_hi;
#else
  float st = (SLIP_KP * lane_err + SLIP_KL * (lat_off + d->lat_shift) - SLIP_KD * gz) / SLIP_MAX_STEER_RAD;
  if (st > SLIP_STEER_MAX) st = SLIP_STEER_MAX;
  if (st < -SLIP_STEER_MAX) st = -SLIP_STEER_MAX;
  int pulse = (d->n_emerg >> 3) & 1u;
  float b;
  switch (d->mode) {
    case SLIP_MODE_EVADE_L: case SLIP_MODE_EVADE_R:
      b = fabsf(lat_off + d->lat_shift) > 0.8f ? 0.25f : (pulse ? 0.7f : 0.35f); break;
    case SLIP_MODE_HARDSTOP: b = d->grip ? 1.0f : (pulse ? 0.95f : 0.5f); break;
    default:                 b = d->grip ? 0.9f : (pulse ? 0.7f : 0.35f); break;      /* 접지 회복 후엔 펄스 없이 강제동 */
  }
#if SLIP_MU_EST
  d->b_hi_last = (d->mode == SLIP_MODE_HARDSTOP) ? 0.95f : 0.7f;
#endif
#endif
  if (speed < 2.0f) b = 1.0f;
  *steer = st; *brake = b;
  if (speed < 0.5f) { d->emerg = 0; d->mode = SLIP_MODE_STOPPED; *brake = 1.0f; *steer = 0.f; }
}

/* (v3 호환) 추가 입력 없이 호출 — 테스트·참조용 */
static inline void slip_control(slipdet_t *d, float speed, float gz, float lane_err, float lat_off,
                                float gap_front, float gap_left, float gap_right, float *brake, float *steer)
{
  slip_control2(d, speed, gz, lane_err, lat_off, gap_front, gap_left, gap_right, 0.f, 0.f, 0.f, 0.f, brake, steer);
  return;
  d->n_emerg++;
  float d_stop = speed * speed / (2.f * SLIP_A_ICE) + 4.f;
  int cannot_stop = gap_front < d_stop;
  /* 모드 결정 (회피는 한 번 정하면 유지 — 미끄러지는 중에 갈팡질팡하면 더 위험) */
  if (d->mode == SLIP_MODE_NONE || d->mode == SLIP_MODE_LANEKEEP || d->mode == SLIP_MODE_HARDSTOP) {
    if (cannot_stop) {
      if (gap_right >= SLIP_GAP_FREE)      { d->mode = SLIP_MODE_EVADE_R; d->lat_shift = +SLIP_LANE_W; }
      else if (gap_left >= SLIP_GAP_FREE)  { d->mode = SLIP_MODE_EVADE_L; d->lat_shift = -SLIP_LANE_W; }
      else                                 { d->mode = SLIP_MODE_HARDSTOP; d->lat_shift = 0.f; }
    } else { d->mode = SLIP_MODE_LANEKEEP; d->lat_shift = 0.f; }
  }
  float st = (SLIP_KP * lane_err + SLIP_KL * (lat_off + d->lat_shift) - SLIP_KD * gz) / SLIP_MAX_STEER_RAD;
  if (st > SLIP_STEER_MAX) st = SLIP_STEER_MAX;
  if (st < -SLIP_STEER_MAX) st = -SLIP_STEER_MAX;
  int pulse = (d->n_emerg >> 3) & 1u;                    /* 16샘플 주기 (50Hz → 약 3Hz) */
  float b;
  switch (d->mode) {
    case SLIP_MODE_EVADE_L: case SLIP_MODE_EVADE_R:
      b = fabsf(lat_off + d->lat_shift) > 0.8f ? 0.25f : (pulse ? 0.7f : 0.35f); break;   /* 차로 옮기는 동안은 조향 우선 */
    case SLIP_MODE_HARDSTOP: b = pulse ? 0.95f : 0.5f; break;
    default:                 b = pulse ? 0.7f : 0.35f; break;
  }
  if (speed < 2.0f) b = 1.0f;                            /* 저속에서는 펄스 없이 확실히 세운다 */
  *steer = st; *brake = b;
  if (speed < 0.5f) { d->emerg = 0; d->mode = SLIP_MODE_STOPPED; *brake = 1.0f; *steer = 0.f; }
}
#endif
