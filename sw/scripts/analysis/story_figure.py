#!/usr/bin/env python3
"""이중 방어를 **한 장으로** — 1차가 잡는 주행과 2차가 받는 주행을 나란히.

지금 그림들은 각각 한 가지를 증명한다. 정작 "이 시스템이 어떻게 동작하나"를
한눈에 보여주는 그림이 없었다. 발표 첫 장에 쓸 것이 필요하다.

왼쪽  1차 방어가 잡는다   — 위험도가 문턱을 넘고, 빙판 **앞에서** 선다
오른쪽 1차가 못 봤다고 치자 — 빙판에 들어가고, IMU 가 미끄러짐을 잡아 비상 제어로 선다

각 칸에 위는 위험도(문턱선 포함), 아래는 속도. 사건은 세로선으로 찍는다.

사용: python3 sw/scripts/analysis/story_figure.py [--left TAG] [--right TAG]
"""
from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
DEMO = ROOT / "logs/carla_demo"
FIG = DEMO / "figures"
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"

EV_STYLE = {
    "primary_warning": ("1차 경보", "#2E6F9E"),
    "patch_enter":     ("빙판 진입", "#8E7CC3"),
    "secondary_slip":  ("2차 미끄러짐 확정", "#D64550"),
    "stopped":         ("정지", "#6AA84F"),
    "stabilized":      ("정지", "#6AA84F"),
    "collision":       ("충돌", "#000000"),
    "lane_departure":  ("차로 이탈", "#B34A4A"),
    "spin":            ("스핀", "#B34A4A"),
}


def load(tag):
    ev = DEMO / f"events_{tag}.json"
    tr = DEMO / f"trace_{tag}.json"
    if not ev.exists() or not tr.exists():
        return None
    d = json.load(open(ev))
    rows = json.load(open(tr))
    if not rows:
        return None
    return dict(tag=tag, args=d.get("args", {}), events=d.get("events", []), rows=rows)


def pick():
    """1차가 잡은 주행 하나, 1차를 끄고 2차가 받은 주행 하나를 고른다.

    이 그림 전용으로 찍은 주행(story_primary / story_secondary)이 있으면 그것을 쓴다.
    없으면 조건에 맞는 아무 주행이나 고른다.
    """
    a, b = load("story_primary"), load("story_secondary")
    if a and b:
        return a, b
    left = right = None
    for f in sorted(DEMO.glob("events_*.json")):
        tag = f.name[7:-5]
        d = load(tag)
        if not d or d["args"].get("control_no_ice"):
            continue
        ev = {e["event"] for e in d["events"]}
        if left is None and "primary_warning" in ev and "stopped" in ev and not d["args"].get("disable_primary"):
            left = d
        if right is None and d["args"].get("disable_primary") and "secondary_slip" in ev \
                and ("stabilized" in ev or "stopped" in ev) and "collision" not in ev:
            right = d
        if left and right:
            break
    return left, right


