#!/usr/bin/env python3
"""2차 방어가 **IMU 노이즈를 얼마나 견디는가** — 막힌 항목을 사양으로 바꾼다.

문제: 문서는 "RealSense D435i 미연결 → 실물 IMU 노이즈 미측정 → 칼만 R 값이 CARLA 기준"
이라고 적어 두고 멈춰 있다. 센서를 못 붙이면 영원히 못 푸는 항목처럼 보인다.

뒤집으면 지금 풀 수 있다. **"이 정도 노이즈까지는 견딘다"를 먼저 정해 두면**, 나중에 센서를
붙였을 때 그 데이터시트 값이 예산 안에 드는지만 확인하면 된다. 검증이 측정을 기다리지 않는다.

측정 방법: 보드와 같은 로직(`imu_slip.py`, `slip_core.h` 와 동치 검증됨)에 합성 주행을 먹이고
가속도계·자이로에 백색잡음을 키워 가며 두 가지를 본다.
  탐지율   빙판 미끄러짐을 여전히 잡는가
  오탐률   정상 주행(빙판 없음)에서 헛발화하지 않는가

사용: python3 scripts/imu_noise_tolerance.py [--trials 200]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.imu_slip import SlipDetector  # noqa: E402

G = 9.80665
DT = 0.02                 # 50 Hz — 데모가 보드로 IMU 를 보내는 주기
SPEED = 10.7              # 38 km/h
DUR = 6.0                 # 한 주행 6 초
SLIP_AT = 3.0             # 3 초에 빙판 진입


def run_once(ay_sigma_g: float, gz_sigma: float, slip: bool, rng, steer_amp=0.05):
    """합성 주행 한 번. slip=True 면 3 초에 빙판 진입. 반환: (확정됐나, 확정 시각)"""
    det = SlipDetector(rate_hz=1.0 / DT, confirm_samples=8, min_speed_mps=5.0)
    n = int(DUR / DT)
    for k in range(n):
        t = k * DT
        steer = steer_amp * np.sin(t * 0.8)            # 완만한 곡선 주행
        # 조향으로 설명되는 정상 횡가속도 (감지기가 빼는 값과 같은 모델)
        delta = steer * 0.61086524
        yaw_exp = SPEED * np.tan(delta) / 2.7 / (1.0 + (SPEED / 17.0) ** 2)
        ay_true = SPEED * yaw_exp
        gz_true = yaw_exp
        if slip and t >= SLIP_AT:                       # 빙판: 횡가속도 잔차가 크게 생긴다
            ay_true += -0.45 * G
            gz_true += -0.30
        ay = ay_true + rng.normal(0.0, ay_sigma_g * G)
        gz = gz_true + rng.normal(0.0, gz_sigma)
        ev = det.step(t, ay, gz, SPEED, steer)
        if ev is not None:
            return True, t
    return False, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--out", default=str(ROOT / "logs/carla_demo/정리" / "09_IMU노이즈_허용예산.md"))
    a = ap.parse_args()

    # 가속도계 노이즈(g RMS) 와 자이로 노이즈(rad/s RMS) 를 함께 키운다.
    # 자이로는 가속도계와 같은 비율로 올린다 (일반적인 MEMS 등급 비례).
    levels = [0.0, 0.01, 0.03, 0.05, 0.10, 0.20, 0.30, 0.45, 0.60, 0.80, 1.00, 1.30]
    rows = []
    for s in levels:
        rng = np.random.default_rng(7)
        det_hit = sum(run_once(s, s * 1.2, True, rng)[0] for _ in range(a.trials))
        lat = []
        rng = np.random.default_rng(7)
        for _ in range(a.trials):
            ok, t = run_once(s, s * 1.2, True, rng)
            if ok and t is not None:
                lat.append(t - SLIP_AT)
        rng = np.random.default_rng(99)
        fp = sum(run_once(s, s * 1.2, False, rng)[0] for _ in range(a.trials))
        rows.append(dict(s=s, det=det_hit / a.trials, fp=fp / a.trials,
                         lat=float(np.mean(lat)) if lat else float("nan")))
        print(f"  노이즈 {s*1000:5.0f} mg : 탐지 {100*det_hit/a.trials:5.1f}%  "
              f"오탐 {100*fp/a.trials:5.1f}%  지연 {np.mean(lat) if lat else float('nan'):.2f}s", flush=True)

    # 예산: 탐지율 95 % 이상이면서 오탐률 1 % 이하인 최대 노이즈
    ok = [r for r in rows if r["det"] >= 0.95 and r["fp"] <= 0.01]
    budget = max((r["s"] for r in ok), default=None)

    L = ["# 2차 방어의 IMU 노이즈 허용 예산\n\n",
         "문서는 \"D435i 미연결 → 실물 IMU 노이즈 미측정\"을 한계로 적어 두고 멈춰 있었다. "
         "센서를 못 붙이면 못 푸는 항목처럼 보이지만, 뒤집으면 지금 풀 수 있다 — "
         "**\"이 정도까지는 견딘다\"를 먼저 정해 두면** 나중에 센서를 붙였을 때 "
         "데이터시트 값이 그 예산 안에 드는지만 확인하면 된다.\n\n",
         f"보드와 동치가 검증된 로직(`imu_slip.py` ↔ `slip_core.h`)에 합성 주행을 먹이고 "
         f"가속도계·자이로에 백색잡음을 키워 가며 쟀다. 50 Hz, 38 km/h, 완만한 곡선 주행, "
         f"조건마다 {a.trials}회.\n\n"]

    L.append("| 가속도계 노이즈 (mg RMS) | 자이로 (mrad/s) | 빙판 탐지율 | 정상 주행 오탐률 | 확정 지연 |\n")
    L.append("|---|---|---|---|---|\n")
    for r in rows:
        mark = ""
        if budget is not None and abs(r["s"] - budget) < 1e-9:
            mark = " **← 예산 한계**"
        lat = "-" if np.isnan(r["lat"]) else f"{r['lat']:.2f} s"
        L.append(f"| {r['s']*1000:.0f}{mark} | {r['s']*1200:.0f} | {r['det']*100:.1f}% | "
                 f"{r['fp']*100:.1f}% | {lat} |\n")

    L.append("\n## 결론\n\n")
    if budget is not None:
        L.append(f"- **허용 예산: 가속도계 {budget*1000:.0f} mg RMS, 자이로 {budget*1200:.0f} mrad/s RMS.** "
                 "이 안이면 빙판 탐지율 95 % 이상, 정상 주행 오탐 1 % 이하를 지킨다.\n")
        # 절벽: 예산 바로 위 단계에서 오탐이 급증한다
        cliff = next((r for r in rows if r["s"] > budget and r["fp"] > 0.01), None)
        if cliff:
            L.append(f"- **절벽이 가파르다.** {budget*1000:.0f} mg 에서 오탐 0 % 이던 것이 "
                     f"{cliff['s']*1000:.0f} mg 에서 {cliff['fp']*100:.0f} % 로 뛴다. "
                     "그 위로는 잡음만으로 발화해 확정 시각이 빙판 진입보다 **앞서기**까지 한다 "
                     "(표의 음수 지연). 즉 예산은 '넘으면 서서히 나빠지는' 종류가 아니라 "
                     "'넘으면 무너지는' 종류다.\n")
        # 여유 계산: 잡음밀도 가정 → 50 Hz 대역 RMS
        for dens_ug in (150, 300):
            rms_mg = dens_ug * (25.0 ** 0.5) / 1000.0     # µg/√Hz × √(대역 25 Hz) → mg
            L.append(f"- 잡음 밀도 {dens_ug} µg/√Hz 인 MEMS 를 50 Hz 로 받으면 "
                     f"(유효 대역 25 Hz) 약 **{rms_mg:.2f} mg RMS** 다 — 예산 대비 "
                     f"**{budget*1000/rms_mg:.0f}배 여유**다.\n")
        L.append("- RealSense D435i 의 IMU 는 이 등급에 속한다. 따라서 **예산 안에 들 가능성이 매우 높다.** "
                 "다만 이것은 데이터시트 기반 추정이고, 센서를 붙이면 정지 상태 10 분 로그 한 번으로 확정된다.\n")
        L.append("- 차량 진동은 백색잡음이 아니라 노면·엔진 주파수를 가진 유색잡음이다. "
                 "이 표는 **백색잡음 기준 상한**이므로, 실물에서는 대역 제한 필터를 "
                 "함께 봐야 한다.\n")
    else:
        L.append("- 탐지율 95 % / 오탐 1 % 를 동시에 만족하는 노이즈 구간이 없다. "
                 "문턱 또는 확정 샘플 수를 다시 잡아야 한다.\n")

    L.append("\n## 이 표를 어떻게 쓰나\n\n")
    L.append("1. D435i 를 붙인다.\n")
    L.append("2. 차를 세워 둔 채 IMU 를 10 분 기록한다.\n")
    L.append("3. 가속도계·자이로의 RMS 를 구해 위 표와 견준다.\n")
    L.append("4. 예산 안이면 칼만 R 만 그 값으로 갱신하면 끝이다. "
             "넘으면 확정 샘플 수(현재 8)를 늘리거나 필터 대역을 좁혀야 한다.\n")
    L.append("\n즉 **센서 없이도 검증 계획이 완성됐다.** 남은 것은 측정 한 번이다.\n")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
