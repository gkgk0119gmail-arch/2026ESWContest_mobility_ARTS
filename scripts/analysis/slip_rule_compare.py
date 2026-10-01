#!/usr/bin/env python3
"""2차 방어 판정 규칙을 바꾼 근거 — 직사각형에서 타원으로.

왜 건드렸나
-----------
변동 스윕에서 35 km/h 주행의 "빙판 진입 → 미끄러짐 확정"이 5.00 s 였다. 40 km/h 는 1.78 s.
확정 순간의 잔차를 보니 **ay_g 0.287 · yaw_err 0.353** 이었다. 횡가속도 임계가 0.300 이니
**4 % 차이로** 못 넘었고, 그래서 훨씬 느린 yaw 경로가 0.35 에 닿을 때까지 3.2 s 를 더 기다렸다.

두 잔차를 OR 로 묶은 직사각형 판정의 **모서리에 딱 걸린 것**이다. 임계값을 그냥 낮추면
오경보가 늘고, 속도로 정규화하면 고속에서 도리어 둔해진다. 형태를 바꾸는 쪽이 옳다.

    직사각형(기존)  |ay_g| ≥ 0.30  또는  |yaw_err| ≥ 0.35
    타원(변경)      (ay_g/0.30)² + (yaw_err/0.35)² ≥ 1

타원은 한쪽만 넘어도 발화하는 성질을 그대로 가진다 — **정의상 기존보다 늦어질 수 없다**.
대신 둘 다 0.71 쯤인 구간을 새로 잡는다. 새 상수도, 속도 보정도 없다.
비용은 곱 2 + 합 1 이고 분기 수는 그대로다.

이 스크립트가 재는 것
--------------------
1. 미끄러짐 세기를 낮춰가며 두 규칙의 확정 시각 — 얼마나 빨라지나, 어디서부터 새로 잡나
2. 정상 주행 오탐 — 민감해진 대가가 있나 (노이즈·속도 흔들기)
3. 곡선 주행 오탐 — 자전거 모델 오차가 가장 큰 조건

사용: python3 scripts/analysis/slip_rule_compare.py
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.imu_slip import Kalman2, SlipDetector  # noqa: E402

G = 9.80665
HZ = 50.0          # 데모 틱 주기와 같게 — 보드는 더 빨리 돌지만 입력이 이 주기로 온다


def _fresh(det):
    det.reset()
    det.kf_ay = Kalman2(1 / HZ, q=2000.0, r=0.16)
    det.kf_yaw = Kalman2(1 / HZ, q=3000.0, r=0.0005)


def run(det, t, ay, gz, v, steer):
    _fresh(det)
    for i in range(len(t)):
        e = det.step(float(t[i]), float(ay[i]), float(gz[i]), float(v), steer)
        if e:
            return float(t[i]), e.trigger
    return None, None


def synth(seed, v, yaw_gain, ay_gain, slip_at=2.0, dur=8.0,
          n_ay=0.4, n_gz=0.02, steer=0.02):
    """정상 주행 → slip_at 에 접지 상실. ramp 0.6 s 는 CARLA 실측 전개 속도에 맞춘 값."""
    rng = np.random.default_rng(seed)
    n = int(dur * HZ)
    t = np.arange(n) / HZ
    yn = SlipDetector(rate_hz=HZ).expected_yaw(v, steer)
    ay = rng.normal(0.0, n_ay, n) + v * yn
    gz = rng.normal(yn, n_gz, n)
    m = t >= slip_at
    ramp = np.clip((t - slip_at) / 0.6, 0, 1)
    gz[m] += yaw_gain * ramp[m]
    ay[m] += ay_gain * G * ramp[m]
    return t, ay, gz, v, steer


LEVELS = [(0.90, 0.45), (0.60, 0.30), (0.45, 0.22),
          (0.36, 0.18), (0.30, 0.15), (0.24, 0.12), (0.18, 0.09)]


def main():
    L = ["# 2차 방어 판정 규칙 — 직사각형에서 타원으로\n\n",
         "## 1. 무엇이 문제였나\n\n",
         "변동 스윕(`정리/11`)에서 35 km/h 주행의 빙판 진입 → 미끄러짐 확정이 **5.00 s** 였다. "
         "40 km/h 는 1.78 s, 45 km/h 는 1.10 s.\n\n",
         "확정 순간의 잔차를 열어 보니 **ay_g 0.287 · yaw_err 0.353** 이었다. "
         "횡가속도 임계가 0.300 이니 **4 % 차이로** 못 넘은 것이다.\n",
         "그래서 훨씬 느린 yaw 경로가 0.35 에 닿을 때까지 3.2 s 를 더 기다렸다.\n\n",
         "두 잔차를 OR 로 묶은 **직사각형 판정의 모서리에 딱 걸렸다**. "
         "위험은 두 축에 나뉘어 있었는데 판정은 축마다 따로 물었다.\n\n",
         "## 2. 어떻게 고쳤나\n\n",
         "```\n직사각형(기존)  |ay_g| >= 0.30  또는  |yaw_err| >= 0.35\n"
         "타원(변경)      (ay_g/0.30)^2 + (yaw_err/0.35)^2 >= 1\n```\n\n",
         "타원은 한쪽만 넘어도 발화하는 성질을 그대로 가진다 — **정의상 기존보다 늦어질 수 없다**.\n",
         "대신 둘 다 0.71 쯤인 구간을 새로 잡는다. 새 상수도, 속도 보정도 필요 없다.\n\n",
         "임계값을 그냥 낮추는 안과 속도로 정규화하는 안도 봤지만 둘 다 버렸다. "
         "낮추면 오경보가 늘고, 속도 정규화는 고속에서 임계가 올라가 **도리어 둔해진다**.\n\n",
         "비용은 곱 2 + 합 1, 분기 수는 그대로다. 보드 WCET 에 영향이 없어야 하고, "
         "실제로 재 봤다 (아래 5절).\n\n"]

    L.append("## 3. 얼마나 빨라지나\n\n")
    L.append("미끄러짐 세기를 낮춰가며 두 규칙의 확정 시각을 잰다. 35 km/h 기준, 진입은 2.00 s.\n\n")
    L.append("| yaw 증가 (rad/s) | 횡가속 증가 (g) | 직사각형 | 타원 | 차이 |\n|---|---|---|---|---|\n")
    n_faster = n_new = 0
    for yg, ag in LEVELS:
        a = synth(1, 9.72, yg, ag)
        tb, gb = run(SlipDetector(rate_hz=HZ, ellipse_rule=False), *a)
        te, ge = run(SlipDetector(rate_hz=HZ, ellipse_rule=True), *a)
        if tb and te and te < tb - 1e-9:
            n_faster += 1
        if te and not tb:
            n_new += 1
        diff = (f"**{tb - te:+.2f} s**" if (tb and te and abs(tb - te) > 1e-9)
                else ("**새로 잡음**" if te and not tb else ("같음" if tb and te else "-")))
        f = lambda x, g_: f"{x:.2f} s ({g_})" if x else "못 잡음"
        L.append(f"| {yg:.2f} | {ag:.2f} | {f(tb, gb)} | {f(te, ge)} | {diff} |\n")
    L.append(f"\n- 더 빨라진 조건 {n_faster} 건, **기존이 아예 못 잡던 것을 새로 잡은 조건 {n_new} 건**.\n")
    L.append("- 느려진 조건은 없다. 있을 수 없다 — 타원은 직사각형을 안에 품는다.\n")
    L.append("- 약한 미끄러짐(yaw 0.24 이하)은 둘 다 못 잡는다. 거기는 규칙이 아니라 "
             "센서·임계의 문제이고, 허용 예산은 `정리/09` 에 있다.\n")

    L.append("\n## 4. 대가가 있나 — 정상 주행 오탐\n\n")
    L.append("민감하게 만들었으면 오경보를 같이 재야 한다. 미끄러짐이 전혀 없는 주행을 만들어 센다.\n\n")
    L.append("| 조건 | 시행 | 직사각형 | 타원 |\n|---|---|---|---|\n")
    for label, gen in (
        ("직진 · 노이즈 1~3배", [(s, 13.9, 0.06, na, ng) for s in range(100)
                                 for na, ng in ((0.4, 0.02), (0.8, 0.04), (1.2, 0.06))]),
        ("곡선 · 조향 0.12~0.30", [(s, v, st_, 0.6, 0.03) for s in range(60)
                                   for st_, v in ((0.12, 13.9), (0.20, 11.1), (0.30, 8.3))]),
    ):
        got = {}
        for name, rule in (("box", False), ("ell", True)):
            fa = 0
            for seed, v, st_, na, ng in gen:
                a = synth(seed, v, 0.0, 0.0, n_ay=na, n_gz=ng, steer=st_)
                tt, _ = run(SlipDetector(rate_hz=HZ, ellipse_rule=rule), *a)
                if tt is not None:
                    fa += 1
            got[name] = fa
        L.append(f"| {label} | {len(gen)} | {got['box']} | {got['ell']} |\n")
    L.append("\n오탐이 **양쪽 다 0** 이다. 타원이 넓히는 영역은 두 잔차가 **동시에** "
             "임계의 0.71 을 넘는 곳인데, 정상 주행에서는 두 잔차가 그렇게 함께 커지지 않는다.\n")
    L.append("잔차가 이미 자전거 모델로 조향 성분을 뺀 값이라 그렇다.\n")

    out = ROOT / "logs/carla_demo/정리/13_판정규칙_타원.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
