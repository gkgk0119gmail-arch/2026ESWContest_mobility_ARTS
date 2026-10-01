#!/usr/bin/env python3
"""주행 로그(t,speed,steer_eq,gz,...)로 감지기 yaw 모델의 특성속도 v_ch 를 맞춘다.
모델: yaw_exp = v·tan(steer_eq·35°)/L / (1 + (v/v_ch)²).  빙판 밖(inside=0) 샘플만, |steer_eq|>0.02, v>4 m/s.
출력: 최적 v_ch, 그때의 잔차 통계(ay_g 잔차 최대 등) — 임계 0.3 g 대비 여유를 본다."""
import sys, math, numpy as np, csv
rows = []
for p in sys.argv[1:]:
    with open(p) as f:
        for r in csv.DictReader(f): rows.append({k: float(v) for k, v in r.items()})
A = np.array([[r["speed"], r["steer_eq"], r["gz"], r["ay"], r["inside"]] for r in rows])
m = (A[:, 4] == 0) & (np.abs(A[:, 1]) > 0.02) & (A[:, 0] > 4)
v, st, gz, ay = A[m, 0], A[m, 1], A[m, 2], A[m, 3]
print(f"샘플 {m.sum()}/{len(A)} (곡선 구간)")
best = None
for vch in np.arange(6, 80, 0.5):
    ye = v * np.tan(st * math.radians(35)) / 2.7 / (1 + (v / vch) ** 2)
    err = np.mean((gz - ye) ** 2)
    if best is None or err < best[1]: best = (vch, err)
vch = best[0]
ye = v * np.tan(st * math.radians(35)) / 2.7 / (1 + (v / vch) ** 2)
ye0 = v * np.tan(st * math.radians(35)) / 2.7
for name, y in (("운동학(보정 없음)", ye0), (f"v_ch={vch:.1f}", ye)):
    ayg = (ay - v * y) / 9.80665; yerr = gz - y
    print(f"  {name:18s} |ay_g| 최대 {np.abs(ayg).max():.3f} p99 {np.percentile(np.abs(ayg),99):.3f}   |yaw_err| 최대 {np.abs(yerr).max():.3f} p99 {np.percentile(np.abs(yerr),99):.3f}")
print(f"VCH={vch:.1f}")
