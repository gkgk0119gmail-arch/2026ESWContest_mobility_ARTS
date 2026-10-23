#!/usr/bin/env python3
"""19번 문서용 — s10: (1) μ 0.066 직선 빙판 위 정지한 앞차에서 고정 게인 / μ 인식 정지거리 / 회피 금지(차로 안 제동만) 비교,
(2) 고장 없으면 피하는 장면에서 라즈베리파이 멈춤 주입 — 2차 방어 위치(STM32 / STM32 + 호스트 감시 / 파이).
입력: logs/gain_sched/runs/s10, 출력: sw/docs/data/gain_sched/s10_summary.json, sw/docs/figures/si_noevade.png, si_fault.png
충돌 속도는 충돌 0.1 s 전 속도다."""
import json, pathlib, re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN = ROOT / "logs/gain_sched/runs/s10"; OUT = ROOT / "sw/docs/data/gain_sched/s10_summary.json"; FIG = ROOT / "sw/docs/figures"
for f in ("NanumGothic", "Noto Sans CJK KR"):
    if any(f in x.name for x in font_manager.fontManager.ttflist): plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
MU_EFF = 0.82 * 0.08   # 2단계 보정 μ_eff ≈ 0.82 × friction
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]


def outcome(tag):
    ev = json.loads((RUN / f"events_{tag}.json").read_text())["events"]
    tr = json.loads((RUN / f"trace_{tag}.json").read_text()); t = np.array([r["t"] for r in tr]); v = np.array([r["speed_kph"] for r in tr])
    col = next((e for e in ev if e["event"] == "collision"), None)
    sl = next((e["t"] for e in ev if e["event"] == "secondary_slip"), None)
    dec = None
    pe = next((e["t"] for e in ev if e["event"] == "patch_enter"), None)
    db = next((e["t"] for e in ev if e["event"] == "driver_brake"), None)
    t_chk = sl if sl is not None else (db + 0.44 if db is not None and pe is not None and db > pe else None)   # 방어 없음·확정 전 고장은 운전자 제동 기준
    if t_chk is not None:   # 확정 0.5~6.5 s 구간 평균 감속/g — 오염 검사 (서버에 남은 마찰 상태)
        t_end = min(t_chk + 6.5, col["t"] - 0.2) if col else t_chk + 6.5   # 충돌 충격이 섞이지 않게
        i0, i1 = np.argmin(np.abs(t - t_chk - 0.5)), np.argmin(np.abs(t - t_end))
        dec = float((v[i0] - v[i1]) / 3.6 / (t[i1] - t[i0]) / 9.81) if t[i1] - t[i0] > 1.0 else None
    return dict(dec=dec, valid=dec is None or abs(dec / MU_EFF - 1) <= 0.35, triggered=sl is not None,
                evaded=any(e["event"] == "emergency_mode" and e["mode"].startswith("evade") for e in ev), tag=tag, crash=col is not None, with_=col["with"] if col else None,
                pre=float(v[np.argmin(np.abs(t - (col["t"] - 0.1)))]) if col else 0.0,
                modes=list(dict.fromkeys(e["mode"] for e in ev if e["event"] == "emergency_mode")),
                stopped=any(e["event"] == "stabilized" for e in ev), departure=any(e["event"] == "lane_departure" for e in ev),
                degraded=any(e["event"] == "host_watchdog_degraded" for e in ev), timeout=any(e["event"] == "timeout" for e in ev))


def style(ax, title):
    ax.set_facecolor(SURF); ax.figure.set_facecolor(SURF)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5); ax.grid(True, axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=8)


allrows = {f.name[7:-5]: outcome(f.name[7:-5]) for f in sorted(RUN.glob("events_s10_*.json"))}
rows = {k: r for k, r in allrows.items() if r["valid"]}
bad = sorted(k for k, r in allrows.items() if not r["valid"])
print(f"주행 {len(allrows)}, 마찰 검사 통과 {len(rows)}, 탈락 {len(bad)} {bad[:5]}, 2차 방어 미작동 {sum(not r['triggered'] for r in rows.values())}")
ab, fault = {}, {}
for tag, r in rows.items():
    m = re.match(r"s10_k(\d+)_t([0-9.]+)_(auto|assist)_(fixed|muaware|noevade)_r(\d+)", tag)
    if m: ab.setdefault((int(m.group(1)), float(m.group(2)), m.group(3)), {}).setdefault(m.group(4), []).append(r); continue
    m = re.match(r"s10_fault_(stm32w|stm32|pi|none)(?:_h([0-9.inf]+))?_r(\d+)", tag)
    if m: fault.setdefault((m.group(1), m.group(2) or "0"), []).append(r)
