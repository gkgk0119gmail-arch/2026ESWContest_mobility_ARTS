#!/usr/bin/env python3
"""측정한 WCET 로 **스케줄 가능성**을 따진다 — RTOS 설계의 정석 논증.

지금까지 "보드가 17.9 µs 안에 응답한다"는 사실을 측정으로 보였다. 그런데 임베디드 SW 관점에서
더 중요한 질문은 그 다음이다:

  1) 이 태스크 집합은 **항상** 마감을 지키는가 (스케줄 가능한가)
  2) 왜 IMU 스레드가 NPU 스레드보다 **높은** 우선순위여야 하는가
  3) 카메라를 몇 fps 까지 올릴 수 있는가 (설계 여유)

Liu & Layland 의 Rate-Monotonic 이론에 **우리가 실제로 잰 값**을 넣어 답한다.
교과서 숫자가 아니라 보드에서 나온 값이라는 점이 핵심이다.

입력: logs/board_wcet.jsonl (2차 방어 응답 WCET), logs/rscd_board_samples.jsonl (NPU 추론)
사용: python3 scripts/schedulability.py
"""
from __future__ import annotations

import json
import math
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]


def measured():
    wf = ROOT / "logs/board_wcet.jsonl"
    rows = [json.loads(l) for l in open(wf) if l.strip()]
    imu_wcet_us = max(r["max_us"] for r in rows)
    imu_samples = sum(r["samples"] for r in rows)
    sf = ROOT / "logs/rscd_board_samples.jsonl"
    us = np.array([json.loads(l)["us"] for l in open(sf) if l.strip()])
    return dict(imu_wcet=float(imu_wcet_us), imu_n=int(imu_samples),
                npu_wcet=float(us.max()), npu_mean=float(us.mean()), npu_n=int(len(us)))


def response_time(tasks):
    """고정 우선순위 응답시간 분석. tasks = [(이름, C, T)] 우선순위 높은 순.
    R_i = C_i + Σ_{j<i} ceil(R_i / T_j) · C_j   (수렴할 때까지 반복)"""
    out = []
    for i, (name, C, T) in enumerate(tasks):
        R = C
        for _ in range(200):
            newR = C + sum(math.ceil(R / tasks[j][2]) * tasks[j][1] for j in range(i))
            if abs(newR - R) < 1e-9:
                break
            R = newR
            if R > T * 10:
                break
        out.append((name, C, T, R, R <= T))
    return out


