"""제어표 탐색용 빙판 시나리오 (대리 모델 + 보드 2차 방어, torch 벡터화).

세계 (오른손 좌표 X 앞, Y 왼쪽):
  · 직선 도로. 자차 차로 중심 Y=0, 차로 폭 3.5 m. 왼쪽 차로 nL ∈ {0,1,2}, 오른쪽 nR ∈ {0,1}. 바깥 끝 0.6 m 밖에 방호벽.
  · 빙판: X ∈ [−20, ice_rem] (확정 지점 X=0), 도로 전폭. 그 밖은 마른 노면(mu_dry).
  · 앞차: 자차 차로, 확정 지점에서 D_lead 앞 (정지 또는 저속, 없을 수도). 옆 차로 차량: 왼쪽/오른쪽 차로에 0~1대, 같은 방향 정속.
자차:
  · 보드가 미끄러짐을 확정한 순간의 상태에서 시작한다 (CARLA 폐루프 주행에서 관측한 분포를 넓혀 뽑는다).
    대리 모델은 CARLA 로그로 '확정 이후' 구간을 보정·검증했고, 확정 전 자율주행 구간(가속 페달·교통 관리자 조향)은
    모형화 범위 밖이라서다 (4단계 검증 문서 참고).
  · 그 순간부터 후보 제어기(제어표 한 칸의 값)가 제동·조향을 낸다.
결과 (env 마다): 충돌 여부·충돌 상대속도, 스핀(차선 대비 > 86°), 차선 이탈(> 34° 또는 도로 밖), 확정 → 정지 거리, 최대 추종 오차.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from icepredict.sim.gs_controller import SlipControllerV
from icepredict.sim.surrogate import Surrogate, VehicleParams

LANE_W, CAR_L, CAR_W = 3.5, 4.8, 2.1
SPIN, DEP_HEAD = 1.5, 0.6


@dataclass
class ScenarioSet:
    """시나리오 변수 텐서 [N]. 자차는 보드가 미끄러짐을 확정한 순간의 상태에서 시작한다 (X=0 이 그 지점)."""
    mu: torch.Tensor
    kappa: torch.Tensor         # 차로 곡률 (1/m, 왼쪽 +). CARLA 시험 구간은 반경 약 120 m 곡선이었다
    v0: torch.Tensor            # 확정 순간 속도 (m/s)
    vy0: torch.Tensor           # 횡미끄럼 속도 (오른손, 왼쪽 +)
    r0: torch.Tensor            # 요레이트 (오른손)
    psi0: torch.Tensor          # 차선 대비 방향 (오른손)
    y0: torch.Tensor            # 차로 중심 대비 횡위치 (왼쪽 +)
    ice_rem: torch.Tensor       # 확정 지점에서 빙판 끝까지 (m)
    nL: torch.Tensor
    nR: torch.Tensor
    lead_x: torch.Tensor        # 앞차 중심 X (없으면 1e6)
    lead_v: torch.Tensor
    adjL_x: torch.Tensor        # 왼쪽 차로 차량 중심 X (없으면 1e6)
    adjR_x: torch.Tensor
    adj_v: torch.Tensor


def sample_scenarios(n, mu_lo, mu_hi, v_lo, v_hi, gen, device, hard_frac=0.5):
    """μ 는 [mu_lo, mu_hi] 로그 균등, 속도 [v_lo, v_hi] km/h 균등.
    확정 순간 상태는 CARLA 폐루프 주행 15회에서 관측한 범위(횡미끄럼 1.2~1.9 m/s, 요레이트 0.11~0.15 rad/s,
    방향오차 4~7°, 횡오프셋 0.3~1.2 m, 빙판 진입 10~25 m 뒤 확정)를 좌우 대칭으로, 약 1.5 배 넓혀 뽑는다.
    hard_frac 만큼은 앞차가 빙판 정지거리 안쪽에 있다."""
    u = lambda: torch.rand(n, generator=gen, device=device)
    mu = torch.exp(math.log(mu_lo) + (math.log(mu_hi) - math.log(mu_lo)) * u())
    v0 = (v_lo + (v_hi - v_lo) * u()) / 3.6
    # 곡선: 반경 90~400 m (좌우), 20 % 는 직선. 빙판 위에서 미끄러짐을 만드는 것은 곡선이 요구하는 횡가속이다.
    turn = torch.where(u() < 0.5, -torch.ones(n, device=device), torch.ones(n, device=device))
    R = 90.0 + 310.0 * u()
    kappa = torch.where(u() < 0.2, torch.zeros_like(v0), turn / R)
    side = torch.where(kappa != 0, -turn, torch.where(u() < 0.5, -torch.ones(n, device=device), torch.ones(n, device=device)))  # 바깥쪽으로 미끄러진다
    # CARLA 관측(오른쪽 곡선): 횡속도 1.2~1.9 m/s 바깥(왼쪽), 요레이트 ≈ 곡선 추종값, 방향오차 4~7° 바깥, 횡오프셋 0.3~1.2 m 바깥
    vy0 = side * (0.6 + 2.2 * u())
    r0 = kappa * v0 + (u() * 2 - 1) * 0.05
    psi0 = side * torch.deg2rad(2.0 + 9.0 * u())
    y0 = side * (0.0 + 1.6 * u())
    ice_rem = 60.0 + 190.0 * u()                 # CARLA 검증은 200 m 빙판 (트리거를 벗어나면 마찰이 3.5 로 돌아가는 결함 때문)
    nL = torch.floor(u() * 3).clamp(max=2)
    nR = torch.floor(u() * 2).clamp(max=1)
    has_lead = u() < 0.75
    hard = u() < hard_frac
    lead_d = torch.where(hard, 10.0 + 30.0 * u(), 40.0 + 110.0 * u())        # 확정 지점에서 앞차 중심까지
    lead_x = torch.where(has_lead, lead_d, torch.full_like(lead_d, 1e6))
    lead_v = torch.where(u() < 0.6, torch.zeros_like(v0), v0 * 0.3 * u())
    occL = (u() < 0.5) & (nL > 0); occR = (u() < 0.5) & (nR > 0)
    adjL_x = torch.where(occL, -25.0 + 60.0 * u(), torch.full_like(v0, 1e6))
    adjR_x = torch.where(occR, -25.0 + 60.0 * u(), torch.full_like(v0, 1e6))
    adj_v = v0 * (0.6 + 0.5 * u())
    return ScenarioSet(mu, kappa, v0, vy0, r0, psi0, y0, ice_rem, nL, nR, lead_x, lead_v, adjL_x, adjR_x, adj_v)


def _boxes_hit(dx, dy, dpsi):
    """두 차(같은 크기) 중심 차이(자차 기준 아님, 세계 좌표)와 자차 방향으로 대략 겹침: 원 3개씩 근사."""
    return (dx.abs() < CAR_L) & (dy.abs() < CAR_W + (CAR_L - CAR_W) * dpsi.sin().abs())


def simulate(sc: ScenarioSet, gains, vparams: VehicleParams, mu_dry: float, device, fixed=False, lut_fn=None,
             max_s=15.0, dt=0.02, substeps=10):
    """확정 순간(X=0)부터 보드 제어기가 정지까지 몬다. 빙판은 X ∈ [−20, ice_rem]."""
    n = sc.mu.numel()
    sim = Surrogate(n, vparams, device=device, dt_ctrl=dt, substeps=substeps)
    sim.set_state(X=torch.zeros_like(sc.v0), Y=sc.y0, psi=sc.psi0, vx=sc.v0, vy=sc.vy0, r=sc.r0)   # 차로 원점 (0,0), 방향 0
    ctl = SlipControllerV(n, dt, 2.0, gains=None if fixed else gains, mu_est=True, device=device, lut_fn=lut_fn)
    ctl.emerg[:] = True; ctl.armed[:] = False                # 보드가 방금 확정했다
    zeros = torch.zeros(n, device=device); ones = torch.ones(n, device=device)
    edgeL = (sc.nL * LANE_W + LANE_W / 2 + 0.6); edgeR = -(sc.nR * LANE_W + LANE_W / 2 + 0.6)
    done = torch.zeros(n, dtype=torch.bool, device=device)
    crash = done.clone(); crash_v = zeros.clone(); spin = done.clone(); dep = done.clone()
    d_stop = zeros.clone(); track_max = zeros.clone(); head_max = zeros.clone(); t_stop = torch.full_like(zeros, max_s)
    lead_x = sc.lead_x.clone(); adjL = sc.adjL_x.clone(); adjR = sc.adjR_x.clone()
    brake_prev = torch.full_like(zeros, 0.6)                 # 확정 샘플의 초기 명령 (emergency_command)
    slip_lane = torch.zeros_like(zeros)                      # 미끄러진 차로 = 자차 차로 (Y=0)
    steps = int(max_s / dt)
    from icepredict.sim.road import ArcLane, wrap
    road = ArcLane(sc.kappa, torch.zeros_like(sc.v0), torch.zeros_like(sc.v0), torch.zeros_like(sc.v0))
    for k in range(steps):
        Xv, psi, spd = sim.X, sim.psi, sim.speed
        S, Yv, psiL = road.frenet(sim.X, sim.Y)                   # 진행 거리, 횡위치(왼쪽 +), 차로 방향
        hrel = wrap(psi - psiL)
        cps = torch.cos(hrel)
        xf = S + sim.P["a"] * cps; xr = S - (sim.P["L"] - sim.P["a"]) * cps
        on_f = (xf >= -20.0) & (xf <= sc.ice_rem); on_r = (xr >= -20.0) & (xr <= sc.ice_rem)
        muf = torch.where(on_f, sc.mu, torch.full_like(sc.mu, mu_dry)); mur = torch.where(on_r, sc.mu, torch.full_like(sc.mu, mu_dry))
        lane_idx = torch.clamp(torch.round(Yv / LANE_W), -sc.nR, sc.nL)
        lane_c = lane_idx * LANE_W
        lat_off = Yv - lane_c
        lane_err = hrel
        Xv = S                                                      # 아래 간격·충돌 계산은 차로 좌표(진행 거리)로 한다
        lead_gap = torch.where(lane_idx == 0, lead_x - Xv - CAR_L, torch.full_like(Xv, 999.0))
        gf = torch.where((lead_x < 1e5) & (lead_gap < 300), lead_gap, torch.full_like(Xv, 999.0))
        left_lane = lane_idx + 1; right_lane = lane_idx - 1
        def side_gap(lane_no, exists):
            g = torch.full_like(Xv, 999.0)
            g = torch.where((lane_no == 1) & (adjL < 1e5), (adjL - Xv).abs() - CAR_L, g)
            g = torch.where((lane_no == -1) & (adjR < 1e5), (adjR - Xv).abs() - CAR_L, g)
            g = torch.where((lane_no == 0) & (lead_x < 1e5), (lead_x - Xv).abs() - CAR_L, g)
            return torch.where(exists, torch.clamp(g, min=0.0), torch.full_like(Xv, -1.0))
        gl = side_gap(left_lane, left_lane <= sc.nL); gr = side_gap(right_lane, right_lane >= -sc.nR)
        frv = torch.where(gf < 900, sc.lead_v - spd, zeros)
        ice_off = Yv - slip_lane                                    # 미끄러진 차로(자차 차로) 중심 대비 횡위치
        _, _, b_cmd, s_cmd = ctl.board(-sim.ay_b, -sim.r, spd, zeros, sim.ax_b, brake_prev,
                                       lane_err, lat_off, gf, gl, gr, ice_off, frv, step_idx=k)
        steer = torch.where(done, zeros, s_cmd); brake = torch.where(done, ones, b_cmd)
        sim.step(steer, brake, muf, mur)
        for nm in ("vx", "vy", "r"):
            setattr(sim, nm, torch.where(done, torch.zeros_like(getattr(sim, nm)), getattr(sim, nm)))
        brake_prev = brake
        lead_x = torch.where(lead_x < 1e5, lead_x + sc.lead_v * dt, lead_x)
        adjL = torch.where(adjL < 1e5, adjL + sc.adj_v * dt, adjL); adjR = torch.where(adjR < 1e5, adjR + sc.adj_v * dt, adjR)
        live = ~done
        spd2 = sim.speed
        S2, Y2, psiL2 = road.frenet(sim.X, sim.Y)
        h2 = wrap(sim.psi - psiL2)
        d_stop = torch.where(live, d_stop + spd2 * dt, d_stop)
        hd = h2.abs()
        head_max = torch.where(live, torch.maximum(head_max, hd), head_max)
        spin = spin | (live & (hd > SPIN))
        offroad = (Y2 + CAR_W / 2 > edgeL) | (Y2 - CAR_W / 2 < edgeR)
        dep = dep | (live & ((hd > DEP_HEAD) | offroad))
        trk = (lat_off + ctl.lat_shift).abs()
        track_max = torch.where(live, torch.maximum(track_max, trk), track_max)
        hit_lead = _boxes_hit(lead_x - S2, (0.0 - Y2), h2) & (lead_x < 1e5)
        hit_L = _boxes_hit(adjL - S2, (LANE_W - Y2), h2) & (adjL < 1e5)
        hit_R = _boxes_hit(adjR - S2, (-LANE_W - Y2), h2) & (adjR < 1e5)
        hit_w = (Y2 + CAR_W / 2 > edgeL + 0.3) | (Y2 - CAR_W / 2 < edgeR - 0.3)
        hit = (hit_lead | hit_L | hit_R | hit_w) & live
        rel = torch.where(hit_lead, (spd2 - sc.lead_v).abs(), torch.where(hit_L | hit_R, (spd2 - sc.adj_v).abs(), spd2))
        crash_v = torch.where(hit & ~crash, rel, crash_v)
        crash = crash | hit
        stopped = (spd2 < 0.3) & live & (k > 3)
        t_stop = torch.where(stopped, torch.full_like(t_stop, (k + 1) * dt), t_stop)
        done = done | hit | stopped
        if bool(done.all()): break
    det = torch.ones(n, dtype=torch.bool, device=device)
    return dict(detected=det, crash=crash, crash_v=crash_v, spin=spin, departure=dep, d_stop=d_stop, t_stop=t_stop,
                track_max=track_max, head_max=head_max, mu_hat=ctl.mu_hat, mu_n=ctl.mu_n, steps=k + 1)


def cost(res, w):
    """작업 지시 5단계: J = w1·충돌(+충돌속도) + w2·스핀 + w3·차선이탈 + w4·정지거리 + w5·최대 횡오프셋."""
    c = (w["crash"] * res["crash"].float() * (1.0 + res["crash_v"] / 10.0)
         + w["spin"] * res["spin"].float() + w["departure"] * res["departure"].float()
         + w["stop"] * res["d_stop"] / 50.0 + w["lat"] * torch.clamp(res["track_max"], max=7.0) / LANE_W)
    return c


def cvar(x, alpha=0.1, dim=-1):
    """최악 alpha 비율의 평균 (값이 클수록 나쁨)."""
    k = max(1, int(round(x.shape[dim] * alpha)))
    return torch.topk(x, k, dim=dim).values.mean(dim)
