"""보드 2차 방어(C slip_core.h)의 torch 벡터화 판 — 대리 모델 위에서 수십만 개를 한꺼번에 돌린다.

C 와 같은 계산을 한다 (test_gs_controller.py 가 C 코어 ctypes 와 샘플 단위로 비교한다):
  slip_step2   : 칼만 3개(ay, yaw, ax) → 기대 yaw(1차 지연) → 잔차 타원 3샘플 확정, 저마찰 트리거
  slip_mu_update: 펄스 강 구간 마찰원 μ 추정 (SLIP_MU_EST)
  slip_control2: 접지 회복 · 정지거리 · 모드(차선유지/회피/최대제동) · PD 조향 · 펄스 제동
제어 파라미터는 env 마다 다를 수 있다 (탐색 후보). 고정 게인 = 현재 보드 값.

제어 파라미터 (제어표 한 칸의 값):
  Kp, Kl, Kd, steer_max   조향 PD
  B_HI, B_LO, PULSE_HALF  펄스 제동 (강/약, 반주기 샘플 수 — 보드 고정값 8 = 16샘플 주기)
  B_EVADE                 회피로 차로를 옮기는 동안 제동
  a_stop_scale            정지거리 계산 감속 = a_stop_scale · μ̂ · g  (고정 게인은 μ 와 무관하게 1.0 m/s²)
  margin                  정지거리 여유 (m, 보드 고정 4)
"""
from __future__ import annotations

import math

import torch

G = 9.80665
MAX_STEER_RAD = 0.61086524
from icepredict.pi.gain_lut import BOUNDS, FIXED, PARAM_NAMES  # noqa: E402  (torch 없이도 쓰는 정의는 거기에)


class KF2V:
    def __init__(self, n, dt, q, r, dev, dtype):
        z = lambda: torch.zeros(n, device=dev, dtype=dtype)
        self.x0, self.x1, self.p00, self.p01, self.p10, self.p11 = z(), z(), z() + 1, z(), z(), z() + 1
        self.dt, self.q, self.r = dt, q, r
        self.init = torch.zeros(n, device=dev, dtype=torch.bool)

    def step(self, zm):
        dt = self.dt; d2 = dt * dt; d3 = d2 * dt; d4 = d3 * dt
        x0 = self.x0 + dt * self.x1; x1 = self.x1
        p00 = self.p00 + dt * (self.p10 + self.p01) + d2 * self.p11 + self.q * d4 * 0.25
        p01 = self.p01 + dt * self.p11 + self.q * d3 * 0.5
        p10 = self.p10 + dt * self.p11 + self.q * d3 * 0.5
        p11 = self.p11 + self.q * d2
        y = zm - x0; S = p00 + self.r; k0 = p00 / S; k1 = p10 / S
        nx0 = x0 + k0 * y; nx1 = x1 + k1 * y
        np00 = (1 - k0) * p00; np01 = (1 - k0) * p01; np10 = p10 - k1 * p00; np11 = p11 - k1 * p01
        i = self.init
        self.x0 = torch.where(i, nx0, zm); self.x1 = torch.where(i, nx1, self.x1)
        self.p00 = torch.where(i, np00, self.p00); self.p01 = torch.where(i, np01, self.p01)
        self.p10 = torch.where(i, np10, self.p10); self.p11 = torch.where(i, np11, self.p11)
        self.init = torch.ones_like(i)
        return self.x0


