#!/usr/bin/env python3
"""마찰 인식 제동 시점(SLIP_ENVELOPE) 결과 — 표 그림과 CARLA 비교.

입력: sw/fw/npu_lib/slip_env_table.h 와 같은 근거(mu_table.json), logs/envelope/sweep/env_*.json (carla_envelope.py)
출력: sw/docs/data/envelope/sweep_summary.json, sw/docs/figures/env_table.png, env_sweep.png
표 그림의 거리는 보드가 쓰는 식 그대로다: 지연 0.3 s + v²/(2 × 0.9 × 실측 감속) + 여유 4 m.
"""
import json
import pathlib
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
SW = ROOT / "logs/envelope2/sweep"; OUT = ROOT / "sw/docs/data/envelope"; FIG = ROOT / "sw/docs/figures"
OUT.mkdir(parents=True, exist_ok=True)
for f in ("NanumGothic", "Noto Sans CJK KR"):
    if any(f in x.name for x in font_manager.fontManager.ttflist): plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
T_R, MARGIN, A_USE = 0.30, 4.0, 0.90
mt = json.loads((ROOT / "sw/docs/data/gain_sched/mu_table.json").read_text())["table"]
MU = sorted((v["mu_eff"], v["decel_by_brake"][3]) for v in mt.values())
mus = np.array([m for m, _ in MU]); dec = np.array([d for _, d in MU])


def need(v_kph, mu):
    v = v_kph / 3.6; a = A_USE * np.interp(mu, mus, dec)
    return v * T_R + v * v / (2 * a) + MARGIN


def style(ax, title):
    ax.set_facecolor(SURF); ax.figure.set_facecolor(SURF)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5); ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=8)


# ---- 1) 표: 마찰 × 속도 → 제동을 시작해야 하는 거리 ----
mu_rows = [0.034, 0.066, 0.099, 0.164, 0.245, 0.408, 0.673]
v_cols = [30, 40, 50, 60, 70, 80]
Z = np.array([[need(v, m) for v in v_cols] for m in mu_rows])
fig, ax = plt.subplots(figsize=(7.6, 3.9), dpi=150)
style(ax, "정지 차량 앞에서 제동을 시작해야 하는 거리 (m) — 2차 방어가 20 ms 마다 계산하는 값")
im = ax.imshow(np.log10(Z), cmap="Blues", aspect="auto")
for i in range(len(mu_rows)):
    for j in range(len(v_cols)):
        ax.text(j, i, f"{Z[i, j]:.0f}", ha="center", va="center", fontsize=8.5, color="white" if np.log10(Z[i, j]) > 2.2 else INK)
ax.set_xticks(range(len(v_cols))); ax.set_xticklabels([f"{v} km/h" for v in v_cols], color=INK2)
ax.set_yticks(range(len(mu_rows)))
lab = {0.673: "0.67 마른 노면", 0.408: "0.41 젖은 노면 정도", 0.245: "0.25 다진 눈 정도", 0.164: "0.16 블랙아이스", 0.099: "0.10", 0.066: "0.07", 0.034: "0.03 매끈한 얼음"}
ax.set_yticklabels([f"μ {lab[m]}" for m in mu_rows], color=INK2)
ax.text(0, -0.9, "", fontsize=1)
fig.text(0.01, 0.01, "CARLA 0.9.16 실측 감속(제동 1.0) 기준 · 지연 0.3 s · 표 감속의 90 % · 여유 4 m. 비교 대상(마른 노면 기준)은 맨 아래 줄만 쓴다.",
         fontsize=7.5, color=INK2)
fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(FIG / "env_table.png"); plt.close(fig)
print("표 (μ × km/h → m):"); [print(f"  μ {m:.3f}: " + "  ".join(f"{z:6.0f}" for z in r)) for m, r in zip(mu_rows, Z)]

# ---- 2) CARLA 비교 ----
rows = []
for f in sorted(SW.glob("env_*.json")):
    d = json.loads(f.read_text())
    if "arm" not in d: continue
    br = next((m for m in d.get("modes", []) if m["mode"] in ("PREBRAKE", "BRAKE")), None)
    rows.append(dict(arm=d["arm"], fr=d["friction"], mu=round(0.82 * d["friction"], 3), kph=int(d["target_kph"]), result=d["result"],
                     pre_kph=d.get("collision", {}).get("pre_kph"), stop_gap=d.get("stopped", {}).get("gap"),
                     mu_probe=d.get("mu_probe"), brake_gap=br["gap"] if br else None, brake_kph=br["kph"] if br else None))
