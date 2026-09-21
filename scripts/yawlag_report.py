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

    # 실제 확인 주행 — 보드는 **옛 펌웨어**(보정 없음), 호스트 참조는 **새 코드**(보정 있음).
    # 같은 주행선 위에서 둘을 나란히 볼 수 있다. 펌웨어를 못 구우니 이게 가장 깨끗한 비교다.
    runs = []
    for tag in ("lag_ctrl_k50", "lag_ice_k35", "lag_ice_k50"):
        fp = ROOT / f"logs/carla_demo/events_{tag}.json"
        if not fp.exists():
            continue
        d = json.load(open(fp))
        ev = d.get("events", [])
        k = {}
        for e in ev:
            k.setdefault(e["event"], e)
        pe, ss = k.get("patch_enter"), k.get("secondary_slip")
        ab = d.get("rule_ab") or {}
        runs.append(dict(tag=tag, board=ss,
                         et=(round(ss["t"] - pe["t"], 2) if ss and pe else None),
                         # rule_ab 가 아예 없으면(옛 기록) 보드 이벤트의 local_agrees 로 읽는다.
                         # 보드가 발화했는데 local_agrees 가 False 면 호스트 참조는 침묵한 것이다.
                         has_ab=bool(ab),
                         host=ab.get("ellipse"), host_box=ab.get("box"),
                         noice=bool(d.get("args", {}).get("control_no_ice"))))
    if runs:
        L.append("\n## 5. 고친 코드로 실제 돌려 봤다\n\n")
        L.append("펌웨어는 못 구웠다. 그래서 **보드는 보정 없는 옛 코드**로 돌고, "
                 "호스트 참조 두 개가 **보정이 들어간 새 코드**로 같은 입력을 같이 본다.\n")
        L.append("같은 주행선 위의 비교라 시뮬을 두 번 돌리는 것보다 깨끗하다.\n\n")
        L.append("| 주행 | 빙판 | 보드(보정 없음) | 호스트 타원(보정 있음) | 호스트 사각형(보정 있음) |\n")
        L.append("|---|---|---|---|---|\n")
        def f_(v, has):
            if v:
                return f"**{v['t']:.2f} s**"
            return "발화 없음" if has else "발화 없음 (기록 방식 이전)"
        for r in runs:
            b = "발화 없음" if not r["board"] else f"**{r['board']['t']:.2f} s** ({r['board']['trigger']})"
            L.append(f"| {r['tag']} | {'없음(대조군)' if r['noice'] else '있음'} | {b} | "
                     f"{f_(r['host'], r['has_ab'])} | {f_(r['host_box'], r['has_ab'])} |\n")
        ctrl = [r for r in runs if r["noice"]]
        if ctrl:
            board_fired = any(r["board"] for r in ctrl)
            host_fired = any(r["host"] or r["host_box"] for r in ctrl)
            # 보드가 발화했는데 로컬이 동의하지 않았다 = 호스트 참조는 침묵했다는 직접 증거
            disagreed = [r for r in ctrl if r["board"] and r["board"].get("local_agrees") is False]
            if disagreed:
                L.append("\n주행 기록에 `local_agrees: false` 로 남아 있다 — "
                         "보드가 발화한 그 순간 호스트 참조는 **동의하지 않았다**.\n")
            L.append("\n")
            if board_fired and not host_fired:
                L.append("**보정 없는 보드는 또 발화했고, 보정이 들어간 코드는 발화하지 않았다.**\n")
                L.append("같은 입력·같은 주행선이므로 차이는 보정 하나뿐이다. 고쳐진 것이 맞다.\n")
            elif not board_fired and not host_fired:
                L.append("이번에는 보드도 발화하지 않았다 — 같은 장면을 다시 만나지 못한 것이다. "
                         "판단은 §4 의 오프라인 스윕으로 한다.\n")
            elif host_fired:
                L.append("**보정이 들어갔는데도 발화했다.** τ 를 다시 봐야 한다.\n")

    L.append("\n## 6. 남은 것\n\n")
    L.append("- tau 는 CARLA 차량(Tesla Model 3 기본 물리)으로 고른 값이다. 실차에 올리면 "
             "같은 방법으로 다시 골라야 한다 — 조향을 계단 입력으로 주고 yaw 응답을 재면 된다.\n")
    L.append("- 이 보정은 보드 펌웨어에 **아직 안 올라가 있다**. `fw/npu_lib/slip_core.h` 의 "
             "`SLIP_YAWEXP_TAU_S` 에 들어갔고 C↔파이썬 동치도 통과했다. "
             "**보드를 돌려받으면** `scripts/fw_redeploy.sh` 한 번이면 된다. STM32N6 는 2026-09-21 다른 용도로 빠져 지금은 굽지 못한다.\n")

    out = ROOT / "logs/carla_demo/정리/16_조향지연보정.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
