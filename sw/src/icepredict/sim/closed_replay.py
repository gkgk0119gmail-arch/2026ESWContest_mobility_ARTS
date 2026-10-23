"""CARLA 폐루프 주행을 대리 모델에서 되풀이한다 (4단계 보정·검증 공용).

CARLA 에서 보드 C 코어(ctypes)가 미끄러짐을 확정한 순간의 상태(속도·횡속도·요레이트·차선 대비 방향·횡위치)에서
대리 모델 + 같은 제어기(gs_controller — C 와 샘플 단위 동치)로 폐루프를 돌린다.
  · 제어 파라미터: 고정 게인(gains=None) 또는 그 주행에 쓴 후보(--slip-gains)
  · 빙판 끝: CARLA 궤적에서 앞축이 빙판을 벗어난 지점까지의 주행 거리
  · 앞차(--lead-stop): 확정 순간의 앞차 간격(core 로그 gap_f)에서 정지해 있다
  · 옆 차로: 첫 비상 모드 이벤트의 gap_left/right (-1 = 차로 없음)
여러 주행 × 여러 차량 파라미터 후보를 한 텐서로 굴린다.
"""
from __future__ import annotations

import json
import math
import pathlib
import re

import numpy as np
import torch

from icepredict.pi.gain_lut import PARAM_NAMES
from icepredict.sim.gs_controller import SlipControllerV
from icepredict.sim.surrogate import Surrogate, VehicleParams, mu_of_friction

DT = 0.02
CAR_L = 4.8


def load_runs(dirs, mu_table, pattern=r"state_(s4c_fix|s4c_gs)_(.+)\.csv"):
    runs = []
    for d in dirs:
        d = pathlib.Path(d)
        jobs = {}
        for jf in d.parent.parent.glob("jobs/*.json"):
            try:
                for j in json.loads(jf.read_text()): jobs[j["tag"]] = j
            except Exception:
                pass
        for f in sorted(d.glob("state_*.csv")):
            m = re.match(pattern, f.name)
            if not m: continue
            tag = f.name[len("state_"):-4]
            evf = d / f"events_{tag}.json"
            if not evf.exists(): continue
            E = json.loads(evf.read_text()); ev = E["events"]; args = E["args"]
            slip = next((e for e in ev if e["event"] == "secondary_slip"), None)
            if slip is None: continue
            X = np.genfromtxt(f, delimiter=",", names=True, dtype=None, encoding=None)
            i0 = int(np.argmin(np.abs(X["t"] - slip["t"])))
            core = d / f"core_{tag}.csv"
            gap0 = 999.0
            if core.exists():
                C = np.genfromtxt(core, delimiter=",", names=True, dtype=None, encoding=None)
                if len(C):
                    j0 = int(np.argmin(np.abs(C["t"] - slip["t"]))); gap0 = float(C["gap_f"][j0])
            # CARLA 는 마찰 트리거를 벗어나면 타이어 마찰을 3.5(횡 2 g)로 되돌린다 — 그 뒤는 비교하지 않는다
            out_i = np.where((X["inside_f"][i0:] < 0.5) | (X["inside_r"][i0:] < 0.5))[0]
            i_end = i0 + int(out_i[0]) if len(out_i) else len(X)
            exited = bool(len(out_i))
            # 충돌 뒤는 비교하지 않는다 (CARLA 충돌 물리는 대리 모델 밖이다)
            col0 = next((e for e in ev if e["event"] == "collision" and e["t"] >= slip["t"] - 1e-6), None)
            if col0 is not None:
                i_end = min(i_end, int(np.argmin(np.abs(X["t"] - col0["t"]))))
            if i_end - i0 < 10: continue                       # 확정 순간에 이미 빙판 밖 — 비교할 구간이 없다
            # 차로 곡률: 확정 직전 3 s 의 차로 방향(오른손 ψ_L = −(yaw + lane_err)) 을 진행 거리로 1차 맞춤
            vv = np.hypot(X["vx"], X["vy"]); ss = np.cumsum(vv) * DT
            w0 = max(0, i0 - 150); win = slice(w0, max(w0 + 2, i0 - 5))
            psiL_c = -(np.unwrap(X["yaw"]) + X["lane_err"])
            kappa = float(np.polyfit(ss[win], np.unwrap(psiL_c[win]), 1)[0]) if (vv[win] > 3).sum() > 30 else 0.0
            v = np.hypot(X["vx"], X["vy"]); s = np.cumsum(v) * DT; s -= s[i0]
            later = np.where(X["inside_f"][i0:] < 0.5)[0]
            em = [e for e in ev if e["event"] == "emergency_mode"]
            gains = json.loads(args["slip_gains"]) if args.get("slip_gains") else None
            fr = float(args["friction"])
            col = next((e for e in ev if e["event"] == "collision" and e["t"] >= slip["t"] - 1e-6), None)
            stop_i = int(np.argmax(v[i0:] < 0.3)) if (v[i0:] < 0.3).any() else len(v) - i0 - 1
            # 방향: 확정 순간의 차선 방향 대비 (CARLA 부호 = 오른손 head). lane_err 는 가장 가까운 차로의 앞 6 m 점을 겨눠서
            # 차로를 건너면 튄다 — 차체 요각 누적으로 바꾼다. (CARLA yaw 는 시계 양수 → 오른손 ψ = −yaw)
            psi = -np.unwrap(X["yaw"]); head_c = (psi - psi[i0]) + float(X["lane_err"][i0])
            runs.append(dict(tag=tag, fr=fr, mu=mu_table.get(fr, mu_of_friction(fr, mu_table)), kph=float(args["target_kph"]),
                             gains=gains, s0=(float(X["vx"][i0]), float(-X["vy"][i0]), float(-X["r"][i0])),
                             lane=(-float(X["lane_err"][i0]), -float(X["lat_off"][i0])), brake0=float(X["brake"][i0]),
                             d_ice=float(s[i0 + later[0]]) if len(later) else 1e9, gap0=gap0,
                             gl=em[0]["gap_left_m"] if em else 999.0, gr=em[0]["gap_right_m"] if em else 999.0,
                             ref_v=np.ascontiguousarray(v[i0:i_end], dtype=np.float64), ref_h=np.ascontiguousarray(head_c[i0:i_end], dtype=np.float64),
                             exited=exited, n_cmp=i_end - i0, kappa=kappa,
                             carla=dict(d_stop=float(np.sum(v[i0:min(i0 + stop_i, i_end)]) * DT), stopped=bool((v[i0:i_end] < 0.3).any()),
                                        head_max=float(np.degrees(np.abs(head_c[i0:min(i0 + stop_i + 1, i_end)]).max())),
                                        spin=bool(np.abs(head_c[i0:i_end]).max() > 1.5),
                                        crash=col is not None and col["t"] <= X["t"][min(i_end, len(X) - 1)] and col["with"] not in ("wall", "guardrail") and gap0 < 900,
                                        crash_any=col is not None and col["t"] <= X["t"][min(i_end, len(X) - 1)], exited=exited)))
    return runs