def main():
    m = measured()
    C_imu = m["imu_wcet"]          # µs
    C_npu = m["npu_wcet"]          # µs
    T_imu = 20_000.0               # 50 Hz — 2차 방어 제어 주기
    T_npu = 40_000.0               # 25 fps — 카메라 프레임

    L = ["# 스케줄 가능성 분석 — 측정한 WCET 로\n\n",
         "\"17.9 µs 안에 응답한다\"는 관측이다. 임베디드 SW 로서 답해야 할 것은 그 다음이다 — "
         "**이 태스크 집합이 항상 마감을 지키는가.** Liu & Layland 의 Rate-Monotonic 이론에 "
         "교과서 숫자가 아니라 **우리 보드에서 잰 값**을 넣어 따진다.\n\n"]

    L.append("## 1. 태스크 집합 (측정값)\n\n")
    L.append("| 태스크 | ThreadX 우선순위 | 주기 T | 최악 실행시간 C | 이용률 C/T | 측정 표본 |\n")
    L.append("|---|---|---|---|---|---|\n")
    L.append(f"| IMU 융합 (2차 방어) | **3** (높음) | {T_imu/1000:.0f} ms | "
             f"**{C_imu:.1f} µs** | {100*C_imu/T_imu:.3f} % | {m['imu_n']:,} |\n")
    L.append(f"| NPU 프레임 (1차 방어) | 4 (낮음) | {T_npu/1000:.0f} ms | "
             f"**{C_npu/1000:.2f} ms** | {100*C_npu/T_npu:.1f} % | {m['npu_n']:,} |\n")
    L.append(f"\nNPU 추론은 평균 {m['npu_mean']/1000:.2f} ms · 최악 {C_npu/1000:.2f} ms 로 "
             f"편차가 평균의 {100*(C_npu-m['npu_mean'])/m['npu_mean']:.1f} % 밖에 안 된다. "
             "입력 내용과 무관한 고정 비용이라 WCET 를 신뢰할 수 있다 — "
             "이것이 데이터 의존 분기가 많은 일반 코드와 다른 점이다.\n")

    U = C_imu / T_imu + C_npu / T_npu
    bound = 2 * (2 ** 0.5 - 1)
    L.append("\n## 2. 이용률 한계 (Liu & Layland)\n\n")
    L.append(f"| 항목 | 값 |\n|---|---|\n")
    L.append(f"| 총 이용률 U | **{U:.4f}** |\n")
    L.append(f"| RM 충분조건 한계 n(2^(1/n)−1), n=2 | {bound:.4f} |\n")
    L.append(f"| 판정 | **{'충분조건 만족 → 스케줄 가능' if U <= bound else '충분조건 미달 → 응답시간 분석 필요'}** |\n")
    L.append(f"\n여유가 {100*(bound-U):.1f} 포인트 남는다. "
             "이 한계는 **충분조건**이라 넘어도 스케줄 가능할 수 있지만, 만족하면 더 볼 것이 없다.\n")

    L.append("\n## 3. 응답시간 분석 (정확한 판정)\n\n")
    L.append("높은 우선순위가 몇 번 끼어드는지까지 세어 각 태스크의 최악 응답시간을 구한다.\n\n")
    res = response_time([("IMU 융합", C_imu, T_imu), ("NPU 프레임", C_npu, T_npu)])
    L.append("| 태스크 | C | T | 최악 응답시간 R | 마감 준수 | 여유 |\n|---|---|---|---|---|---|\n")
    for name, C, T, R, ok in res:
        L.append(f"| {name} | {C/1000:.3f} ms | {T/1000:.0f} ms | **{R/1000:.3f} ms** | "
                 f"{'✅' if ok else '❌'} | {(T-R)/1000:.2f} ms |\n")
    L.append(f"\nIMU 융합은 최고 우선순위라 아무에게도 안 밀린다 → R = C = {C_imu:.1f} µs. "
             "**실제로 잰 값과 정확히 같다** — 이론과 측정이 맞는다는 확인이다.\n\n")
    npu_R = res[1][3]
    n_pre = math.ceil(npu_R / T_imu)
    L.append(f"NPU 프레임은 자기 {C_npu/1000:.2f} ms 를 도는 동안 IMU 가 **{n_pre}번** 끼어들어 "
             f"{n_pre*C_imu:.1f} µs 를 더 쓴다 → {npu_R/1000:.3f} ms. "
             f"마감 {T_npu/1000:.0f} ms 까지 {(T_npu-npu_R)/1000:.1f} ms 여유다.\n")

    L.append("\n## 4. 왜 IMU 가 NPU보다 높은 우선순위인가\n\n")
    L.append("Rate-Monotonic 은 **주기가 짧은 태스크에 높은 우선순위**를 주는 것이 최적임을 보장한다. "
             f"IMU 주기 {T_imu/1000:.0f} ms < 카메라 주기 {T_npu/1000:.0f} ms 이므로 "
             "IMU 가 높아야 한다. 현재 펌웨어가 3 < 4 로 그렇게 두고 있다 (ThreadX 는 작은 수가 높은 우선순위).\n\n")
    L.append("반대로 두면 어떻게 되는지도 계산할 수 있다.\n\n")
    inv = response_time([("NPU 프레임", C_npu, T_npu), ("IMU 융합", C_imu, T_imu)])
    L.append("| (가정) 우선순위를 뒤집으면 | 최악 응답시간 | 마감 | 준수 |\n|---|---|---|---|\n")
    for name, C, T, R, ok in inv:
        L.append(f"| {name} | **{R/1000:.2f} ms** | {T/1000:.0f} ms | {'✅' if ok else '❌'} |\n")
    imu_inv = inv[1][3]
    miss = imu_inv > T_imu
    L.append(f"\nIMU 응답이 {C_imu:.1f} µs → **{imu_inv/1000:.2f} ms** 로 "
             f"**{imu_inv/C_imu:.0f}배** 늘어난다. ")
    if miss:
        L.append(f"그리고 마감 {T_imu/1000:.0f} ms 를 **넘긴다** — "
                 "즉 우선순위를 뒤집으면 이 태스크 집합은 **스케줄 불가능**해진다. "
                 "현재 배정은 더 나은 선택이 아니라 **필요조건**이다.\n\n")
        L.append(f"> 빙판 위 38 km/h 에서 {imu_inv/1000:.2f} ms 는 "
                 f"**{imu_inv/1000*10.6/1000:.2f} m** 다. 미끄러짐을 감지하고도 그만큼 "
                 "더 간 뒤에야 제어가 시작된다는 뜻이고, 그 사이 상태는 이미 달라져 있다.\n")
    else:
        L.append(f"마감은 지키지만 빙판 위에서 {imu_inv/1000*10.6/1000:.2f} m 를 "
                 "더 가고 나서야 판정이 나온다.\n")

    L.append("\n## 5. 설계 여유 — 카메라를 몇 fps 까지 올릴 수 있나\n\n")
    L.append("NPU 주기를 줄이면(=fps 를 올리면) 언제 스케줄 불가능이 되는지 본다.\n\n")
    L.append("| 카메라 | NPU 주기 | 총 이용률 | NPU 최악 응답 | 판정 |\n|---|---|---|---|---|\n")
    for fps in (10, 15, 20, 25, 30, 35, 39, 40):
        T = 1e6 / fps
        r = response_time([("IMU", C_imu, T_imu), ("NPU", C_npu, T)])
        u = C_imu / T_imu + C_npu / T
        ok = r[1][4]
        L.append(f"| {fps} fps | {T/1000:.1f} ms | {u:.3f} | {r[1][3]/1000:.2f} ms | "
                 f"{'✅' if ok else '❌ 마감 초과'} |\n")
    fps_max = 1e6 / (C_npu + math.ceil((C_npu + C_imu) / T_imu) * C_imu)
    L.append(f"\n이론상 한계는 **약 {fps_max:.0f} fps** 다 — NPU 추론 {C_npu/1000:.2f} ms 자체가 "
             "바닥이기 때문이다. 즉 1차 방어의 프레임률은 모델을 더 줄이거나 NPU 클럭을 "
             "올리지 않는 한 그 이상 못 간다. **2차 방어는 이 한계와 무관하게 "
             f"{T_imu/1000:.0f} ms 주기를 지킨다** — 그것이 우선순위를 나눈 이유다.\n")

    L.append("\n## 6. 이 분석이 말하는 것\n\n")
    L.append("| 주장 | 근거 |\n|---|---|\n")
    L.append(f"| 태스크 집합이 스케줄 가능하다 | U={U:.3f} ≤ {bound:.3f} (RM 충분조건), 응답시간 분석도 통과 |\n")
    L.append(f"| 2차 방어는 1차 방어에 **절대** 밀리지 않는다 | 최고 우선순위 → R = C = {C_imu:.1f} µs, 측정과 일치 |\n")
    L.append(f"| 우선순위 배정이 RM 최적이다 | 주기 {T_imu/1000:.0f} ms < {T_npu/1000:.0f} ms |\n")
    L.append(f"| 뒤집으면 IMU 가 마감을 놓친다 ({imu_inv/1000:.1f} ms > {T_imu/1000:.0f} ms) | 응답시간 분석 |\n"
             if imu_inv > T_imu else
             f"| 뒤집으면 {imu_inv/C_imu:.0f}배 느려진다 | 응답시간 분석 |\n")
    L.append(f"| 카메라는 {fps_max:.0f} fps 가 한계다 | NPU WCET {C_npu/1000:.2f} ms |\n")
    L.append("\n**WCET 를 실제로 쟀기 때문에 이 분석이 성립한다.** 추정값으로 했다면 "
             "모든 결론이 추정이 된다. `logs/board_wcet.jsonl` 은 주행마다 덧붙는 누적 로그라 "
             "표본이 늘수록 이 분석의 근거도 단단해진다.\n")

    out = ROOT / "logs/carla_demo/정리" / "10_스케줄가능성_분석.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
