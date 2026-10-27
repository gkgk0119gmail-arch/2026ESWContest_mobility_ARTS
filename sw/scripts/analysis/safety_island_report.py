#!/usr/bin/env python3
"""19번 문서용 — 직선 빙판 위 정지한 앞차(s9): 판단 A/B(고정 게인 vs μ 인식)와 라즈베리파이 고장 주입 결과를 집계·그림으로.

입력: logs/gain_sched/runs/s9/{events,trace}_s9_*.json (CARLA, gs_runner.py)
출력: sw/docs/data/gain_sched/s9_summary.json, sw/docs/figures/si_*.png
충돌 속도는 충돌 0.1 s 전 속도다 (충돌 이벤트의 값은 충격 뒤라 쓰지 않는다 — 18번 문서).
"""
from __future__ import annotations

import json
import pathlib
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN = ROOT / "logs/gain_sched/runs/s9"
OUT = ROOT / "sw/docs/data/gain_sched/s9_summary.json"
FIG = ROOT / "sw/docs/figures"
for f in ("NanumGothic", "Noto Sans CJK KR"):
    if any(f in x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]


def outcome(tag):
    E = json.loads((RUN / f"events_{tag}.json").read_text()); ev = E["events"]
    tr = json.loads((RUN / f"trace_{tag}.json").read_text())
    t = np.array([r["t"] for r in tr]); v = np.array([r["speed_kph"] for r in tr])
    col = next((e for e in ev if e["event"] == "collision"), None)
    slip = next((e for e in ev if e["event"] == "secondary_slip"), None)
    modes = [e["mode"] for e in ev if e["event"] == "emergency_mode"]
    pre = float(v[np.argmin(np.abs(t - (col["t"] - 0.1)))]) if col else None
    return dict(tag=tag, detected=slip is not None, t_slip=slip["t"] if slip else None, trigger=slip["trigger"] if slip else None,
                collision=col["with"] if col else None, pre_kph=pre, modes=modes,
                stopped=any(e["event"] == "stabilized" for e in ev), spin=any(e["event"] == "spin" for e in ev),
                departure=any(e["event"] == "lane_departure" for e in ev), v_max=float(v.max()))


def style(ax, title):
    ax.set_facecolor(SURF); ax.figure.set_facecolor(SURF)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5); ax.grid(True, axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=10.5, color=INK, pad=8)


