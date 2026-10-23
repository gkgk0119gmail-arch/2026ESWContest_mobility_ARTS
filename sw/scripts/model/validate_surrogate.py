#!/usr/bin/env python3
"""4단계 검증: 보정한 대리 모델을 보정에 쓰지 않은 CARLA 폐루프 주행 48회와 비교한다.

  · 비상 제어 주행(ccore, 24회): CARLA 에서 미끄러짐이 확정된 순간의 상태(속도·횡속도·요레이트·차선 오차·횡위치)에서
    대리 모델 + 같은 제어기(gs_controller, 보드 C 와 샘플 단위 동치)로 폐루프를 돌린다.
    빙판 끝: CARLA 궤적에서 앞축이 빙판을 벗어난 지점까지의 주행 거리 (안 벗어났으면 끝까지 빙판).
  · 방어 없음(nodef, 24회): 빙판 진입 순간부터 기록된 입력(자율주행 조향·가속 → 제어 상실 뒤 0)을 그대로 넣는다.
지표: 정지거리(확정 → 정지 주행 거리), 최대 방향오차(차선 대비), 스핀(> 86°), 차선 이탈(> 34° 또는 차선 중심 2.5 m).
기준: μ별 정지거리 오차 ≤ 15 %, 최대 방향오차 차이 ≤ 10°, 스핀 여부 일치 ≥ 90 %.
사용: python validate_surrogate.py --fit surrogate_fit.json --closed DIR --mu-table mu_table.json --out surrogate_valid.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

import numpy as np
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from icepredict.sim.surrogate import Surrogate, VehicleParams, mu_of_friction  # noqa: E402
from icepredict.sim.gs_controller import SlipControllerV  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--fit", required=True); ap.add_argument("--closed", required=True); ap.add_argument("--mu-table", required=True)
ap.add_argument("--closed-dirs", nargs="+", default=None)
ap.add_argument("--out", required=True); ap.add_argument("--device", default="cpu")
a = ap.parse_args()
a.closed_dirs = a.closed_dirs or [a.closed]
MU = {float(k): v for k, v in json.loads(pathlib.Path(a.mu_table).read_text())["friction_to_mu"].items()}
FITP = json.loads(pathlib.Path(a.fit).read_text())["params"]
DT = 0.02
SPIN, DEP_HEAD, DEP_LAT = 1.5, 0.6, 2.5


def wrap(x): return (x + np.pi) % (2 * np.pi) - np.pi


def make_sim(n):
    vp = VehicleParams(**{k: v for k, v in FITP.items() if hasattr(VehicleParams, k)})
    return Surrogate(n, vp, device=a.device, dtype=torch.float64)


def carla_metrics(X, t0, events):
    sel = X["t"] >= t0 - 1e-6
    t = X["t"][sel]; v = np.hypot(X["vx"][sel], X["vy"][sel]); le = X["lane_err"][sel]
    stop_i = np.argmax(v < 0.3) if (v < 0.3).any() else len(v) - 1
    d_stop = float(np.sum(v[:stop_i]) * DT)
    crash = next((e for e in events if e["event"] == "collision" and e["t"] >= t0 - 1e-6), None)
    dep = any(e["event"] == "lane_departure" and e["t"] >= t0 - 0.05 for e in events)
    return dict(d_stop=d_stop, stopped=bool((v < 0.3).any()), head_max=float(np.degrees(np.abs(le[:stop_i + 1]).max())),
                spin=bool(np.abs(le).max() > SPIN), departure=dep, crash=crash is not None)


def run_nodef(run):
    """방어 없음: 제어 상실(자율주행 해제) 순간부터 입력 0 으로 굴린다 — CARLA 도 그 뒤로는 입력이 정확히 0 이다.
    (빙판 진입부터 자율주행 조향을 개루프로 재생하면, 원래 되먹임이던 보정 조향이 오차를 키워 비교가 불공정하다.)
    제어를 잃지 않은 주행(그냥 통과)은 빙판 진입부터 기록 입력을 재생하되 비교는 방향오차·스핀만."""
    X, ev = run["X"], run["events"]
    lost = next((e for e in ev if e["event"] == "control_lost"), None)
    ins = X["inside_f"] > 0.5
    if not ins.any(): return None
    i0 = int(np.argmin(np.abs(X["t"] - lost["t"]))) if lost else int(np.argmax(ins))
    out_i = np.where((X["inside_f"][i0:] < 0.5) | (X["inside_r"][i0:] < 0.5))[0]
    i1 = i0 + int(out_i[0]) if len(out_i) else len(X) - 1                  # 빙판(트리거)을 벗어나기 전까지만
    if i1 - i0 < 10: return None
    psi_c = -np.unwrap(X["yaw"]); head_c = (psi_c - psi_c[i0]) + float(X["lane_err"][i0])
    sim = make_sim(1)
    sim.set_state(vx=float(X["vx"][i0]), vy=float(-X["vy"][i0]), r=float(-X["r"][i0]))
    psi_lane = -float(X["lane_err"][i0]); y_lane = -float(X["lat_off"][i0])
    mu_i = run["mu"]; mu_d = FITP["mu_dry"]; out = []
    T = lambda x: torch.tensor([x], dtype=torch.float64)
    # 빙판 끝까지 거리 (앞축, CARLA 궤적 기준) — 제어 상실 뒤에는 CARLA 궤적과 대리 모델 궤적이 갈라지므로 주행 거리로 맞춘다
    v = np.hypot(X["vx"], X["vy"]); s = np.cumsum(v) * DT; s -= s[i0]
    later = np.where(X["inside_f"][i0:] < 0.5)[0]; d_ice = float(s[i0 + later[0]]) if len(later) else 1e9
    traveled = 0.0
    for i in range(i0, i1):
        c, s_ = math.cos(psi_lane), math.sin(psi_lane)
        lat_left = -float(sim.X[0]) * s_ + float(sim.Y[0]) * c - y_lane
        head = wrap(float(sim.psi[0]) - psi_lane + float(X["lane_err"][i0]) * 0 )
        out.append((float(sim.speed[0]), head, lat_left))
        if lost:
            st, br, th = 0.0, 0.0, 0.0
            muf = mu_i if traveled + 1.4 < d_ice else mu_d; mur = mu_i if traveled - 1.6 < d_ice else mu_d
        else:
            st, br, th = float(X["steer"][i + 1]), float(X["brake"][i + 1]), float(X["throttle"][i + 1])
            muf = mu_i if X["inside_f"][i + 1] > 0.5 else mu_d; mur = mu_i if X["inside_r"][i + 1] > 0.5 else mu_d
        sim.step(T(st), T(br), T(muf), T(mur), T(th))
        traveled += float(sim.speed[0]) * DT
        if float(sim.speed[0]) < 0.2 and i > i0 + 5: break
    o = np.array(out)
    le = head_c[i0:i1]
    # 방향오차는 확정(제어 상실) 순간 차선 방향 대비 — 차체 요각 누적 (lane_err 는 차로를 건너면 튄다)
    head_abs = np.abs(o[:, 1] + 0.0)
    car = dict(head_max=float(np.degrees(np.abs(le).max())), spin=bool(np.abs(le).max() > SPIN),
               departure=any(e["event"] == "lane_departure" for e in ev), lost=bool(lost))
    sur = dict(head_max=float(np.degrees(head_abs.max())), spin=bool(head_abs.max() > SPIN),
               departure=bool((head_abs > DEP_HEAD).any() or (np.abs(o[:, 2]) > DEP_LAT).any()) if not lost else car["departure"])
    return car, sur


from icepredict.sim import closed_replay  # noqa: E402


def is_train(r):
    """교차검증 분할 (fit_surrogate.is_train 과 같다): 고정 게인은 40 km/h, 후보 게인은 짝수 번호만 보정에 썼다."""
    if r["gains"] is None: return r["kph"] == 40
    return int(r["tag"].rsplit("_g", 1)[1]) % 2 == 0


rows = []
# 1) 폐루프 비상 제어 (고정 게인 + 후보 게인) — closed_replay 로 한꺼번에
cl = closed_replay.load_runs(a.closed_dirs, MU)
vp = VehicleParams(**{k: v for k, v in FITP.items() if hasattr(VehicleParams, k)})
oc = closed_replay.rollout(cl, [vp], device=a.device, dtype=torch.float64)
for j, r in enumerate(cl):
    V = oc["V"][0, j].cpu().numpy(); Hh = oc["H"][0, j].cpu().numpy()
    n = r["n_cmp"]                                           # CARLA 가 빙판(트리거)을 벗어나기 전까지만 비교
    V = V[:n]; Hh = Hh[:n]
    stop_i = int(np.argmax(V < 0.3)) if (V < 0.3).any() else len(V) - 1
    sur = dict(d_stop=float(np.sum(V[:stop_i]) * 0.02), stopped=bool((V < 0.3).any()), head_max=float(np.degrees(np.abs(Hh[:stop_i + 1]).max())),
               spin=bool(np.abs(Hh[:stop_i + 1]).max() > SPIN), crash=bool(oc["crash"][0, j]),
               dv=float(V[0] - V[min(n, len(V)) - 1]))
    r["carla"]["dv"] = float(r["ref_v"][0] - r["ref_v"][n - 1])
    train = is_train(r)
    rows.append(dict(mode="ccore" if r["gains"] is None else "cand", tag=r["tag"], fr=r["fr"], mu=r["mu"], kph=r["kph"], carla=r["carla"], sur=sur,
                     train=train, lead=r["gap0"] < 900))
    c_ = r["carla"]
    print(f"{rows[-1]['mode']:5s} μ {r['mu']:.3f} {r['kph']:.0f}km/h {'학습' if train else '검증'}  Δv {c_['dv']:5.2f} / {sur['dv']:5.2f} m/s ({n*0.02:.1f}s)"
          f"  최대 방향오차 {c_['head_max']:6.1f} / {sur['head_max']:6.1f}°  스핀 {int(c_['spin'])}/{int(sur['spin'])}"
          + (f"  앞차 충돌 {int(c_['crash'])}/{int(sur['crash'])}" if r["gap0"] < 900 else ""), flush=True)
# 2) 방어 없음 — 제어 상실 이후
for f in sorted(pathlib.Path(a.closed).glob("state_s4c_nodef_*.csv")):
    m = re.match(r"state_s4c_nodef_f([0-9.]+)_k(\d+)\.csv", f.name)
    fr, kph = float(m.group(1)), int(m.group(2))
    X = np.genfromtxt(f, delimiter=",", names=True, dtype=None, encoding=None)
    ev = json.loads((pathlib.Path(a.closed) / f"events_s4c_nodef_f{fr:g}_k{kph}.json").read_text())["events"]
    res = run_nodef(dict(mode="nodef", fr=fr, kph=kph, X=X, events=ev, mu=MU.get(fr, mu_of_friction(fr, MU))))
    if res is None: continue
    car, sur = res
    rows.append(dict(mode="nodef", fr=fr, mu=MU.get(fr, mu_of_friction(fr, MU)), kph=kph, carla=car, sur=sur, train=False))
    print(f"nodef μ {rows[-1]['mu']:.3f} {kph}km/h  {'제어상실' if car.get('lost') else '통과    '}  최대 방향오차 {car['head_max']:6.1f} / {sur['head_max']:6.1f}°  스핀 {int(car['spin'])}/{int(sur['spin'])}", flush=True)

# 기준 판정 — 사용 범위: 감지 이후 폐루프(비상 제어) + 제어 상실 이후(방어 없음). 감지 없이 통과한 주행은 범위 밖으로 따로 낸다.
# 방향오차 기준은 둘 다 스핀하지 않은 경우에만 적용한다 (스핀 뒤 최대 방향오차는 혼돈적이라 크기 비교가 의미 없다).
# 교차검증: 비상 제어 주행 중 30·50 km/h 는 보정에 썼고, 40·60 km/h 만 보정에 안 쓴 검증 몫이다.
for r in rows:
    r["in_domain"] = r["mode"] in ("ccore", "cand") or bool(r["carla"].get("lost"))
    r["held_out"] = not r.get("train", False)
dom = [r for r in rows if r["in_domain"]]
def crit_of(sel):
    by_mu = {}
    for r in sel:
        if r["mode"] in ("ccore", "cand") and r["carla"]["stopped"] and r["carla"]["d_stop"] > 1.0 and not r["carla"].get("crash_any"):
            by_mu.setdefault(round(r["mu"], 3), []).append((r["sur"]["d_stop"], r["carla"]["d_stop"]))
    stop_err = {k: float(abs(sum(s for s, _ in v) / sum(c for _, c in v) - 1)) for k, v in by_mu.items()}   # μ 구간 합계 거리 기준
    # 정지 전에 충돌한 주행(곡선 빙판에서는 거의 전부)은 정지거리가 없다 → 같은 비교 구간의 속도 감소량 Δv 로 감속 능력을 본다
    by_mu_dv = {}
    for r in sel:
        if r["mode"] in ("ccore", "cand") and "dv" in r["carla"] and r["carla"]["dv"] > 0.3:
            by_mu_dv.setdefault(round(r["mu"], 3), []).append((r["sur"]["dv"], r["carla"]["dv"]))
    dv_err = {k: float(abs(sum(s for s, _ in v) / sum(c for _, c in v) - 1)) for k, v in by_mu_dv.items()}
    if not stop_err: stop_err = dv_err
    ns = [r for r in sel if not r["carla"]["spin"] and not r["sur"]["spin"]]
    hd = [abs(r["carla"]["head_max"] - r["sur"]["head_max"]) for r in ns]
    c = {"stop_err_by_mu": stop_err, "dv_err_by_mu": dv_err, "stop_metric": "정지거리" if by_mu else "속도 감소량 Δv (정지 전 충돌)", "stop_err_max": max(stop_err.values()) if stop_err else None,
         "head_diff_median": float(np.median(hd)) if hd else None, "head_diff_le10_frac": float(np.mean(np.array(hd) <= 10.0)) if hd else None,
         "n_nospin": len(ns), "spin_agree": float(np.mean([r["carla"]["spin"] == r["sur"]["spin"] for r in sel])), "n": len(sel)}
    c["pass"] = bool(c["stop_err_max"] is not None and c["stop_err_max"] <= 0.15 and (c["head_diff_le10_frac"] or 0) >= 0.9 and c["spin_agree"] >= 0.9)
    return c
crit = {"in_domain": crit_of(dom), "in_domain_held_out": crit_of([r for r in dom if r["held_out"]]),
        "out_of_domain_passed_runs": crit_of([r for r in rows if not r["in_domain"]])}
for k, c in crit.items():
    print(f"\n[기준 · {k}] n={c['n']}  μ별 {c.get('stop_metric', '정지거리')} 오차:", {kk: f"{vv*100:.0f}%" for kk, vv in sorted(c["stop_err_by_mu"].items())})
    if c["head_diff_median"] is not None:
        print(f"   방향오차 차이(비스핀 {c['n_nospin']}건) 중앙값 {c['head_diff_median']:.1f}°, ≤10° {c['head_diff_le10_frac']*100:.0f}%,"
              f"  스핀 일치 {c['spin_agree']*100:.0f}%  → {'통과' if c['pass'] else '미달'}")
json.dump({"criteria": crit, "rows": rows, "fit": FITP}, open(a.out, "w"), indent=1, ensure_ascii=False, default=float)
