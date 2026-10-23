#!/usr/bin/env python3
"""대리 모델을 '직선 빙판 위 앞차' 영역(s9: 운전자 제동 → 저마찰 확정 → 정지/회피)에서 검증한다.
4단계 검증(s4d)은 곡선·보드 조향 영역이라, 제어표 탐색이 실제로 쓰는 영역은 따로 확인해야 한다.
지표: 앞차 충돌 여부 일치, 최대 횡변위 차이(회피 성공의 대리), 속도 감소량 오차.
사용: python validate_s9.py --fit surrogate_fit.json --runs DIR --mu-table mu_table.json --out s9_valid.json
"""
import argparse, json, pathlib, sys
import numpy as np, torch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from icepredict.sim import closed_replay  # noqa: E402
from icepredict.sim.surrogate import VehicleParams  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--fit", required=True); ap.add_argument("--runs", required=True)
ap.add_argument("--mu-table", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
MU = {float(k): v for k, v in json.loads(pathlib.Path(a.mu_table).read_text())["friction_to_mu"].items()}
fit = json.loads(pathlib.Path(a.fit).read_text())["params"]
R = closed_replay.load_runs([a.runs], MU, pattern=r"state_(s9_ab)_(.+)\.csv")
vp = VehicleParams(**{k: v for k, v in fit.items() if hasattr(VehicleParams, k)})
o = closed_replay.rollout(R, [vp], device="cuda" if torch.cuda.is_available() else "cpu", dtype=torch.float64)
rows = []
for j, r in enumerate(R):
    n = r["n_cmp"]; V = o["V"][0, j, :n].cpu().numpy()
    # 대리 모델 충돌도 CARLA 비교 구간(n 샘플) 안에서만 센다 — CARLA 주행이 시간 상한으로 끝나면 그 뒤는 모른다
    rows.append(dict(tag=r["tag"], mu=r["mu"], kph=r["kph"], assist=r["assist"], carla_crash=r["carla"]["lead_crash"], sur_crash=bool(o["crash_step"][0, j] < n),
                     carla_lat=r["carla"]["lat_max"], sur_lat=float(o["lat_max"][0, j]), carla_dv=float(r["ref_v"][0] - r["ref_v"][n - 1]), sur_dv=float(V[0] - V[-1])))
agree = np.mean([x["carla_crash"] == x["sur_crash"] for x in rows])
dl = np.array([abs(x["carla_lat"] - x["sur_lat"]) for x in rows])
dv = np.array([abs(x["sur_dv"] / max(x["carla_dv"], 0.3) - 1) for x in rows])
summ = {"n": len(rows), "lead_crash_agree": float(agree), "lat_diff_median_m": float(np.median(dl)), "lat_diff_le1m": float(np.mean(dl <= 1.0)),
        "dv_err_median": float(np.median(dv))}
for k in (40.0, 60.0):
    s = [x for x in rows if x["kph"] == k]
    if s: summ[f"crash_agree_{int(k)}"] = float(np.mean([x["carla_crash"] == x["sur_crash"] for x in s]))
for x in rows[:60]:
    print(f"{x['tag']:40s} 충돌 {int(x['carla_crash'])}/{int(x['sur_crash'])}  최대 횡변위 {x['carla_lat']:5.2f}/{x['sur_lat']:5.2f} m  Δv {x['carla_dv']:5.2f}/{x['sur_dv']:5.2f}")
print(json.dumps(summ, ensure_ascii=False))
pathlib.Path(a.out).write_text(json.dumps({"summary": summ, "rows": rows}, indent=1, ensure_ascii=False))
