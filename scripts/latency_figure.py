#!/usr/bin/env python3
"""실시간성을 **한 장으로** — 리눅스와 RTOS 보드의 지연 분포를 같은 축에 놓는다.

`정리/06` 의 표는 정확하지만 "4,677 us 대 17.9 us" 를 숫자로만 보면 규모가 안 느껴진다.
같은 로그 축에 두 분포를 겹치면 **자릿수가 다르다**는 것이 바로 보인다.

두 장을 만든다.
  latency_cdf.jpg       누적분포 — 꼬리(최악값)가 어디까지 가는지가 실시간성의 본질이다
  latency_timeline.jpg  주행 한 편의 보드 응답 시계열 — 시간이 흘러도 평평하다

원본
  logs/rtos_bench/bench_cyclic_*.txt  Pi 5 리눅스 1 ms 주기 태스크의 깨어남 지연 (ns)
  logs/rtos_bench/bench_slip_*.txt    같은 C 코어를 Pi 에서 돌린 연산 시간 (ns)
  logs/carla_demo/slip_latency_*.json 보드 2차 응답 표본별 (HIL 주행 중 실측)
  logs/board_wcet.jsonl               주행별 평균·최악 (누적)

사용: python3 scripts/latency_figure.py
"""
from __future__ import annotations

import glob
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIG = ROOT / "logs/carla_demo/figures"
BENCH = ROOT / "logs/rtos_bench"
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"


def read_ns(p):
    try:
        v = np.loadtxt(p)
        return v[np.isfinite(v)] / 1000.0        # ns → us
    except Exception:
        return np.array([])


def board_samples():
    """HIL 주행에서 표본별로 남은 보드 응답 (us)."""
    out = []
    for f in sorted(glob.glob(str(ROOT / "logs/carla_demo/slip_latency_*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        out.append((pathlib.Path(f).name[13:-5], d.get("npu_busy", False),
                    np.array([s[0] for s in d["samples"]]),
                    np.array([s[1] for s in d["samples"]])))
    return out


def _style(plt):
    from matplotlib import font_manager
    try:
        font_manager.fontManager.addfont(FONT)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False
    # 로그 축 눈금이 유니코드 마이너스(U+2212)를 쓰는데 나눔 폰트에 없다. 수식 폰트도 기본으로.
    plt.rcParams["mathtext.default"] = "regular"


def cdf_fig():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)

    series = []
    for tag, label, color in (("cyclic_idle", "Pi 5 리눅스 · 주기 태스크 깨어남 (유휴)", "#B34A4A"),
                              ("cyclic_load", "Pi 5 리눅스 · 주기 태스크 깨어남 (부하)", "#8E3B3B"),
                              ("slip_idle", "Pi 5 리눅스 · 같은 C 코어 연산만", "#D19A66")):
        v = read_ns(BENCH / f"bench_{tag}.txt")
        if len(v):
            series.append((label, v, color))

    bs = board_samples()
    if bs:
        v = np.concatenate([u for _, _, _, u in bs])
        series.append((f"STM32N6 + ThreadX · 2차 전체 응답 ({len(v):,} 표본)", v, "#2E6F9E"))
    else:
        rows = [json.loads(l) for l in open(ROOT / "logs/board_wcet.jsonl") if l.strip()]
        if rows:
            v = np.array([r["avg_us"] for r in rows])
            series.append((f"STM32N6 + ThreadX · 주행별 평균 ({len(v)} 주행)", v, "#2E6F9E"))
    if not series:
        print("원본 데이터가 없다.")
        return None

    fig, ax = plt.subplots(figsize=(10.5, 5.6), dpi=150)
    worst = []
    for k, (label, v, color) in enumerate(series):
        x = np.sort(v)
        y = np.arange(1, len(x) + 1) / len(x)
        ax.plot(x, 1.0 - y, color=color, lw=2.0, label=label)
        ax.plot([x[-1]], [1.0 / len(x)], "o", color=color, ms=6)
        worst.append((x[-1], 1.0 / len(x), color, k))
    # 최악값 표시가 서로 겹치지 않게 큰 값부터 위로 계단식으로 올린다
    for rank, (xw, yw, color, _) in enumerate(sorted(worst, key=lambda r: -r[0])):
        ax.annotate(f"최악 {xw:,.0f} us", xy=(xw, yw),
                    xytext=(xw * 1.25, yw * (2.5 ** (rank + 1))),
                    color=color, fontsize=10,
                    arrowprops=dict(arrowstyle="-", color=color, lw=0.8, alpha=0.6))
    ax.axvline(20000, color="#444", ls="--", lw=1.2)
    ax.text(20000 * 1.06, 0.35, "제어 주기 20 ms", rotation=90, color="#444", fontsize=10)
    ax.set_xscale("log")
    ax.set_yscale("log")
    from matplotlib.ticker import FuncFormatter
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: ("1" if v >= 1 else f"1/{1/v:,.0f}") if v > 0 else ""))
    ax.set_xlabel("지연 (us, 로그 축)", fontsize=12)
    ax.set_ylabel("이 값을 넘을 확률 (로그 축)", fontsize=12)
    ax.set_title("실시간성은 평균이 아니라 꼬리로 정해진다", fontsize=15)
    ax.set_xlim(0.3, 40000)
    ax.grid(alpha=0.25, which="both")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(fontsize=10, loc="lower left")
    fig.tight_layout()
    out = FIG / "latency_cdf.jpg"
    fig.savefig(out, pil_kwargs={"quality": 92})
    plt.close(fig)
    return out


