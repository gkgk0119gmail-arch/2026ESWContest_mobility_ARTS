#!/usr/bin/env python3
"""판정 규칙을 **그림 한 장으로** — 잔차 평면 위의 직사각형과 타원.

숫자로 "0.287 대 0.300" 이라고 말하면 안 와닿는다. 두 잔차를 축으로 놓고
판정 경계를 그린 뒤, 실제 주행의 잔차 궤적을 얹으면 한눈에 보인다.

  가로축 ay_g     횡가속도 잔차 (g) — 조향으로 설명되는 원심 성분을 뺀 나머지
  세로축 yaw_err  yaw rate 잔차 (rad/s)
  회색 사각형     기존 판정 — 이 상자 **밖**으로 나가야 발화
  붉은 타원       바뀐 판정 — 이 타원 밖으로 나가면 발화
  궤적            빙판 진입부터의 실제 잔차. 점이 커질수록 시간이 흐른 것

35 km/h 궤적이 사각형 모서리에 붙어 한참을 머무는 장면이 이 그림의 전부다.

사용: python3 scripts/rule_boundary_figure.py
"""
from __future__ import annotations

import glob
import math
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.imu_slip import Kalman2  # noqa: E402

FIG = ROOT / "logs/carla_demo/figures"
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"
G = 9.80665
WB, VCH, MAXSTEER = 2.7, 17.0, math.radians(35)
LAT, YAW = 0.30, 0.35
KPH_RE = re.compile(r"dyn_k(\d+)\.csv$")


def residuals(d):
    t, v, st_, gz, ay = d["t"], d["speed"], d["steer_eq"], d["gz"], d["ay"]
    dt = float(np.median(np.diff(t))) if len(t) > 2 else 0.05
    kay, kyaw = Kalman2(dt, q=2000.0, r=0.16), Kalman2(dt, q=3000.0, r=0.0005)
    A, Y = np.zeros(len(t)), np.zeros(len(t))
    for i in range(len(t)):
        ay_f, yaw_f = kay.step(float(ay[i])), kyaw.step(float(gz[i]))
        vi = max(float(v[i]), 1e-3)
        yex = vi * math.tan(float(st_[i]) * MAXSTEER) / WB / (1.0 + (vi / VCH) ** 2)
        A[i] = (ay_f - vi * yex) / G
        Y[i] = yaw_f - yex
    return A, Y


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Ellipse, Rectangle
    try:
        font_manager.fontManager.addfont(FONT)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False

    runs = []
    for f in sorted(glob.glob(str(ROOT / "logs/dyn_k*.csv"))):
        m = KPH_RE.search(f)
        if not m:
            continue
        d = np.genfromtxt(f, delimiter=",", names=True)
        if d.ndim == 0 or len(d) < 20 or d["inside"].astype(int).sum() < 5:
            continue
        i0 = int(np.argmax(d["inside"].astype(int) > 0))
        A, Y = residuals(d)
        # 확정 **이후**는 그리지 않는다. 비상 제어가 급제동·카운터스티어를 걸어 잔차가
        # ±3 g 까지 튀는데, 그건 감지 이야기가 아니고 그림을 다 덮어 버린다.
        rad = np.hypot(A / LAT, Y / YAW)
        stop = len(A)
        c = 0
        for i in range(i0, len(A)):
            c = c + 1 if rad[i] >= 1.0 else 0
            if c >= 5:
                stop = min(len(A), i + 6)     # 확정 순간이 보이게 조금만 더
                break
        runs.append((int(m.group(1)), A[i0:stop], Y[i0:stop]))
    if not runs:
        print("dyn_k*.csv 가 없다. scripts/speed_dyn_sweep.sh 를 먼저 돌릴 것.")
        return
    runs.sort()

    fig, ax = plt.subplots(figsize=(8.6, 7.2), dpi=150)
    ax.add_patch(Rectangle((-LAT, -YAW), 2 * LAT, 2 * YAW, fill=False,
                           ec="#666", lw=2.0, ls="--", zorder=2))
    ax.add_patch(Ellipse((0, 0), 2 * LAT, 2 * YAW, fill=False,
                         ec="#D64550", lw=2.4, zorder=3))
    ax.axhline(0, color="#ccc", lw=0.8, zorder=1)
    ax.axvline(0, color="#ccc", lw=0.8, zorder=1)

    colors = ["#3E7CB1", "#6AA84F", "#E69138", "#8E7CC3", "#A64D79"]
    for k, (kph, A, Y) in enumerate(runs):
        n = len(A)
        sz = np.linspace(6, 46, n)
        ax.scatter(A, Y, s=sz, color=colors[k % len(colors)], alpha=0.55,
                   edgecolors="none", zorder=4, label=f"{kph} km/h")
        ax.plot(A, Y, color=colors[k % len(colors)], lw=0.7, alpha=0.35, zorder=4)

    # 모서리에 갇힌 지점을 짚어 준다
    stuck = next((r for r in runs if r[0] == 35), None)
    if stuck:
        _, A, Y = stuck
        m = (np.abs(A) > 0.80 * LAT) & (np.abs(A) < LAT) & (np.abs(Y) > 0.80 * YAW) & (np.abs(Y) < YAW)
        if m.any():
            j = int(np.argmax(m))
            ax.annotate("여기서 멈춰 있었다\n사각형 안, 타원 밖",
                        xy=(A[j], Y[j]), xytext=(A[j] * 0.35, Y[j] * 1.75),
                        fontsize=12, color="#b00020", ha="center",
                        arrowprops=dict(arrowstyle="->", color="#b00020", lw=1.4))

    ax.set_xlabel("횡가속도 잔차  ay_g  (g)", fontsize=12)
    ax.set_ylabel("yaw rate 잔차  yaw_err  (rad/s)", fontsize=12)
    ax.set_title("2차 방어 판정 경계 — 사각형에서 타원으로", fontsize=15)
    lim_x = min(0.95, max(0.45, float(max(np.abs(A).max() for _, A, _ in runs)) * 1.15))
    lim_y = min(1.05, max(0.50, float(max(np.abs(Y).max() for _, _, Y in runs)) * 1.15))
    ax.set_xlim(-lim_x, lim_x)
    ax.set_ylim(-lim_y, lim_y)
    ax.grid(alpha=0.2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    leg = ax.legend(title="빙판 진입 속도", loc="upper right", fontsize=10)
    leg.get_title().set_fontsize(10)
    ax.text(0.02, 0.02,
            "점선 사각형 = 기존 판정 (한 축이라도 넘어야 발화)\n"
            "붉은 타원 = 바뀐 판정 (두 축을 합쳐 본다)\n"
            "점이 클수록 나중 시각 · 빙판 진입부터 확정까지만 그렸다\n"
            "(확정 뒤에는 비상 제어가 걸려 잔차가 3 g 까지 튄다 — 감지 이야기가 아니다)",
            transform=ax.transAxes, fontsize=9.5, color="#555", va="bottom")
    fig.tight_layout()
    FIG.mkdir(parents=True, exist_ok=True)
    out = FIG / "rule_boundary.jpg"
    fig.savefig(out, pil_kwargs={"quality": 92})
    plt.close(fig)
    print(f"[저장] {out}  (주행 {len(runs)}건: {', '.join(str(r[0]) + ' km/h' for r in runs)})")


if __name__ == "__main__":
    main()