def panel(axr, axs, d, title, th):
    t = np.array([r["t"] for r in d["rows"]])
    risk = np.array([r.get("risk", 0.0) for r in d["rows"]])
    spd = np.array([r.get("speed_kph", 0.0) for r in d["rows"]])
    inside = np.array([int(r.get("inside", 0)) for r in d["rows"]])

    if inside.any():
        i0 = int(np.argmax(inside > 0))
        i1 = len(inside) - 1 - int(np.argmax(inside[::-1] > 0))
        for ax in (axr, axs):
            ax.axvspan(t[i0], t[i1], color="#9AC7E8", alpha=0.25, lw=0)

    axr.plot(t, risk, color="#2E6F9E", lw=1.6)
    axr.axhline(th, color="#D64550", ls="--", lw=1.2)
    axr.text(0.995, th + 0.02, f"문턱 {th:.2f}", color="#D64550", fontsize=10,
             ha="right", va="bottom", transform=axr.get_yaxis_transform(which="grid"))
    axr.set_ylim(0, 1.03)
    axr.set_ylabel("1차 위험도")
    axr.set_title(title, fontsize=13)

    axs.plot(t, spd, color="#6AA84F", lw=1.8)
    axs.set_ylim(0, max(50.0, float(spd.max()) * 1.15))
    axs.set_ylabel("속도 (km/h)")
    axs.set_xlabel("주행 시각 (s)")

    # 사건 이름은 축 **안쪽**에 층을 나눠 적는다. 위로 빼면 제목과 겹친다.
    seen, lane = set(), 0
    for e in d["events"]:
        st = EV_STYLE.get(e["event"])
        if not st or e["event"] in seen:
            continue
        seen.add(e["event"])
        label, color = st
        for ax in (axr, axs):
            ax.axvline(e["t"], color=color, lw=1.3, ls=":")
        # 위험도 축은 경보가 나면 위쪽이 곡선으로 차 버린다. 이름은 속도 축 위에 적는다.
        y = (0.94, 0.84, 0.74)[lane % 3]
        lane += 1
        axs.text(e["t"], y, " " + label, color=color, fontsize=10,
                 ha="left", va="top", transform=axs.get_xaxis_transform())

    # 1차가 잡은 주행은 빙판에 들어가지 않는다 — 그래서 하늘색 띠가 없다. 그 대신 거리로 말한다.
    pw = next((e for e in d["events"] if e["event"] == "primary_warning"), None)
    stp = next((e for e in d["events"] if e["event"] in ("stopped", "stabilized")), None)
    if pw and stp and not inside.any():
        j = int(np.argmin(np.abs(t - stp["t"])))
        gap = d["rows"][j].get("dist_m")
        if gap is not None:
            axs.annotate(f"빙판 {gap:.0f} m 앞에서 정지", xy=(stp["t"], 2.0),
                         xytext=(stp["t"] - (t[-1] - t[0]) * 0.36, float(spd.max()) * 0.55),
                         fontsize=11, color="#6AA84F",
                         arrowprops=dict(arrowstyle="->", color="#6AA84F", lw=1.3))
    for ax in (axr, axs):
        ax.grid(alpha=0.2)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--left")
    ap.add_argument("--right")
    a = ap.parse_args()

    left = load(a.left) if a.left else None
    right = load(a.right) if a.right else None
    if not (left and right):
        l2, r2 = pick()
        left, right = left or l2, right or r2
    if not (left and right):
        print("쓸 만한 주행을 못 찾았다. 1차가 잡은 주행과 1차를 끈 주행이 각각 하나씩 필요하다.")
        return

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    try:
        font_manager.fontManager.addfont(FONT)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False

    th = 0.603
    try:
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from icepredict.pi.context import LocationCtx, WeatherObs, build_context
        th = build_context(WeatherObs(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                           LocationCtx(feature="bridge", hour=5)).threshold
    except Exception:
        pass

    fig, axes = plt.subplots(2, 2, figsize=(14.5, 6.6), dpi=150,
                             gridspec_kw={"height_ratios": [1.35, 1.0], "hspace": 0.12, "wspace": 0.18})
    panel(axes[0][0], axes[1][0], left,
          f"1차 방어가 잡는다 — 빙판 **앞에서** 선다\n({left['tag']})".replace("**", ""), th)
    panel(axes[0][1], axes[1][1], right,
          f"1차가 못 봤다고 치면 — 2차(IMU)가 받는다\n({right['tag']}, 1차 끔)", th)
    axes[0][1].text(0.02, 0.86, "1차를 껐으므로 위험도는 0 이다", transform=axes[0][1].transAxes,
                    fontsize=10, color="#777")
    fig.suptitle("이중 방어가 실제로 도는 모습 — 하늘색 구간이 빙판", fontsize=15, y=0.995)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.88, bottom=0.09, hspace=0.14, wspace=0.16)
    FIG.mkdir(parents=True, exist_ok=True)
    out = FIG / "story_dual_defense.jpg"
    fig.savefig(out, pil_kwargs={"quality": 92})
    plt.close(fig)
    print(f"[저장] {out}  (왼쪽 {left['tag']} / 오른쪽 {right['tag']})")


if __name__ == "__main__":
    main()
