"""2차 방어 v7 파이썬 참조 구현 — C 코어(sw/fw/npu_lib/slip_core.h)의 slip_step2 + slip_control2 와 같은 논리.

imu_slip.SlipDetector 는 감지만 한다(저마찰·종가속 없음). 여기서는 보드 펌웨어가 한 샘플에 하는 일 전체를 옮긴다:
  slip_step2(ay, gz, speed, steer, ax, brake_cmd) → 확정 시 emerg=1 → emerg 중이면 slip_control2(ax_f = 종가속 칼만값).
CARLA 데모의 로컬 폴백과 동치 테스트가 이 모듈을 쓴다. 예전 v3 제어기(접지 회복·빙판 차로·앞차 상대속도·겹침 없음)는
carla_demo.py 의 LocalEmergencyV3 로 남아 있고 --slip-legacy 로만 쓴다.
"""
from __future__ import annotations

import math

G = 9.80665
MAX_STEER_RAD = 0.61086524           # 35°
WHEELBASE, V_CH, YAW_TAU = 2.7, 17.0, 0.06
LAT_THR, YAW_THR = 0.3, 0.35
LOWMU_BRAKE, LOWMU_DECEL, LOWMU_HOLD, LOWMU_CONFIRM = 0.3, 1.2, 15, 8
KP, KL, KD, SMAX = 1.4, 0.15, 0.30, 0.5
A_ICE, A_DRY, GAP_FREE, LANE_W = 1.0, 6.0, 20.0, 3.5
GRIP_DECEL, GRIP_N = 2.5, 5
MODES = {0: "none", 1: "lane_keep", 2: "evade_left", 3: "evade_right", 4: "hard_stop", 5: "stopped"}
TRIGS = {0: None, 1: "lat_acc", 2: "yaw_rate", 3: "low_mu", 4: "combo"}


class KF2:
    """C kf2_t 와 같은 식 (스칼라 전개)."""
    def __init__(self, dt, q, r):
        self.x0 = self.x1 = 0.0; self.p00 = 1.0; self.p01 = self.p10 = 0.0; self.p11 = 1.0
        self.dt, self.q, self.r, self.init = dt, q, r, False

    def step(self, z):
        if not self.init:
            self.x0 = z; self.init = True; return z
        dt = self.dt; d2 = dt * dt; d3 = d2 * dt; d4 = d3 * dt
        x0 = self.x0 + dt * self.x1; x1 = self.x1
        p00 = self.p00 + dt * (self.p10 + self.p01) + d2 * self.p11 + self.q * d4 * 0.25
        p01 = self.p01 + dt * self.p11 + self.q * d3 * 0.5
        p10 = self.p10 + dt * self.p11 + self.q * d3 * 0.5
        p11 = self.p11 + self.q * d2
        y = z - x0; S = p00 + self.r; k0 = p00 / S; k1 = p10 / S
        self.x0 = x0 + k0 * y; self.x1 = x1 + k1 * y
        self.p00 = (1 - k0) * p00; self.p01 = (1 - k0) * p01
        self.p10 = p10 - k1 * p00; self.p11 = p11 - k1 * p01
        return self.x0