ARMS = ("fixed", "muaware", "noevade")
summ = {"ab": [], "fault": [], "n_runs": len(allrows), "n_invalid": len(bad), "mu_eff": MU_EFF,
        "dec_range": [round(min(r["dec"] for r in rows.values() if r["dec"]), 4), round(max(r["dec"] for r in rows.values() if r["dec"]), 4)],
        "not_triggered": sorted(k for k, r in rows.items() if not r["triggered"])}
xt = {}
for tag, r in rows.items():
    arm = "fault" if "_fault_" in tag else tag.split("_")[4]
    k = f"{arm}|{'미작동' if not r['triggered'] else '회피' if r['evaded'] else '차로 유지'}"; xt.setdefault(k, [0, 0]); xt[k][0] += 1; xt[k][1] += r["crash"]
summ["evade_x_crash"] = {k: {"n": v[0], "crash": v[1]} for k, v in sorted(xt.items())}
for (kph, ttc, mode), d in sorted(ab.items()):
    summ["ab"].append({"kph": kph, "ttc": ttc, "mode": mode, **{a: {"n": len(d.get(a, [])), "crash": sum(x["crash"] for x in d.get(a, [])),
                                                                     "pre": [round(x["pre"], 1) for x in d.get(a, [])],
                                                                     "not_triggered": sum(not x["triggered"] for x in d.get(a, []))} for a in ARMS}})
for (host, dur), d in sorted(fault.items()):
    summ["fault"].append({"host": host, "dur": dur, "n": len(d), "crash": sum(x["crash"] for x in d), "pre": [round(x["pre"], 1) for x in d],
                          "degraded": sum(x["degraded"] for x in d), "not_triggered": sum(not x["triggered"] for x in d), "departure": sum(x["departure"] for x in d)})
OUT.write_text(json.dumps(summ, indent=1, ensure_ascii=False))
for k, v in summ["evade_x_crash"].items(): print(f"{k:24s} 주행 {v['n']:3d} 충돌 {v['crash']}")
for c in summ["ab"]:
    print(f"{c['kph']} km/h 제동 {c['ttc']:.0f}s 전 {c['mode']:6s} " + "  ".join(f"{a} {c[a]['crash']}/{c[a]['n']} {c[a]['pre']}" for a in ARMS))
for c in summ["fault"]:
    print(f"고장 {c['host']:6s} {c['dur']:>4s}s  충돌 {c['crash']}/{c['n']} {c['pre']}  축소모드 {c['degraded']}  이탈 {c['departure']}  미작동 {c['not_triggered']}")
# 그림: 회피 금지 비교
lab = {"fixed": "고정 게인 (보드 지금)", "muaware": "μ 인식 정지거리 (일찍 회피)", "noevade": "회피 금지 (차로 안 제동)"}
fig, axs = plt.subplots(1, 2, figsize=(8.4, 3.5), dpi=150, sharey=True)
for ax, mode, ttl in zip(axs, ("auto", "assist"), ("보드가 조향 (자율)", "운전자가 조향 (보조)")):
    cs = [c for c in summ["ab"] if c["mode"] == mode]; x = np.arange(len(cs))
    for i, a in enumerate(ARMS):
        ax.bar(x + (i - 1) * 0.26, [c[a]["crash"] / max(c[a]["n"], 1) * 100 for c in cs], width=0.24, color=C[i], label=lab[a])
    style(ax, ttl); ax.set_xticks(x); ax.set_xticklabels([f"{c['kph']} km/h\n{c['ttc']:.0f} s 전" for c in cs], fontsize=7.5, color=INK2)
    ax.set_ylabel("충돌 비율 (%)", color=INK2, fontsize=8.5); ax.set_ylim(0, 108)
    for xi, c in enumerate(cs):
        if all(c[a]["crash"] == 0 for a in ARMS):
            ax.text(xi, 4, "빙판 전\n정지" if all(c[a]["not_triggered"] == c[a]["n"] for a in ARMS) else "모두\n멈춤",
                    ha="center", va="bottom", fontsize=7, color=INK2)
