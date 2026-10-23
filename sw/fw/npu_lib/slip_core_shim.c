/* slip_core.h 를 호스트 공유 라이브러리로 내보내는 얇은 껍데기 (ctypes 용).
   보드 펌웨어와 **같은 헤더**를 그대로 컴파일하므로, CARLA 의 --slip-ctypes 주행은 보드가 도는 코드와
   같은 C 연산으로 2차 방어를 돌린다. sc_board() 는 펌웨어 app_netxduo.c 의 IMU 분기(patch_fw_slip.py)와
   같은 순서로 부른다: slip_step2 → (확정 시 emerg=1) → emerg 중이면 slip_control2(ax_f = kax 필터값).
   빌드: make -f Makefile host-shim   (기본 플래그 / SLIP_MU_EST=1 / SLIP_GAIN_SCHED=1 변형을 따로 만든다) */
#include <stddef.h>
#include "slip_core.h"

size_t sc_sizeof(void) { return sizeof(slipdet_t); }

int sc_flags(void)
{
  int f = 0;
#if SLIP_RULE_ELLIPSE
  f |= 1;
#endif
#if defined(SLIP_MU_EST) && SLIP_MU_EST
  f |= 2;
#endif
#if defined(SLIP_GAIN_SCHED) && SLIP_GAIN_SCHED
  f |= 4;
#endif
  return f;
}

void sc_reset(slipdet_t *d, float dt, int confirm, float min_speed) { slip_reset(d, dt, confirm, min_speed); }
/* 재생 검증용: 감지 없이 비상 제어 상태로 만든다 (마찰 추정기를 기록된 제동 펄스로 돌릴 때) */
void sc_force_emerg(slipdet_t *d) { d->emerg = 1; }

int sc_step2(slipdet_t *d, float ay, float gz, float speed, float steer, float ax, float brake_cmd,
             unsigned char *trig, float *ay_g, float *yaw_err)
{ return slip_step2(d, ay, gz, speed, steer, ax, brake_cmd, trig, ay_g, yaw_err); }

void sc_control2(slipdet_t *d, float speed, float gz, float lane_err, float lat_off, float gf, float gl, float gr,
                 float ax_f, float brake_cmd, float ice_off, float front_rel_v, float *brake, float *steer)
{ slip_control2(d, speed, gz, lane_err, lat_off, gf, gl, gr, ax_f, brake_cmd, ice_off, front_rel_v, brake, steer); }

/* 펌웨어 IMU 분기와 같은 한 샘플.
   in [13] = ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gap_front, gap_left, gap_right, ice_lane_off, front_rel_v
   out[12] = slip, trig, ay_g, yaw_err, emerg, brake, steer, mode, grip, mu_hat, mu_n, a_stop */
void sc_board(slipdet_t *d, const float *in, float *out)
{
  unsigned char trig = 0u; float ay_g = 0.f, ye = 0.f, brk = 0.f, st = 0.f;
  int slip = slip_step2(d, in[0], in[1], in[2], in[3], in[4], in[5], &trig, &ay_g, &ye);
  if (slip) d->emerg = 1;
  int emerg = d->emerg;
  if (emerg)
    slip_control2(d, in[2], in[1], in[6], in[7], in[8], in[9], in[10], d->kax.x0, in[5], in[11], in[12], &brk, &st);
  out[0] = (float)slip; out[1] = (float)trig; out[2] = ay_g; out[3] = ye;
  out[4] = (float)emerg; out[5] = brk; out[6] = st; out[7] = (float)d->mode; out[8] = (float)d->grip;
#if defined(SLIP_MU_EST) && SLIP_MU_EST
  out[9] = d->mu_hat; out[10] = (float)d->mu_n;
#else
  out[9] = -1.f; out[10] = 0.f;
#endif
  out[11] = d->a_stop_used;
}
