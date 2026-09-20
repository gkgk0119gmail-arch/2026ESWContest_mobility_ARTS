#!/usr/bin/env python3
"""우선순위 선점을 **그림으로** 보여준다 — 스케줄 타임라인.

숫자표(`정리/10_스케줄가능성_분석.md`)는 정확하지만 한눈에 안 들어온다.
같은 내용을 간트 차트로 그리면 "IMU 가 NPU 를 잘라 먹고 들어간다"가 바로 보인다.

두 장을 만든다.
  schedule_rtos.jpg     현재 설계 (IMU 우선순위 3 > NPU 4)
  schedule_inverted.jpg 뒤집었을 때 — IMU 가 마감을 놓치는 장면

값은 전부 실측이다: IMU WCET 17.9 µs (14,162 샘플), NPU WCET 25.55 ms (25,140 회).

사용: python3 scripts/schedule_figure.py
"""
from __future__ import annotations

import json
import math
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIG = ROOT / "logs/carla_demo/figures"
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"


def measured():
    rows = [json.loads(l) for l in open(ROOT / "logs/board_wcet.jsonl") if l.strip()]
    imu = max(r["max_us"] for r in rows) / 1000.0            # ms
    us = np.array([json.loads(l)["us"] for l in open(ROOT / "logs/rscd_board_samples.jsonl") if l.strip()])
    return imu, float(us.max()) / 1000.0


def draw(inverted: bool, C_imu: float, C_npu: float, T_imu=20.0, T_npu=40.0, span=80.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Rectangle
    try:
        font_manager.fontManager.addfont(FONT)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False

    # IMU 를 보이게 하려고 폭만 확대한다 (17.9 µs 는 이 축에서 한 픽셀도 안 된다)
    VIS = 0.45          # ms 로 그릴 때 쓰는 최소 가시 폭
    fig, ax = plt.subplots(figsize=(11.5, 4.3), dpi=150)

    imu_rel = [k * T_imu for k in range(int(span / T_imu) + 1)]
    npu_rel = [k * T_npu for k in range(int(span / T_npu) + 1)]

    npu_bars, imu_bars, imu_done = [], [], []
    if not inverted:
        # IMU 가 높은 우선순위: 도착 즉시 실행, NPU 는 그만큼 밀린다
        t = 0.0
        for rel in npu_rel:
            t = max(t, rel)
            left = C_npu
            while left > 1e-9:
                nxt = next((r for r in imu_rel if r > t + 1e-9), None)
                run = left if nxt is None else min(left, nxt - t)
                if run > 1e-9:
                    npu_bars.append((t, run))
                    t += run
                    left -= run
                if nxt is not None and abs(t - nxt) < 1e-9:
                    imu_bars.append((t, C_imu))
                    imu_done.append((nxt, t))
                    t += C_imu
        for rel in imu_rel:
            if not any(abs(b[0] - rel) < 1e-6 for b in imu_bars):
                imu_bars.append((rel, C_imu))
                imu_done.append((rel, rel))
    else:
        # NPU 가 높은 우선순위: IMU 는 NPU 가 끝날 때까지 기다린다
        busy = []
        t = 0.0
        for rel in npu_rel:
            t = max(t, rel)
            npu_bars.append((t, C_npu))
            busy.append((t, t + C_npu))
            t += C_npu
        for rel in imu_rel:
            t = rel
            for s, e in busy:
                if s <= t < e:
                    t = e
            imu_bars.append((t, C_imu))
            imu_done.append((rel, t))

    for s, w in npu_bars:
        ax.add_patch(Rectangle((s, 0.08), w, 0.34, color="#3E7CB1", ec="#1d4a72", lw=0.8))
    for s, w in imu_bars:
        ax.add_patch(Rectangle((s, 0.56), max(w, VIS), 0.34, color="#D64550", ec="#8f2630", lw=0.8))

    # 도착 화살표와 마감선
    for rel in imu_rel:
        ax.annotate("", xy=(rel, 0.56), xytext=(rel, 0.99),
                    arrowprops=dict(arrowstyle="->", color="#8f2630", lw=1.0, alpha=0.75))
        ax.axvline(rel + T_imu, color="#8f2630", ls=":", lw=0.7, alpha=0.35)
    for rel in npu_rel:
        ax.annotate("", xy=(rel, 0.08), xytext=(rel, 0.50),
                    arrowprops=dict(arrowstyle="->", color="#1d4a72", lw=1.0, alpha=0.75))

    # 응답시간 표시 (가장 나쁜 것 하나)
    if imu_done:
        worst = max(imu_done, key=lambda d: d[1] - d[0])
        R = worst[1] - worst[0] + C_imu
        miss = R > T_imu
        ax.annotate(f"IMU 응답 {R*1000:.0f} us" if R < 1 else f"IMU 응답 {R:.1f} ms",
                    xy=(worst[1], 0.93), xytext=(worst[1] + 3, 1.10),
                    color="#D64550" if not miss else "#b00020", fontsize=12,
                    arrowprops=dict(arrowstyle="->", color="#D64550", lw=1.2))
        if miss:
            ax.text(worst[0] + T_imu, 1.24, f"마감 {T_imu:.0f} ms 초과 → 스케줄 불가능",
                    color="#b00020", fontsize=13, ha="left")

    ax.set_xlim(-1, span)
    ax.set_ylim(0, 1.45)
    ax.set_yticks([0.25, 0.73], ["NPU 프레임\n(1차 방어)", "IMU 융합\n(2차 방어)"])
    ax.set_xlabel("시간 (ms)")
    title = ("현재 설계 — IMU 가 높은 우선순위(3) : NPU(4) 를 선점한다"
             if not inverted else
             "(가정) 우선순위를 뒤집으면 — IMU 가 NPU 를 기다린다")
    ax.set_title(title, fontsize=14)
    ax.grid(axis="x", alpha=0.22)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.text(0.5, 1.38, f"실측값  IMU WCET {C_imu*1000:.1f} us · NPU WCET {C_npu:.2f} ms   "
                       f"(IMU 막대는 보이도록 폭만 확대했다)",
            fontsize=9.5, color="#666")
    fig.tight_layout()
    name = "schedule_inverted.jpg" if inverted else "schedule_rtos.jpg"
    fig.savefig(FIG / name, pil_kwargs={"quality": 92})
    plt.close(fig)
    return name


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    C_imu, C_npu = measured()
    print(f"실측 IMU WCET {C_imu*1000:.1f} µs, NPU WCET {C_npu:.2f} ms")
    for inv in (False, True):
        print("  " + draw(inv, C_imu, C_npu))
    print(f"[저장] {FIG}")


if __name__ == "__main__":
    main()