h, l = axs[0].get_legend_handles_labels()
fig.legend(h, l, fontsize=7.5, frameon=False, ncol=3, loc="upper right", bbox_to_anchor=(0.99, 0.99))
fig.suptitle("μ 0.066 직선 빙판 위 정지한 앞차 · 반복 3회 · 아래 숫자는 운전자 제동 시점(충돌까지 남은 시간)", fontsize=9, color=INK, x=0.02, y=0.90, ha="left")
fig.tight_layout(rect=(0, 0, 1, 0.86)); fig.savefig(FIG / "si_noevade.png"); plt.close(fig)
# 그림: 고장 주입
hosts = [("stm32", "STM32 (감시 없음)"), ("stm32w", "STM32 + 호스트 감시"), ("pi", "파이에서 실행")]
durs = ["0.5", "1.0", "2.0", "inf"]
fig, ax = plt.subplots(figsize=(7.0, 3.4), dpi=150); style(ax, "라즈베리파이 멈춤 (μ 0.066, 40 km/h) — 충돌 비율, 고장 없음이면 피하는 장면")
base = next((c for c in summ["fault"] if c["host"] == "none"), None)
for k, (h, nm) in enumerate(hosts):
    ys = [next((c["crash"] / c["n"] * 100 for c in summ["fault"] if c["host"] == h and c["dur"] == d), np.nan) for d in durs]
    ax.bar(np.arange(4) + (k - 1) * 0.26, ys, width=0.24, color=C[k], label=nm)
ax.set_xticks(range(4)); ax.set_xticklabels(["0.5 s", "1.0 s", "2.0 s", "죽음"], fontsize=9, color=INK2); ax.set_ylim(0, 110)
ax.set_ylabel("충돌 비율 (%)", color=INK2, fontsize=8.5); ax.legend(fontsize=7.5, frameon=False)
if base: ax.text(3.4, 102, f"고장 없음: 충돌 {base['crash']}/{base['n']}", fontsize=8, color=INK2, ha="right")
fig.tight_layout(); fig.savefig(FIG / "si_fault_straight.png"); plt.close(fig)

# ---- s11: 직선 빙판에서 2차 방어가 운전자 제동보다 나은가 + 확정 전 파이 정지 ----
RUN11 = ROOT / "logs/gain_sched/runs/s11"
if RUN11.exists():
    RUN = RUN11
    s11 = {}
    for f in sorted(RUN11.glob("events_s11_*.json")):
        tag = f.name[7:-5]; r = outcome(tag)
        m = re.match(r"s11_k(\d+)_t(\d+)_(nodef|fixed|hang_stm32|hang_pi)_r(\d+)", tag)
        s11.setdefault(f"{m.group(1)}|{m.group(2)}|{m.group(3)}", []).append(r)
    summ["s11"] = [{"kph": int(k.split("|")[0]), "ttc": float(k.split("|")[1]), "arm": k.split("|")[2], "n": len(v),
                    "crash": sum(x["crash"] for x in v), "pre": [round(x["pre"], 1) for x in v], "valid": sum(x["valid"] for x in v),
                    "triggered": sum(x["triggered"] for x in v), "dec": [round(x["dec"], 3) if x["dec"] else None for x in v]}
                   for k, v in sorted(s11.items())]
    OUT.write_text(json.dumps(summ, indent=1, ensure_ascii=False))
    for c in summ["s11"]:
        print(f"s11 {c['kph']} km/h 제동 {c['ttc']:.0f}s 전 {c['arm']:10s} 충돌 {c['crash']}/{c['n']} {c['pre']}  확정 {c['triggered']}  감속/g {c['dec']}  마찰검사 {c['valid']}/{c['n']}")