class SlipControllerV:
    """N 개의 보드 2차 방어. gains: {이름: 텐서 [N] 또는 스칼라}. gains=None 이면 보드 고정 게인(C 와 비트 단위 동일 논리)."""

    def __init__(self, n, dt=0.02, min_speed=2.0, gains=None, mu_est=True, device="cpu", dtype=torch.float32,
                 lut_fn=None):
        self.n, self.dt, self.dev, self.dtype = n, dt, torch.device(device), dtype
        z = lambda: torch.zeros(n, device=self.dev, dtype=dtype)
        zi = lambda: torch.zeros(n, device=self.dev, dtype=torch.int32)
        zb = lambda: torch.zeros(n, device=self.dev, dtype=torch.bool)
        self.kay = KF2V(n, dt, 2000.0, 0.16, self.dev, dtype)
        self.kyaw = KF2V(n, dt, 3000.0, 0.0005, self.dev, dtype)
        self.kax = KF2V(n, dt, 2000.0, 0.16, self.dev, dtype)
        self.min_speed = min_speed
        self.hits, self.brake_n, self.lowmu_hits, self.n_emerg, self.grip_n, self.pulse_hi_n, self.mu_n = zi(), zi(), zi(), zi(), zi(), zi(), zi()
        self.armed = zb() | True
        self.emerg, self.grip, self.ye_init = zb(), zb(), zb()
        self.mode = zi(); self.lat_shift = z(); self.ye = z()
        self.mu_hat = z() + 0.05; self.b_hi_last = z() + 0.7
        self.fixed = gains is None
        self.lut_fn = lut_fn            # μ̂, v → gains (제어표 경로, SLIP_GAIN_SCHED)
        self.fixed_mask = None          # env 별 고정/제어표 선택 (closed_replay 가 섞어 쓴다)
        g = gains if gains else {k: (v if k != "a_stop_scale" else 1.0) for k, v in FIXED.items()}
        self.g = {k: (v if torch.is_tensor(v) else torch.full((n,), float(v), device=self.dev, dtype=dtype)) for k, v in g.items()}
        self.mu_est = mu_est
        self.t_slip = torch.full((n,), -1, device=self.dev, dtype=torch.int32)

    # ---- slip_step2 ----
    def step2(self, ay, gz, speed, steer, ax, brake_cmd):
        ay_f = self.kay.step(ay); yaw_f = self.kyaw.step(gz); ax_f = self.kax.step(ax)
        slow = speed < self.min_speed
        delta = steer * MAX_STEER_RAD
        yaw_raw = speed * torch.tan(delta) / 2.7 / (1.0 + (speed / 17.0) ** 2)
        k = self.dt / (0.06 + self.dt)
        ye_new = torch.where(self.ye_init, self.ye + k * (yaw_raw - self.ye), yaw_raw)
        self.ye = torch.where(slow, self.ye, ye_new); self.ye_init = self.ye_init | ~slow
        ay_g = (ay_f - speed * self.ye) / G; yaw_err = yaw_f - self.ye
        hit = (ay_g / 0.3) ** 2 + (yaw_err / 0.35) ** 2 >= 1.0
        # C 는 저속이면 여기까지 오지 않고 돌아간다 (hits=0 만 하고 카운터·armed 는 그대로) — where(slow, 옛값, 새값)
        brake_n = torch.where(brake_cmd >= 0.3, self.brake_n + 1, torch.zeros_like(self.brake_n))
        lowmu_now = (brake_n >= 15) & (ax_f.abs() < 1.2)
        lowmu_hits = torch.where(lowmu_now, self.lowmu_hits + 1, torch.zeros_like(self.lowmu_hits))
        hits = torch.where(hit, self.hits + 1, torch.zeros_like(self.hits))
        armed = self.armed | (~hit & ~lowmu_now)
        fire = armed & ((hits >= 3) | (lowmu_hits >= 8)) & ~slow
        self.brake_n = torch.where(slow, self.brake_n, brake_n)
        self.lowmu_hits = torch.where(slow, self.lowmu_hits, lowmu_hits)
        self.hits = torch.where(slow, torch.zeros_like(hits), hits)
        self.armed = torch.where(slow, self.armed, armed & ~fire)
        return fire, ax_f, ay_f

    # ---- slip_control2 (+ μ 갱신) ----
    def control2(self, speed, gz, lane_err, lat_off, gf, gl, gr, ax_f, ay_f, brake_cmd, ice_off, front_rel_v, active):
        g = self.g
        self.n_emerg = torch.where(active, self.n_emerg + 1, self.n_emerg)
        if self.mu_est:
            hi = brake_cmd >= self.b_hi_last - 1e-3
            self.pulse_hi_n = torch.where(active, torch.where(hi, self.pulse_hi_n + 1, torch.zeros_like(self.pulse_hi_n)), self.pulse_hi_n)
            upd = active & (self.pulse_hi_n > 2) & (speed > 2.0)
            m = torch.clamp(torch.sqrt(ax_f * ax_f + ay_f * ay_f) / G, 0.02, 1.0)
            al = torch.where(m > self.mu_hat, torch.full_like(m, 0.08), torch.full_like(m, 0.35))
            self.mu_hat = torch.where(upd, self.mu_hat + al * (m - self.mu_hat), self.mu_hat)
            self.mu_n = torch.where(upd, self.mu_n + 1, self.mu_n)
        if self.lut_fn is not None:
            g = self.lut_fn(self.mu_hat, speed)
        gripping = (brake_cmd >= 0.3) & (ax_f <= -2.5)
        self.grip_n = torch.where(active, torch.where(gripping, self.grip_n + 1, torch.zeros_like(self.grip_n)), self.grip_n)
        self.grip = self.grip | (active & (self.grip_n >= 5))
        # 고정 게인 경로와 제어표 경로를 env 마다 고를 수 있다 (fixed_mask: True = 보드 고정 게인과 같은 계산)
        fm = self.fixed_mask if self.fixed_mask is not None else torch.full_like(self.emerg, self.fixed and self.lut_fn is None)
        a_ice = torch.where(fm, torch.full_like(speed, 1.0), torch.clamp(g["a_stop_scale"] * self.mu_hat * G, min=0.15))
        a_stop = torch.where(self.grip, torch.full_like(speed, 6.0), a_ice)
        d_stop = speed * speed / (2.0 * a_stop) + torch.where(fm, torch.full_like(speed, 4.0), g["margin"])
        overlap = gf < 0.0
        cannot = ~overlap & (gf < d_stop) & (front_rel_v < 1.0)
        free = (self.mode == 0) | (self.mode == 1) | (self.mode == 4)
        r_ok = (gr >= 20.0) & ((3.5 - ice_off).abs() > 1.75)
        l_ok = (gl >= 20.0) & ((-3.5 - ice_off).abs() > 1.75)
        pl = ice_off > 0.5
        first_ok = torch.where(pl, l_ok, r_ok); second_ok = torch.where(pl, r_ok, l_ok)
        ev_first = torch.where(pl, torch.full_like(self.mode, 2), torch.full_like(self.mode, 3))
        ev_second = torch.where(pl, torch.full_like(self.mode, 3), torch.full_like(self.mode, 2))
        new_mode = torch.where(overlap, torch.full_like(self.mode, 4),
                   torch.where(cannot & ~self.grip, torch.where(first_ok, ev_first, torch.where(second_ok, ev_second, torch.full_like(self.mode, 4))),
                   torch.where(cannot & self.grip, torch.full_like(self.mode, 4), torch.full_like(self.mode, 1))))
        shift = torch.where(new_mode == 2, torch.full_like(speed, -3.5), torch.where(new_mode == 3, torch.full_like(speed, 3.5), torch.zeros_like(speed)))
        upd_mode = active & free
        self.mode = torch.where(upd_mode, new_mode, self.mode)
        self.lat_shift = torch.where(upd_mode, shift, self.lat_shift)
        st = (g["Kp"] * lane_err + g["Kl"] * (lat_off + self.lat_shift) - g["Kd"] * gz) / MAX_STEER_RAD
        st = torch.maximum(torch.minimum(st, g["steer_max"]), -g["steer_max"])
        ph = torch.where(fm, torch.full_like(self.n_emerg, 8), torch.clamp(torch.round(g["PULSE_HALF"]), min=1).to(torch.int32))
        pulse = ((self.n_emerg // ph) % 2).bool()                                 # 고정: (n >> 3) & 1 과 같다
        b_hi, b_lo = g["B_HI"] * torch.ones_like(speed), g["B_LO"] * torch.ones_like(speed)
        is_ev = (self.mode == 2) | (self.mode == 3)
        moving = (lat_off + self.lat_shift).abs() > 0.8
        f = lambda x: torch.full_like(speed, x)
        b_ev_f = torch.where(moving, f(0.25), torch.where(pulse, f(0.7), f(0.35)))
        b_hs_f = torch.where(self.grip, f(1.0), torch.where(pulse, f(0.95), f(0.5)))
        b_lk_f = torch.where(self.grip, f(0.9), torch.where(pulse, f(0.7), f(0.35)))
        bhl_f = torch.where(self.mode == 4, f(0.95), f(0.7))
        # 제어표 경로: 회피 이동 중 B_EVADE, 그 밖에는 B_HI/B_LO 펄스 (최대제동은 B_HI 를 1.357배 — 보드 0.95/0.70 비율 — 상한 1)
        hs_hi = torch.clamp(b_hi * 1.357, max=1.0)
        b_ev_g = torch.where(moving, g["B_EVADE"] * torch.ones_like(speed), torch.where(pulse, b_hi, b_lo))
        b_hs_g = torch.where(self.grip, f(1.0), torch.where(pulse, hs_hi, b_lo))
        b_lk_g = torch.where(self.grip, f(0.9), torch.where(pulse, b_hi, b_lo))
        bhl_g = torch.where(self.mode == 4, hs_hi, b_hi * torch.ones_like(speed))
        b_ev = torch.where(fm, b_ev_f, b_ev_g); b_hs = torch.where(fm, b_hs_f, b_hs_g)
        b_lk = torch.where(fm, b_lk_f, b_lk_g); bhl = torch.where(fm, bhl_f, bhl_g)
        b = torch.where(is_ev, b_ev, torch.where(self.mode == 4, b_hs, b_lk))
        if self.mu_est:
            self.b_hi_last = torch.where(active, bhl, self.b_hi_last)
        b = torch.where(speed < 2.0, torch.ones_like(b), b)
        stopped = active & (speed < 0.5)
        self.mode = torch.where(stopped, torch.full_like(self.mode, 5), self.mode)
        self.emerg = self.emerg & ~stopped
        b = torch.where(stopped, torch.ones_like(b), b); st = torch.where(stopped, torch.zeros_like(st), st)
        return b, st

    def board(self, ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v, step_idx=0):
        fire, ax_f, ay_f = self.step2(ay, gz, speed, steer, ax, brake_cmd)
        self.t_slip = torch.where(fire & (self.t_slip < 0), torch.full_like(self.t_slip, step_idx), self.t_slip)
        self.emerg = self.emerg | fire
        active = self.emerg.clone()
        b, st = self.control2(speed, gz, lane_err, lat_off, gf, gl, gr, self.kax.x0, self.kay.x0, brake_cmd, ice_off, front_rel_v, active)
        b = torch.where(active, b, torch.zeros_like(b)); st = torch.where(active, st, torch.zeros_like(st))
        return fire, active, b, st


def lut_torch(lut, device="cpu", dtype=torch.float32):
    """GainLUT(C slip_gain_lut.h 와 같은 표) → (μ̂, v) 텐서를 받아 파라미터 dict 텐서를 돌려주는 함수. C 와 같은 로그·쌍선형 보간."""
    from icepredict.pi.gain_lut import PARAM_NAMES as _PN
    T = torch.tensor(lut.table, device=device, dtype=dtype)                     # [nm, nv, 10]
    lm_ax = torch.tensor(lut.logmu, device=device, dtype=dtype); v_ax = torch.tensor(lut.kph, device=device, dtype=dtype)
    def find(ax, x):
        n = ax.numel()
        k = torch.zeros_like(x, dtype=torch.long)
        for j in range(1, n - 1): k = k + (x >= ax[j]).long()
        t = (x - ax[k]) / (ax[k + 1] - ax[k])
        t = torch.where(x <= ax[0], torch.zeros_like(t), torch.where(x >= ax[-1], torch.ones_like(t), t))
        k = torch.where(x >= ax[-1], torch.full_like(k, n - 2), k)
        return k, t
    def fn(mu_hat, speed):
        im, tm = find(lm_ax, torch.log(torch.clamp(mu_hat, min=float(lut.mu[0]))))
        iv, tv = find(v_ax, speed * 3.6)
        a = T[im, iv]; b = T[im, iv + 1]; c = T[im + 1, iv]; e = T[im + 1, iv + 1]
        lo = a + (b - a) * tv[:, None]; hi = c + (e - c) * tv[:, None]
        g = lo + (hi - lo) * tm[:, None]
        return {k: g[:, i] for i, k in enumerate(_PN)}
    return fn