def rollout(runs, vparams_list, device="cpu", dtype=torch.float32, steps=None, fit_keys=(), base=None):
    """vparams_list: [VehicleParams 또는 dict(맞추는 키만)] P 개. 반환: dict(V [P,R,L], H [P,R,L], crash [P,R], d_stop [P,R], ...)."""
    P, R = len(vparams_list), len(runs); N = P * R
    L = steps or min(600, max(len(r["ref_v"]) for r in runs) + 50)
    base = vparams_list[0] if isinstance(vparams_list[0], VehicleParams) else (base or VehicleParams())
    sim = Surrogate(N, base, device=device, dtype=dtype)
    if not isinstance(vparams_list[0], VehicleParams):
        for k in fit_keys:
            sim.P[k] = torch.tensor([pp[k] for pp in vparams_list for _ in range(R)], device=device, dtype=dtype)
    rep = lambda key: torch.tensor([float(r[key]) for r in runs], device=device, dtype=dtype).repeat(P)
    s0 = torch.tensor([r["s0"] for r in runs], device=device, dtype=dtype).repeat(P, 1)
    lane = torch.tensor([r["lane"] for r in runs], device=device, dtype=dtype).repeat(P, 1)
    sim.set_state(vx=s0[:, 0], vy=s0[:, 1], r=s0[:, 2])
    # 제어 파라미터: 고정 게인 주행과 후보 주행이 섞여 있다 → 둘 다 제어표 경로로 표현한다
    # 고정 게인 = FIXED + a_stop 1.0 m/s² (a_stop_scale 은 env 마다 1/(μ̂g) 가 되도록 따로 처리)
    fixed_mask = torch.tensor([r["gains"] is None for r in runs], device=device).repeat(P)
    from icepredict.pi.gain_lut import FIXED
    g = {}
    for k in PARAM_NAMES:
        vals = [(r["gains"][k] if r["gains"] else (FIXED[k] if k != "a_stop_scale" else 1.0)) for r in runs]
        g[k] = torch.tensor(vals, device=device, dtype=dtype).repeat(P)
    ctl = SlipControllerV(N, DT, 2.0, gains=g, mu_est=True, device=device, dtype=dtype)
    ctl.fixed_mask = fixed_mask
    ctl.emerg[:] = True; ctl.armed[:] = False
    mu_i, d_ice, gl, gr, gap0 = rep("mu"), rep("d_ice"), rep("gl"), rep("gr"), rep("gap0")
    mu_d = sim.P["mu_dry"] * torch.ones_like(mu_i)
    brake_prev = rep("brake0"); trav = torch.zeros(N, device=device, dtype=dtype)
    psi_l, y_l = lane[:, 0], lane[:, 1]
    from icepredict.sim.road import ArcLane, wrap
    kap = rep("kappa")
    # 차로 기준점: 차량(원점) 오른쪽 lat_off 위치의 차로 중심 — 오른손 좌표 (0, −lat_off) 를 차로 방향 법선으로 근사
    road = ArcLane(kap, -y_l * torch.sin(psi_l), y_l * torch.cos(psi_l), psi_l)
    lead_s = torch.where(gap0 < 900, gap0 + CAR_L, torch.full_like(gap0, 1e6))
    V = torch.zeros(N, L, device=device, dtype=dtype); H = torch.zeros(N, L, device=device, dtype=dtype)
    crash = torch.zeros(N, dtype=torch.bool, device=device); done = crash.clone()
    d_stop = torch.zeros(N, device=device, dtype=dtype); z = torch.zeros(N, device=device, dtype=dtype)
    for k in range(L):
        along, dlat, psiL = road.frenet(sim.X, sim.Y)
        lane_err = wrap(sim.psi - psiL)                                   # CARLA 부호 lane_err = 오른손 (ψ − ψ_L)
        lat = dlat - 3.5 * torch.round(dlat / 3.5)                        # 가장 가까운 차로 중심 기준 (CARLA lane_state 와 같다)
        head = wrap(sim.psi - psi_l)                                      # 비교용: 확정 순간 차로 방향 대비 차체 방향
        spd = sim.speed
        V[:, k] = spd; H[:, k] = head
        in_lane = dlat.abs() < 1.75 + 0.2
        gf = torch.where((lead_s < 1e5) & in_lane, lead_s - along - CAR_L, torch.full_like(z, 999.0))
        _, act, b, st = ctl.board(-sim.ay_b, -sim.r, spd, z, sim.ax_b, brake_prev, lane_err, lat, gf, gl, gr, dlat, z, step_idx=k)
        b = torch.where(done, torch.ones_like(b), b); st = torch.where(done, z, st)
        muf = torch.where(trav + 1.4 < d_ice, mu_i, mu_d); mur = torch.where(trav - 1.6 < d_ice, mu_i, mu_d)
        sim.step(st, b, muf, mur)
        for nm in ("vx", "vy", "r"):
            setattr(sim, nm, torch.where(done, torch.zeros_like(getattr(sim, nm)), getattr(sim, nm)))
        trav = trav + spd * DT; brake_prev = b
        hit = (lead_s < 1e5) & in_lane & (lead_s - along - CAR_L < 0.0) & ~done
        crash = crash | hit
        d_stop = torch.where(~done, d_stop + sim.speed * DT, d_stop)
        done = done | hit | ((sim.speed < 0.3) & (k > 3))
    return dict(V=V.view(P, R, L), H=H.view(P, R, L), crash=crash.view(P, R), d_stop=d_stop.view(P, R))


def traj_loss(out, runs, w_head=3.0):
    P, R, L = out["V"].shape
    dev = out["V"].device
    loss = torch.zeros(P, R, device=dev)
    for j, r in enumerate(runs):
        n = min(len(r["ref_v"]), L)
        if n < 5: continue
        rv = torch.tensor(r["ref_v"][:n], device=dev, dtype=out["V"].dtype); rh = torch.tensor(r["ref_h"][:n], device=dev, dtype=out["V"].dtype)
        loss[:, j] = ((out["V"][:, j, :n] - rv) ** 2).mean(1) + w_head * ((out["H"][:, j, :n] - rh) ** 2).mean(1)
    return loss
