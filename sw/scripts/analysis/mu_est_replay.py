#!/usr/bin/env python3
"""3단계 검증: 2단계 CARLA 로그를 C 코어 마찰 추정기(SLIP_MU_EST=1, ctypes)에 재생한다.

마찰 구간 진입 순간부터 비상 제어 상태로 두고(sc_force_emerg), 기록된 IMU·속도·제동을 그대로 먹인다.
마찰 추정은 '강' 펄스 제동 샘플에서만 갱신되므로, 보드 제어기와 같은 펄스(0.70/0.35, 16샘플)를 준 pulse 시험과
일정 강제동(0.7·1.0) 시험을 쓴다. 목표: 펄스 3회(≈ 0.96 s) 안에 μ̂ 가 μ_eff ±30 % 에 들어와 머문다.
사용: python3 mu_est_replay.py <mu_calib 폴더들> --table logs/gain_sched/mu_table.json --out logs/gain_sched/mu_est_replay.json
"""
import argparse, json, math, pathlib, re, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from icepredict.pi.slip_ctypes import SlipCoreC

ap = argparse.ArgumentParser(); ap.add_argument("dirs", nargs="+"); ap.add_argument("--table", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--trials", default="pulse,brake_0.7,brake_1.0"); ap.add_argument("--tol", type=float, default=0.30)
a = ap.parse_args()
mu_tab = {float(k): v for k, v in json.loads(pathlib.Path(a.table).read_text())["friction_to_mu"].items()}
res = {}
for d in a.dirs:
    for f in sorted(pathlib.Path(d).glob("f*_*.csv")):
        m = re.match(r"f([0-9.]+)_(.+)\.csv", f.name); fr, name = float(m.group(1)), m.group(2)
        if name not in a.trials.split(","): continue
        X = np.genfromtxt(f, delimiter=",", names=True)
        ins = X["inside"] > 0.5
        if ins.sum() < 40: continue
        i0 = int(np.argmax(ins)); i1 = len(ins) - int(np.argmax(ins[::-1])) - 1
        c = SlipCoreC(0.02, 3, 2.0, variant="mu")
        mu_true = mu_tab[fr]; trace = []
        for i in range(len(X)):
            if i == i0: c.force_emerg()
            sp = math.hypot(X["vx"][i], X["vy"][i])
            o = c.board(X["imu_ay"][i], X["imu_gz"][i], sp, 0.0, X["imu_ax"][i], X["brake"][i], 0, 0, 999, 999, 999, 0, 0)
            if i >= i0: trace.append((X["t"][i] - X["t"][i0], o["mu_hat"], o["mu_n"]))
            if i >= i1 - 20: break                                  # 이탈 0.4 s 전까지만 (축 하나가 마른 노면을 밟는다)
        tr = np.array(trace)
        ok = np.abs(tr[:, 1] - mu_true) <= a.tol * mu_true
        # 수렴 시각: 그 뒤로 계속 범위 안에 머무는 첫 시각
        conv = None
        for j in range(len(ok)):
            if ok[j:].all(): conv = float(tr[j, 0]); break
        res.setdefault(f"{fr:g}", {})[name] = {"mu_eff": mu_true, "mu_hat_end": float(tr[-1, 1]), "err_end": float(tr[-1, 1] / mu_true - 1),
                                               "t_conv": conv, "samples": int(tr[-1, 2]), "trace": tr[::5].round(4).tolist()}
print(f"{'friction':>8} {'μ_eff':>6} | " + " | ".join(f"{t:^22s}" for t in a.trials.split(",")))
for fr in sorted(res, key=float):
    cells = []
    for t in a.trials.split(","):
        r = res[fr].get(t)
        cells.append(f"μ̂ {r['mu_hat_end']:.3f} ({r['err_end']*100:+4.0f}%) {('%.2fs' % r['t_conv']) if r['t_conv'] is not None else '  미수렴'}" if r else " " * 22)
    print(f"{fr:>8} {res[fr][next(iter(res[fr]))]['mu_eff']:6.3f} | " + " | ".join(cells))
pathlib.Path(a.out).write_text(json.dumps({"tol": a.tol, "results": res}, ensure_ascii=False))
print("저장:", a.out)
