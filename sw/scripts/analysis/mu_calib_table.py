#!/usr/bin/env python3
"""2단계 산출: CARLA friction 배수 → 실효 μ_eff 표 (carla_mu_calib.py 의 CSV 로부터).

순간 피크(진입·이탈 때 한쪽 축만 마른 노면을 밟는 순간, 펄스 제동의 충격)에 흔들리지 않게
정상 구간만 쓴다: 마찰 구간 진입 0.5 s 뒤 ~ 이탈 0.4 s 전. 그 구간의 0.1 s 평균 √(ax²+ay²) 의 95 분위를
시험마다 구하고, 바퀴가 확실히 한계에 있는 시험(제동 0.7·1.0, 조향 0.35, 결합)의 중앙값을 μ_eff 로 둔다.
제동 수준별 평균 감속도 함께 내 '제동을 올려도 감속이 줄지 않는가(바퀴 잠김 손실)' 를 본다.
사용: python3 mu_calib_table.py <mu_calib 폴더들...> --out logs/gain_sched/mu_table.json
"""
import argparse, json, pathlib, re
import numpy as np

G = 9.80665
ap = argparse.ArgumentParser(); ap.add_argument("dirs", nargs="+"); ap.add_argument("--out", required=True)
a = ap.parse_args()
rows = {}
for d in a.dirs:
    for f in sorted(pathlib.Path(d).glob("f*_*.csv")):
        m = re.match(r"f([0-9.]+)_(.+)\.csv", f.name); fr, name = float(m.group(1)), m.group(2)
        X = np.genfromtxt(f, delimiter=",", names=True)
        ins = X["inside"] > 0.5
        if ins.sum() < 40: continue
        i0 = np.argmax(ins); i1 = len(ins) - np.argmax(ins[::-1]) - 1
        t = X["t"]; sel = (t >= t[i0] + 0.5) & (t <= t[i1] - 0.4) & ins
        if sel.sum() < 10: continue
        at = np.hypot(X["ax"][sel], X["ay"][sel]); k = 5
        sm = np.convolve(at, np.ones(k) / k, mode="valid")
        rows.setdefault(fr, {})[name] = {"a95": float(np.percentile(sm, 95)), "decel": float(-X["ax"][sel].mean()),
                                         "ay95": float(np.percentile(np.abs(X["ay"][sel]), 95)), "n": int(sel.sum())}
SAT = ["brake_0.7", "brake_1.0", "steer_0.35", "combo"]
table = {}
print(f"{'friction':>8} {'μ_eff':>6} {'±':>5}  감속(제동 0.3/0.5/0.7/1.0)       최대 횡가속(조향 0.1/0.2/0.35)")
for fr in sorted(rows):
    r = rows[fr]; v = [r[k]["a95"] / G for k in SAT if k in r]
    mu, sd = float(np.median(v)), float(np.std(v))
    dec = [r.get(f"brake_{b}", {}).get("decel") for b in ("0.3", "0.5", "0.7", "1.0")]
    lat = [r.get(f"steer_{s}", {}).get("ay95") for s in ("0.1", "0.2", "0.35")]
    lock_loss = None
    if all(x is not None for x in dec):
        lock_loss = float((max(dec) - dec[-1]) / max(dec))       # 제동 1.0 이 최대 감속보다 얼마나 낮은가
    table[f"{fr:g}"] = {"mu_eff": mu, "mu_sd": sd, "decel_by_brake": dec, "ay95_by_steer": lat, "lock_loss": lock_loss, "trials": r}
    print(f"{fr:8g} {mu:6.3f} {sd:5.3f}  " + " ".join(f"{x:5.2f}" if x is not None else "  -  " for x in dec)
          + "        " + " ".join(f"{x:5.2f}" if x is not None else "  -  " for x in lat)
          + (f"   잠김 손실 {lock_loss*100:.0f}%" if lock_loss is not None else ""))
ks = sorted(rows); mus = [table[f"{k:g}"]["mu_eff"] for k in ks]
lo = [k for k in ks if k <= 0.2]
slope = float(np.polyfit(lo, [table[f"{k:g}"]["mu_eff"] for k in lo], 1)[0])
out = {"method": "정상 구간 0.1 s 평균 합가속 95 분위, 포화 시험 중앙값", "friction_to_mu": {f"{k:g}": table[f"{k:g}"]["mu_eff"] for k in ks},
       "low_friction_slope": slope, "table": table}
pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(f"\n저마찰(≤0.2) 기울기 μ_eff ≈ {slope:.3f} × friction   저장: {a.out}")
