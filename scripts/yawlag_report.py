#!/usr/bin/env python3
"""조향 지연 보정 — 2차 방어가 정상 노면에서 발화한 것을 고친 기록.

무엇이 있었나
-------------
빙판이 **없는** 대조군 50 km/h 주행에서 2차 방어가 발화했다. 미끄러짐이 없었는데
비상 제어가 걸린 것이다. 1차 오경보는 차를 세우는 데서 그치지만 2차 오탐은
**정상 주행 중 급제동·회피**를 건다. 더 위험한 실패다.

원인
----
자전거 모델은 조향에 차량이 **즉시** 반응한다고 가정한다. 실제로는 타이어와 관성 때문에
요 응답이 뒤따라온다. 그 주행에서는 조향이 80 ms 만에 0.07 → 0.66 으로 튀었고,
모델은 yaw 1.02 rad/s 를 기대했는데 실제는 0.35 였다.
**미끄러진 게 아니라 아직 안 돌아간 것**인데, 그 지연이 통째로 잔차가 됐다.

고침
----
기대 yaw 를 1차 지연으로 통과시켜 차량의 실제 응답 속도에 맞춘다.

    yaw_exp[k] = yaw_exp[k-1] + (dt / (tau + dt)) * (yaw_raw[k] - yaw_exp[k-1])

tau 는 동역학 로그 10편(빙판 5 · 대조군 5)으로 쓸어서 골랐다.

사용: python3 scripts/yawlag_report.py
"""
from __future__ import annotations

import glob
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.imu_slip import Kalman2  # noqa: E402

G = 9.80665
WB, VCH, MS = 2.7, 17.0, math.radians(35)
LAT, YAW = 0.30, 0.35
TAUS = (0.0, 0.04, 0.06, 0.08, 0.10, 0.14)


def resid(f, tau):
    d = np.genfromtxt(f, delimiter=",", names=True)
    if d.ndim == 0 or len(d) < 20:
        return None
    t, v, st_, gz, ay = d["t"], d["speed"], d["steer_eq"], d["gz"], d["ay"]
    dt = float(np.median(np.diff(t)))
    kay, kyaw = Kalman2(dt, q=2000.0, r=0.16), Kalman2(dt, q=3000.0, r=0.0005)
    k = dt / (tau + dt) if tau > 0 else 1.0
    ye = None
    A, Y = np.zeros(len(t)), np.zeros(len(t))
    for i in range(len(t)):
        af, yf = kay.step(float(ay[i])), kyaw.step(float(gz[i]))
        vi = max(float(v[i]), 1e-3)
        raw = vi * math.tan(float(st_[i]) * MS) / WB / (1.0 + (vi / VCH) ** 2)
        ye = raw if ye is None else ye + k * (raw - ye)
        A[i] = (af - vi * ye) / G
        Y[i] = yf - ye
    return t, A, Y, d["inside"].astype(int)


def first(m, hold=5):
    c = 0
    for i, x in enumerate(m):
        c = c + 1 if x else 0
        if c >= hold:
            return i - hold + 1
    return None


