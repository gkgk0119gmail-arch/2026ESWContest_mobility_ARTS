"""2차 방어 제어표 탐색용 경량 대리 차량 모델 (torch 벡터화, GPU 수천~수십만 개 병렬).

왜 직접 만드나: 2차 방어는 IMU 만 보므로 노면 외관(렌더)은 판정과 무관하다. 필요한 것은
'마찰 μ 에서 제동·조향을 이렇게 주면 차가 어떻게 움직이나' 를 셀 48개 × 후보 수백 × 시나리오 수백 번 돌리는 것이다.
CARLA 는 한 번에 한 대·실시간 근처라 탐색에 못 쓰고, 검증에만 쓴다. 이 모델은 CARLA 로그로 보정하고 오차를 보고한 뒤에 쓴다.

모델 (오른손 좌표: x 앞, y 왼쪽, ψ 반시계 양수 — CARLA 는 왼손이라 입력 시 부호를 뒤집는다)
  · 동역학 자전거 모델 (vx, vy, r) + 전역 위치 (X, Y, ψ)
  · 축별 PhysX 형 타이어: 종 슬립(Cx·Fz·κ)·횡 슬립각(Cy·Fz·α) 원력을 마찰원 μFz 로 정규화
  · 축별 바퀴 회전 ω (암시적 적분): 제동 토크가 타이어 토크를 넘으면 잠기고, 풀면 μ 에 비례한 속도로 다시 돈다
  · 엔진 브레이크(발 뗌) — CARLA 차량에서 마른 노면 1.95 m/s² 감속, 거의 전부 뒷축
  · 종방향 하중 이동 (무게중심 높이 h), 조향 1차 지연 τ_δ, 구름저항 + 공기저항
  · 내부 dt 0.002 s, 제어 dt 0.02 s (보드 50 Hz 와 같다)
CARLA 테슬라 모델3 값: 질량 1845 kg, 바퀴 반경 0.37 m, 바퀴당 최대 제동 700 N·m, 최대 조향 70°, 축거 약 3.0 m.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import math
import torch

G = 9.80665


@dataclass
class VehicleParams:
    m: float = 1845.0
    L: float = 3.0
    a: float = 1.40            # 무게중심 → 앞축 (fit)
    h: float = 0.50            # 무게중심 높이
    Iz: float = 2900.0         # 요 관성 (fit)
    R: float = 0.37
    Tb_axle: float = 1400.0    # 축당 최대 제동 토크 (2 바퀴 × 700)
    front_brake_share: float = 0.5
    delta_max: float = math.radians(70.0)   # steer 1.0 → 바퀴각 (fit 배율)
    delta_scale: float = 1.0
    tau_delta: float = 0.08    # 조향 1차 지연 (fit)
    Cy: float = 6.0            # 횡 강성 (/rad, 하중당) (fit)
    Cx: float = 6.0            # 종 강성 (/슬립, 하중당) — 잠긴 바퀴(κ=−1)에 남는 횡력 몫도 정한다 (fit)
    Iw: float = 3.0            # 축당 바퀴 회전 관성 (kg·m², 두 바퀴) (fit)
    v_eps: float = 2.0         # 슬립 계산 저속 정규화 (m/s)
    F_drive: float = 4000.0    # 가속 페달 1.0 의 구동력 (N)
    E_b: float = 3500.0        # 엔진 브레이크 (가속 페달 0 일 때 구동축에 걸리는 제동력, N) (fit)
                               #  CARLA 차량은 damping_rate_zero_throttle_clutch_engaged=2.0 이라 마른 노면에서 발을 떼면
                               #  1.95 m/s² 로 감속한다. 이 힘이 뒷바퀴 마찰을 먹어 저마찰에서 '발 뗌 오버스티어' 스핀을 만든다.
    eb_front: float = 0.5      # 엔진 브레이크 앞축 몫 (fit)
    c_roll: float = 0.003      # 구름저항 계수 (×mg)
    c_drag: float = 0.40       # 공기저항 (N/(m/s)²)
    mu_dry: float = 0.75       # 빙판 밖 (보정 실험의 friction 1.0 μ_eff)

    def to(self, device, dtype=torch.float32):
        return {k: torch.tensor(v, device=device, dtype=dtype) for k, v in asdict(self).items()}


class Surrogate:
    """N 대를 한꺼번에 굴린다. 상태 텐서 [N]."""

    def __init__(self, n: int, params: VehicleParams | None = None, device: str = "cpu", dt_ctrl: float = 0.02,
                 substeps: int = 10, dtype=torch.float32):
        self.n, self.dev, self.dtype = n, torch.device(device), dtype
        self.p = params or VehicleParams()
        self.P = self.p.to(self.dev, dtype)
        self.dt_c, self.k = dt_ctrl, substeps
        self.dt = dt_ctrl / substeps
        z = lambda: torch.zeros(n, device=self.dev, dtype=dtype)
        self.X, self.Y, self.psi, self.vx, self.vy, self.r, self.delta = z(), z(), z(), z(), z(), z(), z()
        self.ax_b, self.ay_b = z(), z()          # 차체 가속도 (IMU 가 보는 값)
        self.locked_f, self.locked_r = z().bool(), z().bool()
        self.wf, self.wr = z(), z()             # 축별 바퀴 각속도 (rad/s)

    def set_state(self, X=0.0, Y=0.0, psi=0.0, vx=0.0, vy=0.0, r=0.0, delta=0.0, wf=None, wr=None):
        for k, v in dict(X=X, Y=Y, psi=psi, vx=vx, vy=vy, r=r, delta=delta).items():
            t = getattr(self, k)
            t.copy_(v if torch.is_tensor(v) else torch.full_like(t, float(v)))
        # 바퀴는 기본적으로 굴러가는 중 (ω = v/R)
        self.wf.copy_(wf if wf is not None else self.vx / self.p.R)
        self.wr.copy_(wr if wr is not None else self.vx / self.p.R)

    # ---- 축 하나의 타이어 힘 + 바퀴 회전 (축 좌표: x 굴러가는 방향) ----
    # PhysX 차량(=CARLA)과 같은 계열: 종 슬립 κ = (ωR − v)/v, 슬립각 α 로 종·횡 '원력' 을 구한 뒤 마찰원 μFz 로 정규화한다.
    #   Fx_raw = Cx·Fz·κ,  Fy_raw = −Cy·Fz·α,  |F_raw| > μFz 면 같은 방향으로 줄인다 (잠기면 κ = −1 → 미끄럼).
    # 바퀴 회전 ω: Iw·ω̇ = −Fx·R − T_brake − T_engine.  제동 토크가 타이어 토크보다 크면 바퀴가 멎는다(잠김).
    # 펄스 제동이 약해지면 바퀴가 다시 돌기 시작하는데, 그 회복 시간은 Iw·ω/(μFz·R) — μ 가 낮을수록 길다.
    # (CARLA 보정 실험: μ 0.16 에서 펄스 + 사인 조향이 22° 를 돈다. 바퀴 회전 상태 없이 '늘 잠김' 으로 두면 0° 가 된다.)
    # ω 는 선형화한 뒤 암시적(후진 오일러)으로 적분한다 — 저속에서 강성(Cx·Fz·R/v)이 커도 발산하지 않는다.
    def _axle(self, vwx, vwy, Fz, mu, Tb, Teng, w):
        P = self.P
        vden = torch.clamp(vwx.abs(), min=P["v_eps"])
        alpha = torch.atan2(vwy, vwx.abs() + 0.3)
        Fmax = mu * Fz
        def forces(wv):
            kap = torch.clamp((wv * P["R"] - vwx) / vden, -1.0, 1.0)
            Fx_raw = P["Cx"] * Fz * kap; Fy_raw = -P["Cy"] * Fz * alpha
            mag = torch.sqrt(Fx_raw * Fx_raw + Fy_raw * Fy_raw) + 1e-6
            k = torch.clamp(Fmax / mag, max=1.0)
            return Fx_raw * k, Fy_raw * k, k
        Fx, Fy, k = forces(w)
        # 암시적 ω 갱신: dFx/dω ≈ Cx·Fz·R/vden·k
        kw = P["Cx"] * Fz * P["R"] / vden * k
        Tb_s = Tb * torch.sign(w)                           # 제동 토크는 회전을 막는 방향
        dw = self.dt * (-Fx * P["R"] - Tb_s - Teng) / (P["Iw"] + self.dt * kw * P["R"])
        w_new = w + dw
        # 제동으로 0 을 지나치면 멈춘다 (마찰 브레이크는 거꾸로 돌리지 않는다)
        cross = (w > 0) & (w_new < 0) | (w < 0) & (w_new > 0)
        w_new = torch.where(cross & (Tb > 0), torch.zeros_like(w_new), w_new)
        Fx, Fy, _ = forces(w_new)
        return Fx, Fy, w_new, w_new.abs() * P["R"] < 0.05 * vwx.abs()

    def step(self, steer, brake, mu_f, mu_r, throttle=None):
        """제어 한 주기(0.02 s). steer: CARLA 정규 조향(-1~1, 오른쪽 양수), brake 0~1, mu_f/mu_r: 앞/뒤축 노면 μ [N]."""
        P = self.P
        d_cmd = -steer * P["delta_max"] * P["delta_scale"]
        thr = throttle if throttle is not None else torch.zeros_like(steer)
        b_ = P["L"] - P["a"]
        for _ in range(self.k):
            self.delta = self.delta + (d_cmd - self.delta) * (self.dt / (P["tau_delta"] + self.dt))
            cd, sd = torch.cos(self.delta), torch.sin(self.delta)
            Fzf = torch.clamp(P["m"] * (G * b_ - self.ax_b * P["h"]) / P["L"], min=200.0)
            Fzr = torch.clamp(P["m"] * (G * P["a"] + self.ax_b * P["h"]) / P["L"], min=200.0)
            Tb_f = brake * P["Tb_axle"] * 2 * P["front_brake_share"]
            Tb_r = brake * P["Tb_axle"] * 2 * (1 - P["front_brake_share"])
            eng = P["E_b"] * P["R"] * (thr <= 1e-3).to(steer.dtype)            # 엔진 브레이크 토크 (발 뗌)
            drive = thr * P["F_drive"] * P["R"]
            # 엔진 토크는 회전 방향 반대로 (ω>0 이면 감속), 구동은 앞으로
            Te_f = eng * P["eb_front"] * torch.tanh(self.wf / 2.0) - drive * P["eb_front"]
            Te_r = eng * (1 - P["eb_front"]) * torch.tanh(self.wr / 2.0) - drive * (1 - P["eb_front"])
            vfx, vfy = self.vx, self.vy + P["a"] * self.r
            vrx, vry = self.vx, self.vy - b_ * self.r
            wfx = vfx * cd + vfy * sd; wfy = -vfx * sd + vfy * cd
            Fxf_w, Fyf_w, self.wf, lf = self._axle(wfx, wfy, Fzf, mu_f, Tb_f, Te_f, self.wf)
            Fxr, Fyr, self.wr, lr = self._axle(vrx, vry, Fzr, mu_r, Tb_r, Te_r, self.wr)
            Fxf = Fxf_w * cd - Fyf_w * sd; Fyf = Fxf_w * sd + Fyf_w * cd
            spd = torch.sqrt(self.vx ** 2 + self.vy ** 2)
            Fres = P["c_roll"] * P["m"] * G * torch.tanh(spd / 0.5) + P["c_drag"] * spd * spd
            ux = self.vx / (spd + 1e-6); uy = self.vy / (spd + 1e-6)
            Fx = Fxf + Fxr - Fres * ux; Fy = Fyf + Fyr - Fres * uy
            ax_b = Fx / P["m"]; ay_b = Fy / P["m"]
            rdot = (P["a"] * Fyf - b_ * Fyr) / P["Iz"]
            self.vx = self.vx + (ax_b + self.vy * self.r) * self.dt
            self.vy = self.vy + (ay_b - self.vx * self.r) * self.dt
            self.r = self.r + rdot * self.dt
            stop = (spd < 0.15) & (brake > 0.05)
            self.vx = torch.where(stop, torch.zeros_like(self.vx), self.vx)
            self.vy = torch.where(stop, torch.zeros_like(self.vy), self.vy)
            self.r = torch.where(stop, self.r * 0.5, self.r)
            c, s_ = torch.cos(self.psi), torch.sin(self.psi)
            self.X = self.X + (self.vx * c - self.vy * s_) * self.dt
            self.Y = self.Y + (self.vx * s_ + self.vy * c) * self.dt
            self.psi = self.psi + self.r * self.dt
            self.ax_b, self.ay_b = ax_b, ay_b
            self.locked_f, self.locked_r = lf, lr
        return self

    @property
    def speed(self):
        return torch.sqrt(self.vx ** 2 + self.vy ** 2)


def mu_of_friction(friction: float, table: dict[float, float]) -> float:
    """CARLA friction 배수 → μ_eff (2단계 보정표, 로그 선형 보간)."""
    ks = sorted(table)
    if friction <= ks[0]: return table[ks[0]] * friction / ks[0]
    if friction >= ks[-1]: return table[ks[-1]]
    for lo, hi in zip(ks, ks[1:]):
        if lo <= friction <= hi:
            w = (math.log(friction) - math.log(lo)) / (math.log(hi) - math.log(lo))
            return table[lo] * (1 - w) + table[hi] * w
    return table[ks[-1]]