def timeline_fig():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)
    bs = board_samples()
    if not bs:
        return None
    # 1차 NPU 가 실제로 돌던 주행을 먼저 고른다 — RTOS 논거가 가장 잘 보이는 조건이다
    bs.sort(key=lambda r: (not r[1], -len(r[2])))
    tag, busy, t, us = bs[0]

    fig, ax = plt.subplots(figsize=(11.0, 4.4), dpi=150)
    ax.plot(t, us, color="#2E6F9E", lw=0.8, alpha=0.85)
    ax.axhline(float(us.mean()), color="#6AA84F", ls="--", lw=1.4,
               label=f"평균 {us.mean():.1f} us")
    ax.axhline(float(us.max()), color="#D64550", ls=":", lw=1.4,
               label=f"최악 {us.max():.1f} us")
    ax.set_xlabel("주행 시각 (s)", fontsize=12)
    ax.set_ylabel("2차 방어 전체 응답 (us)", fontsize=12)
    ax.set_title(f"보드 응답은 주행 내내 평평하다 — {tag}  ({len(us):,} 표본"
                 + (", 1차 NPU 동시 가동)" if busy else ", 1차 끔)"), fontsize=14)
    # 계단 변화 찾기 — 1차가 멈추면(정지·게이트) NPU 경합이 사라져 응답이 뚝 떨어진다.
    # 같은 주행 안에서 선점 비용이 그대로 드러나는 장면이라 놓치면 아깝다.
    if len(us) > 60:
        # 전역 중앙값을 쓰면 계단 위치가 뒤로 밀린다. **국소** 창으로 찾는다.
        k = 25
        best = None
        for i in range(k, len(us) - k):
            d_ = float(np.median(us[i - k:i])) - float(np.median(us[i:i + k]))
            if best is None or d_ > best[0]:
                best = (d_, i)
        if best and best[0] > 0.8:                  # 잡음이 아니라 계단이라고 볼 만한 크기
            i = best[1]
            a_, b_ = float(np.median(us[:i])), float(np.median(us[i:]))
            ax.axvline(float(t[i]), color="#8E7CC3", lw=1.2, ls="-.")
            ax.annotate(f"여기서 1차가 멈췄다\nNPU 경합 있을 때 {a_:.1f} us → 없을 때 {b_:.1f} us\n"
                        f"선점 비용 {a_-b_:.1f} us (선점이 없었다면 25,500 us)",
                        xy=(float(t[i]), b_), xytext=(float(t[i]) + (t[-1] - t[0]) * 0.03,
                                                      max(25.0, float(us.max()) * 1.35) * 0.72),
                        fontsize=10.5, color="#5B4A93",
                        arrowprops=dict(arrowstyle="->", color="#8E7CC3", lw=1.2))

    ax.set_ylim(0, max(25.0, float(us.max()) * 1.35))
    ax.grid(alpha=0.22)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(fontsize=10, loc="upper right")
    ax.text(0.01, 0.06, "같은 그림을 리눅스로 그리면 세로축이 5,000 us 까지 올라간다 "
                        "(제어 주기 20 ms 의 23 %)",
            transform=ax.transAxes, fontsize=10, color="#B34A4A")
    fig.tight_layout()
    out = FIG / "latency_timeline.jpg"
    fig.savefig(out, pil_kwargs={"quality": 92})
    plt.close(fig)
    return out


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    for fn in (cdf_fig, timeline_fig):
        p = fn()
        print(f"[저장] {p}" if p else f"[건너뜀] {fn.__name__} — 원본 없음")


if __name__ == "__main__":
    main()