# ---- s12: 곡선 빙판(2차 방어가 결과를 바꾸는 자리)에서 파이 멈춤 — 위치별 ----
RUN12 = ROOT / "logs/gain_sched/runs/s12"
if RUN12.exists():
    RUN = RUN12
    s12 = {}
    for f in sorted(RUN12.glob("events_s12_*.json")):
        tag = f.name[7:-5]; r = outcome(tag)
        m = re.match(r"s12_f([0-9.]+)_k(\d+)_(\w+?)_r(\d+)$", tag)
        fr = float(m.group(1)); r["valid"] = r["dec"] is None or abs(r["dec"] / (0.82 * fr) - 1) <= 0.35   # 이 묶음은 마찰이 칸마다 다르다
        s12.setdefault((fr, int(m.group(2)), m.group(3)), []).append(r)
    A12 = ["nodef", "fixed", "pre_pi", "pre_stm32", "pre_stm32w", "mid_pi", "mid_stm32"]
    summ["s12"] = [{"fr": k[0], "mu": round(0.82 * k[0], 3), "kph": k[1], "arm": k[2], "n": len(v), "crash": sum(x["crash"] for x in v),
                    "pre": [round(x["pre"], 1) for x in v], "valid": sum(x["valid"] for x in v), "triggered": sum(x["triggered"] for x in v),
                    "degraded": sum(x["degraded"] for x in v), "modes": sorted({m for x in v for m in x["modes"]})}
                   for k, v in sorted(s12.items(), key=lambda kv: (kv[0][0], kv[0][1], A12.index(kv[0][2])))]
    OUT.write_text(json.dumps(summ, indent=1, ensure_ascii=False))
    for c in summ["s12"]:
        print(f"s12 μ {c['mu']:.3f} {c['kph']} km/h {c['arm']:11s} 충돌 {c['crash']}/{c['n']} {c['pre']}  확정 {c['triggered']}  축소 {c['degraded']}  마찰검사 {c['valid']}/{c['n']}  {c['modes']}")
    lab12 = {"nodef": "방어 없음", "fixed": "2차 방어 · 고장 없음", "pre_pi": "2차 방어가 파이 · 확정 전 파이 죽음 (진입 0.3 s)",
             "pre_stm32": "2차 방어가 STM32 · 확정 전 파이 죽음", "pre_stm32w": "STM32 + 파이 감시(축소 모드) · 확정 전 파이 죽음",
             "mid_pi": "2차 방어가 파이 · 진입 1.5 s 파이 죽음", "mid_stm32": "2차 방어가 STM32 · 진입 1.5 s 파이 죽음"}
    cells = sorted({(c["fr"], c["kph"]) for c in summ["s12"]})
    fig, ax = plt.subplots(figsize=(9.0, 3.8), dpi=150)
    style(ax, "곡선 빙판에서 라즈베리파이가 죽으면 — 충돌 직전 속도 (km/h, 낮을수록 좋다 · 반복 2회가 같은 값)")
    col12 = ["#b5b3ad", "#52514e", C[1], C[0], C[2], C[1], C[0]]
    hat = [None, None, None, None, None, "///", "///"]
    w = 0.115
    for i, arm in enumerate(A12):
        ys = [np.mean(next((c["pre"] for c in summ["s12"] if (c["fr"], c["kph"]) == ck and c["arm"] == arm), [np.nan])) for ck in cells]
        xs = np.arange(len(cells)) + (i - 3) * w
        ax.bar(xs, ys, width=w * 0.92, color=col12[i], hatch=hat[i], edgecolor=SURF, lw=0.4, label=lab12[arm])
        for xv, yv in zip(xs, ys): ax.text(xv, yv + 0.8, f"{yv:.0f}", ha="center", fontsize=6.5, color=INK2)
    ax.set_xticks(range(len(cells))); ax.set_xticklabels([f"μ {0.82*fr:.3f} · {k} km/h" for fr, k in cells], fontsize=8.5, color=INK2)
    ax.set_ylabel("km/h", color=INK2, fontsize=9); ax.legend(fontsize=7, frameon=False, ncol=2, loc="upper left", bbox_to_anchor=(0, -0.12))
    fig.tight_layout(); fig.savefig(FIG / "si_fault.png"); plt.close(fig)