class SlipCoreRef:
    """보드 한 샘플(sc_board) 의 파이썬 판. 상태·상수 이름은 C 와 같다."""

    def __init__(self, dt=0.02, confirm=3, min_speed=2.0):
        self.reset(dt, confirm, min_speed)

    def reset(self, dt, confirm=3, min_speed=2.0):
        self.kay, self.kyaw, self.kax = KF2(dt, 2000.0, 0.16), KF2(dt, 3000.0, 0.0005), KF2(dt, 2000.0, 0.16)
        self.hits = 0; self.armed = True; self.confirm = confirm if confirm > 0 else 3
        self.dt = dt; self.min_speed = min_speed if min_speed > 0 else 2.0
        self.emerg = False; self.n_emerg = 0; self.mode = 0; self.lat_shift = 0.0
        self.brake_n = 0; self.lowmu_hits = 0; self.grip_n = 0; self.grip = False
        self.ye = 0.0; self.ye_init = False; self.a_stop_used = 0.0

    # ---- slip_step2 ----
    def step2(self, ay, gz, speed, steer, ax, brake_cmd):
        ay_f = self.kay.step(ay); yaw_f = self.kyaw.step(gz); ax_f = self.kax.step(ax)
        if speed < self.min_speed:
            self.hits = 0; return 0, 0, 0.0, 0.0
        delta = steer * MAX_STEER_RAD
        yaw_raw = speed * math.tan(delta) / WHEELBASE / (1.0 + (speed / V_CH) * (speed / V_CH))
        if not self.ye_init: self.ye = yaw_raw; self.ye_init = True
        else: self.ye += self.dt / (YAW_TAU + self.dt) * (yaw_raw - self.ye)
        ay_g = (ay_f - speed * self.ye) / G; yaw_err = yaw_f - self.ye
        lat = abs(ay_g) >= LAT_THR; yaw = abs(yaw_err) >= YAW_THR
        nl, ny = ay_g / LAT_THR, yaw_err / YAW_THR
        hit = nl * nl + ny * ny >= 1.0
        self.brake_n = self.brake_n + 1 if brake_cmd >= LOWMU_BRAKE else 0
        lowmu_now = self.brake_n >= LOWMU_HOLD and abs(ax_f) < LOWMU_DECEL
        self.lowmu_hits = self.lowmu_hits + 1 if lowmu_now else 0
        if hit: self.hits += 1
        else:
            self.hits = 0
            if not lowmu_now: self.armed = True
        if self.armed and (self.hits >= self.confirm or self.lowmu_hits >= LOWMU_CONFIRM):
            self.armed = False
            trig = 1 if lat else (2 if yaw else (4 if hit else 3))
            return 1, trig, ay_g, yaw_err
        return 0, 0, ay_g, yaw_err

    # ---- slip_control2 ----
    def control2(self, speed, gz, lane_err, lat_off, gf, gl, gr, ax_f, brake_cmd, ice_off, front_rel_v):
        self.n_emerg += 1
        if brake_cmd >= 0.3 and ax_f <= -GRIP_DECEL:
            self.grip_n += 1
            if self.grip_n >= GRIP_N: self.grip = True
        else: self.grip_n = 0
        a_stop = A_DRY if self.grip else A_ICE
        d_stop = speed * speed / (2.0 * a_stop) + 4.0
        self.a_stop_used = a_stop
        overlap = gf < 0.0
        cannot_stop = (not overlap) and gf < d_stop and front_rel_v < 1.0
        if self.mode in (0, 1, 4):
            if overlap: self.mode, self.lat_shift = 4, 0.0
            elif cannot_stop and not self.grip:
                r_ok = gr >= GAP_FREE and abs(+LANE_W - ice_off) > 1.75
                l_ok = gl >= GAP_FREE and abs(-LANE_W - ice_off) > 1.75
                prefer_left = ice_off > 0.5
                if (l_ok if prefer_left else r_ok):
                    self.mode, self.lat_shift = (2, -LANE_W) if prefer_left else (3, +LANE_W)
                elif (r_ok if prefer_left else l_ok):
                    self.mode, self.lat_shift = (3, +LANE_W) if prefer_left else (2, -LANE_W)
                else: self.mode, self.lat_shift = 4, 0.0
            elif cannot_stop and self.grip: self.mode, self.lat_shift = 4, 0.0
            else: self.mode, self.lat_shift = 1, 0.0
        st = (KP * lane_err + KL * (lat_off + self.lat_shift) - KD * gz) / MAX_STEER_RAD
        st = max(-SMAX, min(SMAX, st))
        pulse = (self.n_emerg >> 3) & 1
        if self.mode in (2, 3): b = 0.25 if abs(lat_off + self.lat_shift) > 0.8 else (0.7 if pulse else 0.35)
        elif self.mode == 4: b = 1.0 if self.grip else (0.95 if pulse else 0.5)
        else: b = 0.9 if self.grip else (0.7 if pulse else 0.35)
        if speed < 2.0: b = 1.0
        if speed < 0.5:
            self.emerg = False; self.mode = 5; b = 1.0; st = 0.0
        return b, st

    # ---- 펌웨어 IMU 분기와 같은 한 샘플 ----
    def board(self, ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v):
        slip, trig, ay_g, ye = self.step2(ay, gz, speed, steer, ax, brake_cmd)
        if slip: self.emerg = True
        emerg = self.emerg; b = st = 0.0
        if emerg:
            b, st = self.control2(speed, gz, lane_err, lat_off, gf, gl, gr, self.kax.x0, brake_cmd, ice_off, front_rel_v)
        return dict(slip=slip, trig=trig, ay_g=ay_g, yaw_err=ye, emerg=int(emerg), brake=b, steer=st,
                    mode=self.mode, grip=int(self.grip), mu_hat=-1.0, mu_n=0, a_stop=self.a_stop_used)


class LocalEmergency7:
    """carla_demo 로컬 폴백용 v7 제어기 (감지는 데모의 SlipDetector 가 한다). ax 는 내부 칼만으로 거른다 — 보드와 같다."""
    MODES_NAME = MODES
    def __init__(self, dt=0.02):
        self.core = SlipCoreRef(dt)
        self.core.emerg = True

    def step(self, speed, gz, lane_err, lat_off, gf, gl, gr, ax=0.0, brake_cmd=0.0, ice_off=0.0, front_rel_v=0.0):
        ax_f = self.core.kax.step(ax)
        b, st = self.core.control2(speed, gz, lane_err, lat_off, gf, gl, gr, ax_f, brake_cmd, ice_off, front_rel_v)
        return b, st, MODES[self.core.mode]