def main():
    rows = {}
    for f in sorted(RUN.glob("events_s9_*.json")):
        tag = f.name[7:-5]
        try: rows[tag] = outcome(tag)
        except FileNotFoundError: continue
    ab, fault = [], []
    for tag, r in rows.items():
        m = re.match(r"s9_ab_f([0-9.]+)_k(\d+)_t([0-9.]+)_(auto|assist)_(fixed|muaware|soft)_r(\d+)", tag)
        if m:
            r.update(fr=float(m.group(1)), kph=int(m.group(2)), ttc=float(m.group(3)), mode=m.group(4), ctl=m.group(5), rep=int(m.group(6))); ab.append(r); continue
        m = re.match(r"s9_fault_(stm32|pi)_h([0-9.inf]+)_r(\d+)", tag)
        if m: r.update(host=m.group(1), dur=m.group(2), rep=int(m.group(3))); fault.append(r)
        elif tag.startswith("s9_fault_none"): r.update(host="none", dur="0", rep=int(tag[-1])); fault.append(r)
    # 세 조건 비교: 같은 (마찰, 속도, 운전자 제동 시점, 모드, 반복) 묶음
    ARMS = ("fixed", "muaware", "soft")
    groups = {}
    for r in ab: groups.setdefault((r["fr"], r["kph"], r["ttc"], r["mode"], r["rep"]), {})[r["ctl"]] = r
    groups = {k: v for k, v in groups.items() if all(a in v for a in ARMS)}
    def crash(x): return x["collision"] is not None
    summ = {"n_groups": len(groups), "by_cell": [], "totals": {}}
    for mode in ("auto", "assist"):
        G = [v for k, v in groups.items() if k[3] == mode]
        summ["totals"][mode] = {a: {"n": len(G), "crash": sum(crash(g[a]) for g in G),
                                    "pre_median": float(np.median([g[a]["pre_kph"] for g in G if g[a]["pre_kph"] is not None])) if any(g[a]["pre_kph"] for g in G) else None}
                                for a in ARMS}
    cells = sorted({(k[0], k[1], k[2], k[3]) for k in groups})
    for c in cells:
        G = [v for k, v in groups.items() if k[:4] == c]
        summ["by_cell"].append({"fr": c[0], "kph": c[1], "ttc": c[2], "mode": c[3], "n": len(G),
                                **{a: {"crash": sum(crash(g[a]) for g in G), "pre": [g[a]["pre_kph"] for g in G],
                                       "modes": sorted({m for g in G for m in g[a]["modes"]})} for a in ARMS}})
    summ["fault"] = [{k: r[k] for k in ("host", "dur", "rep", "collision", "pre_kph", "modes", "stopped", "spin", "detected", "departure")} for r in
                     sorted(fault, key=lambda x: (x["host"], float(x["dur"]) if x["dur"] != "inf" else 1e9, x.get("rep", 0)))]
    OUT.write_text(json.dumps(summ, indent=1, ensure_ascii=False))
    # 그림 1: 조건별 충돌 수 (μ·속도 칸별, 자율/보조)
    lab = {"fixed": "고정 게인 (보드 지금)", "muaware": "μ 인식 정지거리", "soft": "μ 인식 + 약한 제동"}
    fig, axs = plt.subplots(1, 2, figsize=(8.6, 3.6), dpi=150, sharey=True)
    for ax, mode, ttl in zip(axs, ("auto", "assist"), ("보드가 조향 (자율)", "운전자가 조향 (보조)")):
        cs = [c for c in summ["by_cell"] if c["mode"] == mode]
        keys = sorted({(c["fr"], c["kph"]) for c in cs})
        x = np.arange(len(keys))
        for i, a in enumerate(ARMS):
            ys = [sum(c[a]["crash"] for c in cs if (c["fr"], c["kph"]) == k) for k in keys]
            ns = [sum(c["n"] for c in cs if (c["fr"], c["kph"]) == k) for k in keys]
            ax.bar(x + (i - 1) * 0.26, ys, width=0.24, color=C[i], label=lab[a])
        style(ax, ttl)
        ax.set_xticks(x); ax.set_xticklabels([f"μ {0.82*fr:.3f}\n{k} km/h" for fr, k in keys], fontsize=7.5, color=INK2)
        ax.set_ylabel(f"충돌 수 (칸당 {ns[0] if ns else 0}회)", color=INK2, fontsize=8.5)
    axs[0].legend(fontsize=7.5, frameon=False, loc="upper left")
    fig.suptitle("직선 빙판 위 정지한 앞차 — 같은 장면에서 제어만 바꿨다 (운전자 제동 8·11 s 전 × 반복 2)", fontsize=9.5, color=INK, x=0.02, ha="left")
    fig.tight_layout(); fig.savefig(FIG / "si_decision_ab.png"); plt.close(fig)
    # 그림 2: 고장 주입
    fl = [r for r in fault if r["host"] != "none"]
    if fl:
        fig, ax = plt.subplots(figsize=(6.6, 3.2), dpi=150); style(ax, "라즈베리파이 멈춤 시간별 충돌 직전 속도 (반복 평균, 0 = 충돌 없음)")
        durs = ["0.3", "0.6", "1.2", "inf"]
        for k, host in enumerate(("stm32", "pi")):
            ys = [float(np.mean([r["pre_kph"] or 0.0 for r in fl if r["host"] == host and r["dur"] == d])) if any(r["host"] == host and r["dur"] == d for r in fl) else np.nan for d in durs]
            ax.bar(np.arange(4) + (k - 0.5) * 0.36, ys, width=0.34, color=C[0] if host == "stm32" else C[1],
                   label="2차 방어가 STM32 (안전 전용 RTOS)" if host == "stm32" else "2차 방어가 파이 (리눅스)")
        ax.set_xticks(range(4)); ax.set_xticklabels(["0.3 s", "0.6 s", "1.2 s", "죽음"], fontsize=9, color=INK2)
        ax.set_ylabel("km/h", color=INK2, fontsize=9); ax.legend(fontsize=8, frameon=False)
        fig.tight_layout(); fig.savefig(FIG / "si_fault_s9.png"); plt.close(fig)
    print(json.dumps(summ["totals"], ensure_ascii=False))
    for c in summ["by_cell"]:
        print(f"μ {0.82*c['fr']:.3f} {c['kph']}km/h 제동 {c['ttc']:.0f}s 전 {c['mode']:6s} n {c['n']}  충돌 " + "  ".join(f"{a} {c[a]['crash']}" for a in ARMS))
    for r in summ["fault"]: print(r)


if __name__ == "__main__":
    main()