(OUT / "sweep_summary.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
for r in sorted(rows, key=lambda r: (r["fr"], r["kph"], r["arm"])):
    print(f"μ {r['mu']:.3f} {r['kph']} km/h {r['arm']:3s}: {r['result']:7s} "
          + (f"충돌 {r['pre_kph']} km/h" if r["pre_kph"] is not None else f"정지 {r['stop_gap']} m 앞" if r["stop_gap"] is not None else "")
          + f"  제동 시작 {r['brake_gap']} m 앞" + (f"  탭 μ {r['mu_probe']:.3f}" if r["mu_probe"] else ""))
if rows:
    cells = sorted({(r["mu"], r["kph"]) for r in rows})
    fig, ax = plt.subplots(figsize=(8.6, 3.6), dpi=150)
    style(ax, "빙판 위 정지 차량 (빙판 시작 +150 m) — 충돌 직전 속도 (km/h, 0 = 정지)")
    x = np.arange(len(cells))
    for k, (arm, col, name) in enumerate((("dry", "#8a8985", "지금 기술: 마른 노면 기준"), ("mu", "#2a78d6", "2차 방어: 노면 확인 탭 + 마찰 인식"))):
        ys = [next((r["pre_kph"] or 0.0 for r in rows if (r["mu"], r["kph"]) == c and r["arm"] == arm), np.nan) for c in cells]
        ax.bar(x + (k - 0.5) * 0.38, ys, width=0.36, color=col, label=name)
        for xi, (yv, c) in enumerate(zip(ys, cells)):
            r = next((r for r in rows if (r["mu"], r["kph"]) == c and r["arm"] == arm), None)
            t = f"{yv:.0f}" if yv else (f"정지\n{r['stop_gap']:.0f} m" if r and r["stop_gap"] is not None else "")
            ax.text(xi + (k - 0.5) * 0.38, (yv or 0) + 1, t, ha="center", va="bottom", fontsize=6.5, color=INK2)
    ax.set_xticks(x); ax.set_xticklabels([f"μ {m:.2f}\n{k} km/h" for m, k in cells], fontsize=7.5, color=INK2)
    ax.grid(True, axis="y", color=GRID, lw=0.6); ax.set_ylabel("km/h", color=INK2, fontsize=9); ax.legend(fontsize=8, frameon=False, loc="upper left")
    fig.tight_layout(); fig.savefig(FIG / "env_sweep.png"); plt.close(fig)

# ---- 3) 탭 판단 기준 시험 (카메라 위험도 + 결빙 취약 구간) ----
CAM = ROOT / "logs/envelope2/cam"
cam_rows = []
for f in sorted(CAM.glob("env_*.json")):
    d = json.loads(f.read_text())
    if "risk_max" not in d: continue
    pr = d.get("probes", [])
    cam_rows.append(dict(tag=d["tag"], weather=d["weather"], temp=d["temp"], ice=not d["no_ice"], zone=d["zone"], kph=int(d["target_kph"]),
                         mu=round(0.82 * d["friction"], 3) if not d["no_ice"] else None, risk_max=d["risk_max"],
                         gray=d["risk_frac_gray"], alarm=d["risk_frac_alarm"], result=d["result"],
                         stop_gap=d.get("stopped", {}).get("gap"), pre_kph=d.get("collision", {}).get("pre_kph"),
                         probes=[dict(by=p["by"], on_ice=p["inside_ice"], mu=p.get("mu_probe"), gap=p["gap"]) for p in pr]))
if cam_rows:
    (OUT / "probe_trigger_summary.json").write_text(json.dumps(cam_rows, indent=1, ensure_ascii=False))
    print("\n탭 판단 기준 시험:")
    for r in cam_rows:
        cond = f"{r['weather']:10s} {r['temp']:+.0f}°C {'빙판 μ%.2f' % r['mu'] if r['ice'] else '빙판 없음  '} {'구간O' if r['zone'] else '구간X'} {r['kph']}km/h"
        pz = ", ".join(f"{p['by']}{'(빙판)' if p['on_ice'] else '(밖)'} μ{p['mu']:.2f}" if p['mu'] is not None else p['by'] for p in r["probes"]) or "탭 없음"
        res = f"정지 {r['stop_gap']} m" if r["stop_gap"] is not None else f"충돌 {r['pre_kph']} km/h" if r["pre_kph"] is not None else r["result"]
        print(f"  {cond} | 위험도 최대 {r['risk_max']:.2f} 애매 {r['gray']*100:.1f}% | {pz} | {res}")
    # 대표 주행 시간 흐름
    main = CAM / "env_mu_f0.2_k60_o150_cam_ClearNoon_t-3.csv"
    if main.exists():
        X = np.genfromtxt(main, delimiter=",", names=True, dtype=None, encoding=None)
        t = X["t"].astype(float); v = X["v"].astype(float) * 3.6; rk = X["risk"].astype(float); md = X["mode"]; ins = X["inside"].astype(int)
        d = json.loads((CAM / "env_mu_f0.2_k60_o150_cam_ClearNoon_t-3.json").read_text())
        fig, ax = plt.subplots(figsize=(8.6, 3.4), dpi=150); style(ax, "결빙 취약 구간 계기 탭 — 대표 주행 (μ 0.16, 60 km/h, 1차 카메라는 빙판을 못 봄)")
        ax.plot(t, v, color="#2a78d6", lw=1.6, label="속도 km/h")
        ax.fill_between(t, 0, 70, where=ins > 0, color="#cfe3f7", alpha=0.6, step="mid", label="빙판 위")
        ax.plot(t, rk * 100, color="#eb6834", lw=1.0, label="1차 위험도 ×100")
        for p in d.get("probes", []):
            ax.axvline(p["t"], color=INK, lw=0.8, ls="--")
            ax.text(p["t"] + 0.2, 64, f"탭 → μ {p.get('mu_probe', 0):.2f}" if p.get("mu_probe") is not None else "탭", fontsize=7, color=INK, rotation=90, va="top")
        ax.set_xlim(10, t.max()); ax.set_ylim(0, 70); ax.set_xlabel("초", color=INK2, fontsize=8.5); ax.grid(True, axis="y", color=GRID, lw=0.6)
        ax.legend(fontsize=7.5, frameon=False, loc="upper right")
        fig.tight_layout(); fig.savefig(FIG / "env_probe_timeline.png"); plt.close(fig)
