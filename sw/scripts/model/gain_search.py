#!/usr/bin/env python3
"""5단계: 대리 모델 위에서 마찰·속도 셀별 제어 파라미터(제어표 한 칸)를 찾는다.

  · 셀 = μ_eff 8구간 × 속도 6구간(30~80 km/h). 셀마다 같은 시나리오 세트(S개)로 후보를 평가한다.
  · 시나리오의 실제 μ 는 이웃 칸까지(±1칸) 걸쳐 뽑는다 — μ̂ 가 틀려도 버티는 값을 고르기 위해서다.
  · 점수 = 최악 10 %(CVaR) 비용. 평균이 아니다 (안전 기능은 꼬리가 기준이다).
  · 랜덤 서치(초기) → CMA-ES (셀마다 독립). 모든 셀·후보·시나리오를 한 텐서로 묶어 GPU 에서 한 번에 굴린다.
  · 같은 시나리오로 현재 보드 고정 게인도 평가해 기준선으로 남긴다.
산출: <out>/cells.json (셀별 상위 후보·점수·시드), <out>/config.json
사용: python gain_search.py --fit surrogate_fit.json --out ~/icepredict/logs/gain_sched/sweep_YYYYMMDD
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time

import numpy as np
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from icepredict.sim.gs_controller import BOUNDS, FIXED, PARAM_NAMES  # noqa: E402
from icepredict.sim.gs_scenario import ScenarioSet, cost, cvar, sample_scenarios, simulate  # noqa: E402
from icepredict.sim.surrogate import VehicleParams  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--fit", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--mu-axis", default="0.02,0.034,0.057,0.096,0.163,0.276,0.47,0.8")
ap.add_argument("--v-axis", default="30,40,50,60,70,80")
ap.add_argument("--scen", type=int, default=128, help="셀당 시나리오 수")
ap.add_argument("--pop", type=int, default=24, help="CMA-ES 후보 수 (셀당, 세대당)")
ap.add_argument("--random", type=int, default=48, help="초기 랜덤 서치 후보 수 (셀당)")
ap.add_argument("--gens", type=int, default=20)
ap.add_argument("--alpha", type=float, default=0.10)
ap.add_argument("--w", default='{"crash": 10, "spin": 5, "departure": 2, "stop": 1, "lat": 0.5}')
ap.add_argument("--seed", type=int, default=20261023)
ap.add_argument("--cells", default=None, help="일부 셀만 (예: 0-0,3-2)")
ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
ap.add_argument("--chunk", type=int, default=400000, help="한 번에 굴릴 env 수 상한 (GPU 메모리)")
a = ap.parse_args()
dev = torch.device(a.device)
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
W = json.loads(a.w)
MU = [float(x) for x in a.mu_axis.split(",")]; VK = [float(x) for x in a.v_axis.split(",")]
fit = json.loads(pathlib.Path(a.fit).read_text())["params"]
VP = VehicleParams(**{k: v for k, v in fit.items() if hasattr(VehicleParams, k)})
MU_DRY = fit["mu_dry"]
cells = [(i, j) for i in range(len(MU)) for j in range(len(VK))]
if a.cells:
    want = {tuple(int(x) for x in c.split("-")) for c in a.cells.split(",")}
    cells = [c for c in cells if c in want]
LO = np.array([BOUNDS[k][0] for k in PARAM_NAMES]); HI = np.array([BOUNDS[k][1] for k in PARAM_NAMES])


def cell_ranges(i, j):
    mlo = MU[max(i - 1, 0)] if i > 0 else MU[0] * 0.8
    mhi = MU[min(i + 1, len(MU) - 1)] if i < len(MU) - 1 else min(MU[-1] * 1.15, 1.0)
    vlo = max(VK[j] - 5.0, 20.0); vhi = VK[j] + 5.0
    return mlo, mhi, vlo, vhi


# 셀마다 고정 시나리오 세트 (시드 고정 → 재현 가능, 후보끼리 같은 시나리오로 비교)
gen = torch.Generator(device=dev); gen.manual_seed(a.seed)
SC = {}
for (i, j) in cells:
    mlo, mhi, vlo, vhi = cell_ranges(i, j)
    SC[(i, j)] = sample_scenarios(a.scen, mlo, mhi, vlo, vhi, gen, dev)


def tile(sc: ScenarioSet, reps):
    return ScenarioSet(**{k: getattr(sc, k).repeat(reps) for k in sc.__dataclass_fields__})


def cat(scs):
    return ScenarioSet(**{k: torch.cat([getattr(s, k) for s in scs]) for k in ScenarioSet.__dataclass_fields__})


def evaluate(cands, fixed=False):
    """cands: {cell: np.array [P, 10]} → {cell: (cvar [P], mean [P], 상세)}. fixed=True 면 보드 고정 게인 (cands 무시, P=1)."""
    jobs = []
    for c in cells:
        P = 1 if fixed else len(cands[c])
        jobs.append((c, P))
    res = {}
    # env 수가 상한을 넘지 않게 셀을 묶어서 굴린다
    batch, nb = [], 0
    def flush(batch):
        scs, gains_parts = [], {k: [] for k in PARAM_NAMES}
        for c, P in batch:
            scs.append(tile(SC[c], P))
            if not fixed:
                g = torch.tensor(cands[c], device=dev, dtype=torch.float32)        # [P, 10]
                for t, k in enumerate(PARAM_NAMES):
                    gains_parts[k].append(g[:, t].repeat_interleave(a.scen))
        sc = cat(scs)
        gains = None if fixed else {k: torch.cat(v) for k, v in gains_parts.items()}
        r = simulate(sc, gains, VP, MU_DRY, dev, fixed=fixed)
        cst = cost(r, W)
        off = 0
        for c, P in batch:
            n = P * a.scen
            cc = cst[off:off + n].view(P, a.scen)
            det = r["detected"][off:off + n].view(P, a.scen)
            # 감지가 안 된 시나리오(그냥 지나감)는 제어기와 무관 — 점수에서 뺀다 (비용 0 으로 두면 CVaR 를 희석한다)
            cc_m = torch.where(det, cc, torch.full_like(cc, float("nan")))
            cv = torch.stack([cvar(row[~torch.isnan(row)], a.alpha) if (~torch.isnan(row)).any() else torch.tensor(0.0, device=dev) for row in cc_m])
            mean = torch.nanmean(cc_m, dim=1)
            stats = {k: r[k][off:off + n].view(P, a.scen).float().mean(1).cpu().numpy() for k in ("crash", "spin", "departure", "detected")}
            stats["d_stop"] = torch.nanmean(torch.where(det, r["d_stop"][off:off + n].view(P, a.scen), torch.full_like(cc, float("nan"))), 1).cpu().numpy()
            res[c] = (cv.cpu().numpy(), mean.cpu().numpy(), stats)
            off += n
    for c, P in jobs:
        if nb + P * a.scen > a.chunk and batch:
            flush(batch); batch, nb = [], 0
        batch.append((c, P)); nb += P * a.scen
    if batch: flush(batch)
    return res


def fixed_as_lut(i):
    """현재 고정 게인을 제어표 경로 파라미터로 옮긴 값 (CMA 시작점). a_stop 1.0 m/s² 를 μ 칸에 맞춰 환산."""
    v = dict(FIXED); v["a_stop_scale"] = float(np.clip(1.0 / (MU[i] * 9.80665), *BOUNDS["a_stop_scale"]))
    return np.array([v[k] for k in PARAM_NAMES])


def main():
    import cma
    t0 = time.time()
    (out / "config.json").write_text(json.dumps({**vars(a), "w": W, "mu_axis": MU, "v_axis": VK, "fit": fit, "bounds": BOUNDS,
                                                 "params": PARAM_NAMES}, indent=1, ensure_ascii=False))
    base = evaluate(None, fixed=True)
    print(f"[search] 기준선(보드 고정 게인) 평가 {time.time()-t0:.0f}s", flush=True)
    rng = np.random.default_rng(a.seed)
    # 1) 랜덤 서치 + 고정 게인 환산값
    cand = {}
    for c in cells:
        R = LO + (HI - LO) * rng.random((a.random, len(PARAM_NAMES)))
        R[0] = fixed_as_lut(c[0])
        cand[c] = R
    r0 = evaluate(cand)
    hist = {c: [(float(r0[c][0][p]), cand[c][p].tolist(), float(r0[c][1][p])) for p in range(len(cand[c]))] for c in cells}
    print(f"[search] 랜덤 서치 {a.random}개 × 셀 {len(cells)} 끝 ({time.time()-t0:.0f}s)", flush=True)
    # 2) CMA-ES — 랜덤 서치 최고값에서 시작
    es = {}
    for c in cells:
        best = min(hist[c], key=lambda h: h[0])
        x0 = (np.array(best[1]) - LO) / (HI - LO)
        es[c] = cma.CMAEvolutionStrategy(np.clip(x0, 0.02, 0.98), 0.15,
                                         {"popsize": a.pop, "bounds": [0, 1], "seed": a.seed % 10000 + c[0] * 10 + c[1], "verbose": -9})
    for g in range(a.gens):
        asks = {c: es[c].ask() for c in cells}
        cand = {c: LO + (HI - LO) * np.clip(np.array(asks[c]), 0, 1) for c in cells}
        r = evaluate(cand)
        for c in cells:
            es[c].tell(asks[c], r[c][0].tolist())
            hist[c] += [(float(r[c][0][p]), cand[c][p].tolist(), float(r[c][1][p])) for p in range(len(cand[c]))]
        bests = [min(h[0] for h in hist[c]) for c in cells]
        print(f"[search] 세대 {g:2d}  셀 평균 최고 CVaR {np.mean(bests):.3f}  ({time.time()-t0:.0f}s)", flush=True)
    # 3) 셀별 상위 후보 — 다시 평가해 통계까지 남긴다
    top = {c: sorted(hist[c], key=lambda h: h[0])[:5] for c in cells}
    cand = {c: np.array([t[1] for t in top[c]]) for c in cells}
    rf = evaluate(cand)
    res = []
    for c in cells:
        i, j = c
        bcv, bmean, bst = base[c]
        res.append({"cell": [i, j], "mu": MU[i], "kph": VK[j], "mu_range": cell_ranges(i, j)[:2],
                    "baseline": {"cvar": float(bcv[0]), "mean": float(bmean[0]), **{k: float(v[0]) for k, v in bst.items()}},
                    "top": [{"params": dict(zip(PARAM_NAMES, cand[c][p].tolist())), "cvar": float(rf[c][0][p]), "mean": float(rf[c][1][p]),
                             **{k: float(v[p]) for k, v in rf[c][2].items()}} for p in range(len(cand[c]))]})
        b = res[-1]["top"][0]
        print(f"[search] 셀 μ {MU[i]:.3f} {VK[j]:.0f}km/h  고정 CVaR {bcv[0]:6.2f} (충돌 {bst['crash'][0]*100:3.0f}% 스핀 {bst['spin'][0]*100:3.0f}%)"
              f"  →  최적 {b['cvar']:6.2f} (충돌 {b['crash']*100:3.0f}% 스핀 {b['spin']*100:3.0f}%)  B_HI {b['params']['B_HI']:.2f} B_LO {b['params']['B_LO']:.2f}"
              f" PH {b['params']['PULSE_HALF']:.0f} Kp {b['params']['Kp']:.2f}", flush=True)
    (out / "cells.json").write_text(json.dumps({"seed": a.seed, "cells": res, "elapsed_s": time.time() - t0}, indent=1, ensure_ascii=False))
    print(f"[search] 저장 {out/'cells.json'}  ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