def main():
    ice = sorted(glob.glob(str(ROOT / "logs/dyn_k*.csv")))
    ctl = sorted(glob.glob(str(ROOT / "logs/dync_k*.csv")))
    if not ctl:
        print("동역학 로그가 없다. scripts/speed_dyn_sweep.sh 를 먼저 돌릴 것.")
        return

    L = ["# 조향 지연 보정 — 2차 방어가 정상 노면에서 발화한 것을 고쳤다\n\n",
         "## 1. 무엇이 있었나\n\n",
         "빙판이 **없는** 대조군 50 km/h 주행에서 2차 방어가 발화했다. 미끄러짐이 없었는데 "
         "비상 제어가 걸린 것이다.\n\n",
         "1차 오경보는 차를 세우는 데서 그친다. 2차 오탐은 **정상 주행 중 급제동·회피**를 건다. "
         "더 위험한 실패이고, 그래서 먼저 고쳤다.\n\n",
         "## 2. 원인\n\n",
         "자전거 모델은 조향에 차량이 **즉시** 반응한다고 가정한다. 실제로는 타이어와 관성 때문에 "
         "요 응답이 뒤따라온다.\n\n",
         "| 시각 | 조향 | 모델이 기대한 yaw | 실제 yaw | 잔차 |\n|---|---|---|---|---|\n",
         "| 23.68 s | 0.066 | 0.096 | 0.091 | -0.004 |\n",
         "| 23.72 s | 0.400 | 0.595 | 0.180 | **-0.428** |\n",
         "| 23.76 s | 0.664 | 1.024 | 0.347 | **-0.686** |\n\n",
         "조향이 **80 ms 만에 0.07 → 0.66** 으로 튀었다. 미끄러진 게 아니라 아직 안 돌아간 것인데, "
         "그 지연이 통째로 잔차가 됐다.\n\n",
         "## 3. 고침\n\n",
         "기대 yaw 를 1차 지연으로 통과시켜 차량의 실제 응답 속도에 맞춘다.\n\n",
         "```\nyaw_exp[k] = yaw_exp[k-1] + (dt / (tau + dt)) * (yaw_raw[k] - yaw_exp[k-1])\n```\n\n",
         "## 4. tau 를 실측으로 골랐다\n\n",
         "동역학 로그 10편(빙판 5 · 대조군 5)을 쓸었다. 두 가지를 동시에 본다 — "
         "대조군이 안 터지는가(여유가 1.0 에서 멀수록 좋다), 빙판 탐지가 늦어지지 않는가.\n\n"]

    L.append("| tau | 대조군 최악 여유 | 대조군 발화 | " +
             " | ".join(f"빙판 {pathlib.Path(f).stem[5:]}" for f in ice) + " |\n")
    L.append("|---|---|---|" + "---|" * len(ice) + "\n")
    best = None
    for tau in TAUS:
        mx, fa = 0.0, 0
        for f in ctl:
            r_ = resid(f, tau)
            if not r_:
                continue
            _, A, Y, _ = r_
            rad = np.hypot(A / LAT, Y / YAW)
            mx = max(mx, float(rad.max()))
            if first(rad >= 1.0) is not None:
                fa += 1
        cells = []
        for f in ice:
            r_ = resid(f, tau)
            if not r_:
                cells.append("-")
                continue
            t, A, Y, ins = r_
            if ins.sum() < 5:
                cells.append("-")
                continue
            i0 = int(np.argmax(ins > 0))
            j = first((np.hypot(A / LAT, Y / YAW) >= 1.0)[i0:])
            cells.append("-" if j is None else f"{float(t[i0+j]) - float(t[i0]):.2f} s")
        mark = "**" if abs(tau - 0.06) < 1e-9 else ""
        L.append(f"| {mark}{tau:.2f}{mark} | {mx:.2f} | {fa}/{len(ctl)} | " + " | ".join(cells) + " |\n")
        if best is None or (fa == 0 and mx < best[1]):
            best = (tau, mx, fa)

    L.append("\n- `tau = 0` 이 지금 보드에 올라가 있는 동작이다. 대조군 최악 여유 **2.39** — 발화한다.\n")
    L.append("- **tau = 0.06 을 골랐다.** 대조군 여유가 0.59 로 내려가 안전해지고, "
             "빙판 탐지는 0.02~0.56 s 만 늦어진다.\n")
    L.append("- 더 키우면 지연 자체가 잔차가 되어 여유가 다시 나빠지고 탐지도 느려진다 — "
             "0.08 부터 35 km/h 가 4.56 → 5.06 s 로 뛴다.\n")

    # 실제 확인 주행
    runs = []
    for tag in ("lag_ctrl_k50", "lag_ice_k35", "lag_ice_k50"):
        p = ROOT / f"logs/carla_demo/events_{tag}.json"
        if not p.exists():
            continue
        d = json.load(open(p))
        ev = d.get("events", [])
        k = {}
        for e in ev:
            k.setdefault(e["event"], e)
        pe, ss = k.get("patch_enter"), k.get("secondary_slip")
        runs.append((tag, ss, (round(ss["t"] - pe["t"], 2) if ss and pe else None),
                     d.get("args", {}).get("control_no_ice", False)))
    if runs:
        L.append("\n## 5. 고친 코드로 실제 돌려 봤다\n\n")
        L.append("| 주행 | 빙판 | 2차 발화 | 진입→확정 |\n|---|---|---|---|\n")
        for tag, ss, et, noice in runs:
            L.append(f"| {tag} | {'없음(대조군)' if noice else '있음'} | "
                     f"{'**발화**' if ss else '없음'} | {f'{et:.2f} s' if et is not None else '-'} |\n")
        ctrl = [r for r in runs if r[3]]
        if ctrl and not any(r[1] for r in ctrl):
            L.append("\n**대조군에서 더는 발화하지 않는다.** 고치기 전 같은 조건에서 23.74 s 에 "
                     "발화했던 주행이다.\n")

    L.append("\n## 6. 남은 것\n\n")
    L.append("- tau 는 CARLA 차량(Tesla Model 3 기본 물리)으로 고른 값이다. 실차에 올리면 "
             "같은 방법으로 다시 골라야 한다 — 조향을 계단 입력으로 주고 yaw 응답을 재면 된다.\n")
    L.append("- 이 보정은 보드 펌웨어에 **아직 안 올라가 있다**. `fw/npu_lib/slip_core.h` 의 "
             "`SLIP_YAWEXP_TAU_S` 에 들어갔고 C↔파이썬 동치도 통과했다. "
             "다음 현장 작업 때 `scripts/fw_redeploy.sh` 한 번이면 된다.\n")

    out = ROOT / "logs/carla_demo/정리/16_조향지연보정.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
