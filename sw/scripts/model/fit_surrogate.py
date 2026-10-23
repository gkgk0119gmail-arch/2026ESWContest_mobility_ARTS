#!/usr/bin/env python3
"""4단계: 대리 차량 모델을 CARLA 로그로 보정하고 오차를 보고한다.

보정(fit) 데이터 = 2단계 보정 실험(개루프 시험 81회: 일정 제동·계단 조향·펄스·결합 × 마찰 9종).
검증(hold-out) 데이터 = 폐루프 주행 48회 (보드 C 코어 비상 제어 24 + 방어 없음 24, 마찰 6종 × 30~60 km/h).
  · 비상 제어 주행: CARLA 에서 미끄러짐이 확정된 순간의 상태에서 대리 모델 + 같은 제어기(torch, C 와 동치)로
    폐루프를 돌려 정지거리·최대 방향오차·스핀·차선이탈을 비교한다.
  · 방어 없음 주행: 빙판 진입 순간부터 기록된 입력(자율주행 → 제어 상실 후 0)을 그대로 넣어 비교한다.
기준 (작업 지시 4단계): μ별 정지거리 오차 ≤ 15 %, 최대 방향오차 차이 ≤ 10°, 스핀 여부 일치 ≥ 90 %.

CMA-ES 후보 전체 × 시험 전체를 한 번에 굴린다 (GPU). μ 는 2단계 표로 고정하고 맞추지 않는다 — 맞추면 보정이 아니라 암기다.
사용: python fit_surrogate.py --calib DIR... --closed DIR --mu-table mu_table.json --out surrogate_fit.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
import time

import numpy as np
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from icepredict.sim.surrogate import Surrogate, VehicleParams, mu_of_friction  # noqa: E402
from icepredict.sim.gs_controller import SlipControllerV  # noqa: E402
from icepredict.sim import closed_replay  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--calib", nargs="+", required=True)
ap.add_argument("--closed", required=True, help="(구) 고정 게인 폐루프 폴더 — --closed-dirs 에 같이 넣는다")
ap.add_argument("--closed-dirs", nargs="+", default=None, help="폐루프 주행 폴더들 (보정 몫은 is_train 이 고른다)")
ap.add_argument("--mu-table", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--gens", type=int, default=40)
ap.add_argument("--pop", type=int, default=24)
ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
ap.add_argument("--max-steps", type=int, default=500)
ap.add_argument("--w-closed", type=float, default=1.0, help="폐루프 손실 가중")
a = ap.parse_args()
a.closed_dirs = a.closed_dirs or [a.closed]
dev = torch.device(a.device)
MU = {float(k): v for k, v in json.loads(pathlib.Path(a.mu_table).read_text())["friction_to_mu"].items()}
MU_DRY = MU[max(MU)]
DT = 0.02

# 맞출 파라미터: 이름, 초기값, 하한, 상한
FIT = [("a", 1.9, 0.7, 2.6), ("Iz", 4700.0, 800.0, 9000.0), ("delta_scale", 1.3, 0.3, 2.2), ("tau_delta", 0.03, 0.0, 0.4),
       ("Cy", 20.0, 0.5, 80.0), ("Cx", 20.0, 0.5, 80.0), ("Iw", 8.0, 0.3, 30.0), ("c_roll", 0.003, 0.0, 0.02),
       ("c_drag", 0.4, 0.0, 1.5), ("E_b", 4500.0, 0.0, 7000.0), ("eb_front", 0.05, 0.0, 1.0)]
# 고정값: 마른 노면 μ 는 2단계 표의 friction 1.0 값(보정 시험이 빙판 구간 안만 써서 데이터로 정해지지 않는다),
# 앞뒤 제동 배분은 CARLA 테슬라가 네 바퀴 모두 700 N·m 라 정확히 0.5.
FIXED_P = {"mu_dry": None, "front_brake_share": 0.5}


def load_calib():
    """보정 실험: 마찰 구간 안(진입 ~ 이탈 0.4 s 전). 입력 u[i] 가 i→i+1 을 만든다."""
    out = []
    for d in a.calib:
        for f in sorted(pathlib.Path(d).glob("f*_*.csv")):
            m = re.match(r"f([0-9.]+)_(.+)\.csv", f.name); fr, name = float(m.group(1)), m.group(2)
            X = np.genfromtxt(f, delimiter=",", names=True)
            ins = X["inside"] > 0.5
            if ins.sum() < 40: continue
            i0 = int(np.argmax(ins)); i1 = len(ins) - int(np.argmax(ins[::-1])) - 1 - 20
            seg = X[i0:i1]
            out.append(dict(name=f"f{fr:g}_{name}", fr=fr, mu=MU.get(fr, mu_of_friction(fr, MU)), kind="calib",
                            s0=(-seg["vy"][0], -seg["r"][0], seg["vx"][0]),
                            steer=seg["steer"][:-1], brake=seg["brake"][:-1], thr=seg["throttle"][:-1],
                            mu_f=np.ones(len(seg) - 1), mu_r=np.ones(len(seg) - 1),            # 전 구간 빙판
                            ref=dict(v=np.hypot(seg["vx"], seg["vy"]), psi=-(seg["yaw"] - seg["yaw"][0]),
                                     X=seg["x"], Y=-seg["y"], yaw0=-seg["yaw"][0])))
    return out


def load_closed():
    runs = []
    for f in sorted(pathlib.Path(a.closed).glob("state_s4_*.csv")):
        m = re.match(r"state_s4_(ccore|nodef)_f([0-9.]+)_k(\d+)\.csv", f.name)
        mode, fr, kph = m.group(1), float(m.group(2)), int(m.group(3))
        X = np.genfromtxt(f, delimiter=",", names=True, dtype=None, encoding=None)
        if len(X) < 50: continue
        ev = json.loads((pathlib.Path(a.closed) / f"events_s4_{mode}_f{fr:g}_k{kph}.json").read_text())["events"]
        runs.append(dict(mode=mode, fr=fr, kph=kph, X=X, events=ev, mu=MU.get(fr, mu_of_friction(fr, MU))))
    return runs


def rollout(trials, params_list):
    """params_list: [P 후보] 각 dict. 반환: [P, T] 손실, 상세 궤적."""
    P, T = len(params_list), len(trials)
    L = min(a.max_steps, max(len(t["steer"]) for t in trials))
    N = P * T
    vp = VehicleParams(mu_dry=MU_DRY, front_brake_share=FIXED_P["front_brake_share"])
    sim = Surrogate(N, vp, device=dev)
    for k, _, _, _ in FIT:
        sim.P[k] = torch.tensor([pp[k] for pp in params_list for _ in range(T)], device=dev, dtype=torch.float32)
    pad = lambda key: torch.tensor(np.stack([np.pad(t[key][:L], (0, max(0, L - len(t[key][:L]))), mode="edge") for t in trials]),
                                   device=dev, dtype=torch.float32).repeat(P, 1)
    S, Bk, Th, Mf, Mr = pad("steer"), pad("brake"), pad("thr"), pad("mu_f"), pad("mu_r")
    mu_ice = torch.tensor([t["mu"] for t in trials], device=dev, dtype=torch.float32).repeat(P)
    mu_dry = sim.P["mu_dry"]
    s0 = torch.tensor([t["s0"] for t in trials], device=dev, dtype=torch.float32).repeat(P, 1)
    sim.set_state(vx=s0[:, 2], vy=s0[:, 0], r=s0[:, 1])
    V = torch.zeros(N, L + 1, device=dev); PS = torch.zeros(N, L + 1, device=dev)
    V[:, 0] = sim.speed
    for i in range(L):
        muf = torch.where(Mf[:, i] > 0.5, mu_ice, mu_dry); mur = torch.where(Mr[:, i] > 0.5, mu_ice, mu_dry)
        sim.step(S[:, i], Bk[:, i], muf, mur, Th[:, i])
        V[:, i + 1] = sim.speed; PS[:, i + 1] = sim.psi
    loss = torch.zeros(P, T, device=dev)
    for j, t in enumerate(trials):
        n = min(len(t["ref"]["v"]), L + 1)
        rv = torch.tensor(t["ref"]["v"][:n], device=dev, dtype=torch.float32)
        rp = torch.tensor(t["ref"]["psi"][:n], device=dev, dtype=torch.float32)
        idx = torch.arange(P, device=dev) * T + j
        ev = ((V[idx, :n] - rv) ** 2).mean(1)
        ep = ((PS[idx, :n] - rp) ** 2).mean(1)
        loss[:, j] = ev + 3.0 * ep                      # 속도 (m/s)² + 방향 (rad)² × 3  (1 m/s ≈ 33°)
    return loss, V, PS


def is_train(r):
    """교차검증 분할: 고정 게인 주행은 40 km/h, 후보 게인 주행은 짝수 번호(g0,g2,g4)만 보정에 쓴다. 나머지는 검증 몫."""
    if r["gains"] is None: return r["kph"] == 40
    return int(r["tag"].rsplit("_g", 1)[1]) % 2 == 0


def to_unit(x):
    return [(x[i] - lo) / (hi - lo) for i, (_, _, lo, hi) in enumerate(FIT)]


def from_unit(u):
    return {k: lo + (hi - lo) * float(np.clip(u[i], 0, 1)) for i, (k, _, lo, hi) in enumerate(FIT)}


def main():
    import cma
    trials = load_calib()
    cl_all = closed_replay.load_runs(a.closed_dirs, MU)
    cl = [r for r in cl_all if is_train(r)]
    print(f"[fit] 보정 시험 {len(trials)}개 + 폐루프 {len(cl)}개(30·50 km/h), μ 범위 {min(t['mu'] for t in trials):.3f}~{max(t['mu'] for t in trials):.3f}", flush=True)
    x0 = to_unit([v for _, v, _, _ in FIT])
    es = cma.CMAEvolutionStrategy(x0, 0.2, {"popsize": a.pop, "bounds": [0, 1], "seed": 1, "verbose": -9})
    t0 = time.time(); best = None
    for g in range(a.gens):
        X = es.ask()
        pl = [from_unit(x) for x in X]
        loss, _, _ = rollout(trials, pl)
        oc = closed_replay.rollout(cl, pl, device=dev, fit_keys=[k for k, *_ in FIT],
                                   base=VehicleParams(mu_dry=MU_DRY, front_brake_share=FIXED_P["front_brake_share"]))
        lc = closed_replay.traj_loss(oc, cl)
        f = (loss.mean(1) + a.w_closed * lc.mean(1)).cpu().numpy().tolist()
        es.tell(X, f)
        i = int(np.argmin(f))
        if best is None or f[i] < best[0]: best = (f[i], pl[i])
        print(f"[fit] 세대 {g:2d}  최저 {min(f):.4f}  ({time.time()-t0:.0f}s)", flush=True)
    fitp = dict(best[1]); fitp.update(mu_dry=MU_DRY, front_brake_share=FIXED_P["front_brake_share"])
    print("[fit] 결과:", {k: round(v, 4) for k, v in fitp.items()}, flush=True)
    # 보정 데이터 오차 (시험별)
    loss, V, PS = rollout(trials, [best[1]])
    calib_rep = []
    for j, t in enumerate(trials):
        n = min(len(t["ref"]["v"]), V.shape[1])
        v_s = V[j, :n].cpu().numpy(); p_s = PS[j, :n].cpu().numpy()
        dv_c = t["ref"]["v"][0] - t["ref"]["v"][n - 1]; dv_s = v_s[0] - v_s[-1]
        calib_rep.append(dict(name=t["name"], mu=t["mu"], dv_carla=float(dv_c), dv_sur=float(dv_s),
                              dpsi_max_carla=float(np.degrees(np.abs(t["ref"]["psi"][:n]).max())),
                              dpsi_max_sur=float(np.degrees(np.abs(p_s).max()))))
    json.dump({"params": fitp, "calib": calib_rep, "fit_loss": best[0]}, open(a.out, "w"), indent=1, ensure_ascii=False)
    print("[fit] 저장", a.out)


if __name__ == "__main__":
    main()
