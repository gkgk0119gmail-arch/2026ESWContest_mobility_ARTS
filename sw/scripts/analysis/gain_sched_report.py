#!/usr/bin/env python3
"""게인 스케줄링(2차 방어 마찰 추정 + 마찰별 제어표) 근거 문서 18 과 그림을 만든다. 숫자는 전부 데이터 파일에서 읽는다.

입력: sw/docs/data/gain_sched/*.json (각 단계 스크립트의 출력을 옮겨 둔 것)
출력: sw/docs/evidence/18_마찰별_게인스케줄링.md, sw/docs/figures/gs_*.png
사용: python3 sw/scripts/analysis/gain_sched_report.py
"""
from __future__ import annotations

import json
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
D = ROOT / "sw/docs/data/gain_sched"
FIG = ROOT / "sw/docs/figures"
DOC = ROOT / "sw/docs/evidence/18_마찰별_게인스케줄링.md"
for f in ("NanumGothic", "Noto Sans CJK KR", "Pretendard"):
    if any(f in x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]       # 고정 순서 (팔레트 검증본)
G = 9.80665


def style(ax, title):
    ax.set_facecolor(SURF); ax.figure.set_facecolor(SURF)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9); ax.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)


def load(name):
    return json.loads((D / name).read_text())


# ---- 1) friction → μ_eff ----
mt = load("mu_table.json")
fr = np.array(sorted(float(k) for k in mt["friction_to_mu"])); mu = np.array([mt["friction_to_mu"][f"{f:g}"] for f in fr])
fig, ax = plt.subplots(figsize=(6.4, 3.8), dpi=150)
style(ax, "CARLA friction 배수 → 실효 마찰 μ_eff (2단계 보정, 마찰 9종 × 시험 9종)")
ax.plot(fr, fr * mt["low_friction_slope"], color=GRID, lw=1.5, ls="--")
ax.plot(fr, mu, color=C[0], lw=2, marker="o", ms=6, mec=SURF, mew=1.5)
for f, m in zip(fr, mu):
    if f in (0.02, 0.08, 0.3, 0.5, 1.0): ax.annotate(f"{m:.3f}", (f, m), textcoords="offset points", xytext=(6, -12), fontsize=8, color=INK2)
ax.text(0.12, 0.33, f"μ_eff ≈ {mt['low_friction_slope']:.2f} × friction", fontsize=8.5, color=INK2)
ax.set_xlabel("CARLA friction (트리거 값)", color=INK2, fontsize=9); ax.set_ylabel("μ_eff = 최대 합가속 / g", color=INK2, fontsize=9)
fig.tight_layout(); fig.savefig(FIG / "gs_mu_calib.png"); plt.close(fig)

# ---- 2) μ̂ 수렴 (pulse 시험 재생) ----
mr = load("mu_est_replay.json")
fig, ax = plt.subplots(figsize=(6.4, 3.8), dpi=150)
style(ax, "C 코어 마찰 추정값 — 펄스 제동(0.70/0.35) 로그 재생, 점선 = 실제 μ_eff")
pick = ["0.02", "0.06", "0.12", "0.2", "0.3"]
for i, f in enumerate(pick):
    r = mr["results"][f]["pulse"]; tr = np.array(r["trace"])
    ax.plot(tr[:, 0], tr[:, 1], color=C[i], lw=2, label=f"friction {f}  (μ_eff {r['mu_eff']:.3f})")
    ax.axhline(r["mu_eff"], color=C[i], lw=1, ls=":")
ax.axvline(0.96, color=INK2, lw=0.8, ls="--"); ax.text(0.99, 0.27, "펄스 3회", fontsize=8, color=INK2)
ax.set_xlim(0, 3); ax.set_ylim(0, 0.3)
ax.set_xlabel("비상 제어 시작 후 (s)", color=INK2, fontsize=9); ax.set_ylabel("추정 μ", color=INK2, fontsize=9)
ax.legend(fontsize=7.5, frameon=False, loc="upper right")
fig.tight_layout(); fig.savefig(FIG / "gs_mu_converge.png"); plt.close(fig)

# ---- 3) 마찰 트리거 결함 ----
fp = load("friction_probe.json")["results"]
lab = {"none": "트리거 없음\n(데모: tire_friction 1.0 설정)", "dry_trigger": "friction 1.0\n트리거 안", "after_ice": "빙판 트리거를\n벗어난 뒤"}
fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=150)
style(ax, "CARLA 0.9.16 — 54 km/h 조향 0.6 계단, 평균 횡가속 (m/s²)")
ks = list(lab); vals = [fp[k] for k in ks]
bars = ax.barh(range(len(ks)), vals, color=[C[1], C[0], C[1]], height=0.55)
for i, v in enumerate(vals): ax.text(v + 0.3, i, f"{v:.1f}", va="center", fontsize=9, color=INK)
ax.set_yticks(range(len(ks))); ax.set_yticklabels([lab[k] for k in ks], fontsize=8.5, color=INK2); ax.invert_yaxis()
ax.axvline(G, color=INK2, lw=0.8, ls="--"); ax.text(G + 0.2, 2.45, "1 g", fontsize=8, color=INK2)
ax.set_xlim(0, max(vals) * 1.15)
fig.tight_layout(); fig.savefig(FIG / "gs_friction_trigger.png"); plt.close(fig)

# ---- 4) 곡선 빙판 충돌 직전 속도 ----
im = load("s4d_impacts.json")       # s4c 는 강제 종료가 남긴 트리거로 마찰이 오염됐다 — 다시 모은 s4d 를 쓴다
fig, axs = plt.subplots(1, 3, figsize=(9.6, 3.6), dpi=150, sharey=False)
names = {"nodef": "방어 없음", "fixed": "고정 게인\n(보드 지금)", "cand": "탐색 후보\n(무작위 게인)"}
summary = {}
for ax, k in zip(axs, (40, 60, 80)):
    style(ax, f"진입 {k} km/h")
    for x, kind in enumerate(("nodef", "fixed", "cand")):
        v = np.array([r["pre"] for r in im if r["kph"] == k and r["kind"] == kind and r["pre"] is not None])
        jit = (np.random.default_rng(x).random(len(v)) - 0.5) * 0.35
        ax.scatter(np.full(len(v), x) + jit, v, s=16, color=C[x], alpha=0.75, edgecolors="none")
        ax.plot([x - 0.25, x + 0.25], [np.median(v)] * 2, color=INK, lw=2)
        ax.text(x + 0.28, np.median(v), f"{np.median(v):.1f}", va="center", fontsize=8, color=INK)
        summary[(k, kind)] = (len(v), float(np.median(v)), float(v.min()), float(v.max()))
    ax.set_xticks(range(3)); ax.set_xticklabels([names[n] for n in ("nodef", "fixed", "cand")], fontsize=8, color=INK2)
    ax.set_ylabel("충돌 0.1 s 전 속도 (km/h)", color=INK2, fontsize=9)
fig.suptitle("반경 약 120 m 곡선 빙판(200 m × 60 m, 마찰 5종) — 충돌 직전 속도, 선 = 중앙값", fontsize=10, color=INK, x=0.02, ha="left")
fig.tight_layout(); fig.savefig(FIG / "gs_curve_impact.png"); plt.close(fig)

# ---- 문서 ----
bi = load("baseline_impact_correction.json")
bench = load("bench_gs.json")["results"]
sv = load("surrogate_valid.json")["criteria"]
fit = load("surrogate_fit.json")["params"]
rep = mr["results"]
conv_rows = []
for f in sorted(rep, key=float):
    r = rep[f].get("pulse")
    if r: conv_rows.append(f"| {f} | {r['mu_eff']:.3f} | {r['mu_hat_end']:.3f} | {r['err_end']*100:+.0f} % | " + (f"{r['t_conv']:.2f} s" if r["t_conv"] is not None else "미수렴") + " |")
cal_rows = [f"| {f:g} | {mt['table'][f'{f:g}']['mu_eff']:.3f} | " + " / ".join(f"{x:.2f}" for x in mt['table'][f'{f:g}']['decel_by_brake']) + " |" for f in fr]
imp_rows = [f"| {k} km/h | {names[kind].replace(chr(10), ' ')} | {n} | {med:.1f} | {lo:.1f} ~ {hi:.1f} |" for (k, kind), (n, med, lo, hi) in summary.items()]
bi_rows = [f"| {b['tag']} | {b['with']} | {b['recorded_kph']:.1f} | {b['pre_impact_kph']:.1f} |" for b in bi]
dom = sv["in_domain"]; ho = sv["in_domain_held_out"]
_hr = {}
for r in load("surrogate_valid.json")["rows"]:
    if not r.get("in_domain") or r["carla"]["spin"] or r["sur"]["spin"]: continue
    _hr.setdefault(round(r["mu"], 3), []).append(abs(r["carla"]["head_max"] - r["sur"]["head_max"]) <= 10)
head_rows = [f"| {m:.3f} | {len(v)} | {np.mean(v)*100:.0f} % |" for m, v in sorted(_hr.items())]
txt = f"""# 18. 2차 방어 확장 — 마찰 추정과 마찰별 제어표 (진행 중)

> 자동 생성: `sw/scripts/analysis/gain_sched_report.py` · 데이터: `sw/docs/data/gain_sched/` · 브랜치 `feature/gain-sched`

2차 방어의 비상 제어는 지금 **손으로 고른 고정 게인**이다 (펄스 제동 0.70/0.35, 조향 PD 1.4/0.15/0.30, 정지거리 감속 1.0 m/s²).
마찰이 달라도 같은 값을 쓴다. 이 작업은 그것을 **실시간 마찰 추정 → 마찰·속도별 제어표 조회**로 바꾸려는 것이다.
표는 시뮬레이션에서 미리 만들고 보드는 찾아 쓰기만 한다. 모든 변경은 컴파일 플래그(`SLIP_MU_EST`, `SLIP_GAIN_SCHED`, 기본 0) 뒤에 있다.

## 지금까지 된 것

| 단계 | 결과 |
|---|---|
| 1. 시뮬 = 보드 코드 | CARLA `--slip-ctypes` 가 보드 펌웨어와 **같은 `slip_core.h`** 를 ctypes 로 부른다. C = 파이썬 v7 참조 = torch 대리 제어기, 샘플 단위 동치 시험 통과 |
| 2. 마찰 보정 | CARLA friction ≤ 0.3 에서 **μ_eff ≈ {mt['low_friction_slope']:.2f} × friction**. 바퀴 잠김으로 감속이 줄지는 않는다 |
| 3. 마찰 추정기 | 마찰원 μ̂ = √(ax²+ay²)/g (펄스 강 구간만, 상승 느리게·하강 빠르게). μ_eff ≤ 0.41 전 구간에서 **1 s 안에 ±30 %** |
| 7(일부). 보드 이식 준비 | `slip_gain_lut.h` 자동 생성기, 쌍선형 보간 C 경로, 펌웨어 패처 갱신(문법 검사), **플래그 0 출력 비트 단위 동일** |
| 4. 대리 모델 | 보정·교차검증 진행 중 — **기준 미달** (아래) |
| 5·6·8 | 대리 모델 검증 뒤로 미룸 |

![μ 보정](../figures/gs_mu_calib.png)

| friction | μ_eff | 평균 감속 m/s² (제동 0.3 / 0.5 / 0.7 / 1.0) |
|---|---|---|
""" + "\n".join(cal_rows) + f"""

friction 0.5 부터는 바퀴당 최대 제동 토크(700 N·m)가 먼저 한계에 닿는다. 그래서 마른 노면 μ_eff 는 제동으로 잰 값이다.

![μ̂ 수렴](../figures/gs_mu_converge.png)

| friction | μ_eff | 끝 μ̂ | 오차 | ±30 % 수렴 |
|---|---|---|---|---|
""" + "\n".join(conv_rows) + f"""

마른 노면(friction 1.0)에서는 제동 토크가 먼저 한계라 타이어가 포화하지 않는다. 마찰원으로는 μ 를 잴 수 없고, 추정은 안전한 쪽(과소)으로 남는다.
마른 노면은 기존 '접지 회복' 판정(감속이 2.5 m/s² 이상 살아나면 마른 노면 제동거리)이 따로 처리한다.
회피로 차로를 옮기는 동안은 제동이 0.25 로 고정돼 '강' 펄스가 없어 추정이 멈춘다 — 설계상 한계로 남긴다.

## 발견 1 — CARLA 마찰 트리거 결함 (기존 결과에도 영향)

![트리거 결함](../figures/gs_friction_trigger.png)

데모는 차량을 만들 때 `tire_friction = 1.0` 을 주는데 **거동에 효과가 없었다.** 마른 노면은 사실 CARLA 기본값(횡 약 2 g)이었다.
또 빙판 트리거를 **벗어나면 타이어 마찰이 그 기본값으로 돌아간다.** 옆으로 미끄러지던 차가 7 m 폭 빙판을 벗어나면 2.5 g 로 급정지했다.
그래서 기존 108회 주행의 '빙판을 벗어난 뒤' 구간은 실제보다 정지가 짧고 회복이 쉽다. 이 작업의 검증 주행은 빙판을 200 m × 60 m 로 깔아
차가 멈추거나 부딪힐 때까지 트리거를 벗어나지 않게 했다. 측정: `sw/scripts/sim/carla_friction_probe.py` (전용 서버에서).

## 발견 2 — 기록된 충돌 속도는 충격 뒤 값이었다 (README 정정)

데모가 충돌 이벤트를 처리할 때 그 틱의 속도를 적었는데, 충돌 센서 콜백은 충격 뒤에 들어온다. 방호벽 충돌에서 차이가 크다.

| 주행 | 상대 | 기록 (km/h) | 충돌 0.1 s 전 (km/h) |
|---|---|---|---|
""" + "\n".join(bi_rows) + f"""

데모는 이제 직전 틱 속도를 `speed_kph`, 충격 뒤를 `speed_after_kph` 로 남긴다. 기존 이벤트 108건은 손대지 않았다.

## 발견 3 — 곡선 빙판에서는 제동·조향 방식으로 물리 한계를 못 넘는다

Town04 시험 구간은 반경 약 120 m 곡선이다. 60 km/h 로 따라가려면 2.3 m/s² 의 횡가속이 필요한데, μ 0.07 빙판의 한계는 0.65 m/s² 다.

![곡선 빙판 충돌 속도](../figures/gs_curve_impact.png)

| 진입 | 제어 | 주행 | 충돌 직전 속도 중앙값 | 범위 |
|---|---|---|---|---|
""" + "\n".join(imp_rows) + f"""

2차 방어는 방어 없음보다 충돌 직전 속도를 낮추지만, 무작위로 뽑은 후보 게인 {sum(summary[(k, 'cand')][0] for k in (40, 60, 80))}개는 고정 게인보다 낫지 않았다 (세 속도 모두 중앙값 차이 0.2 km/h 안).
같은 시드로 다시 녹화한 비교 영상(`sw/media/gs_f0.04_k60_g3.webp`, friction 0.04)에서도 두 차 모두 53 km/h 로 방호벽에 부딪혔다.
이 표는 강제 종료가 남긴 트리거 오염을 지운 뒤 다시 모은 s4d 다 — 주행마다 강제동 구간 합가속/g 가 설정 μ 의 ±35% 안인지 확인했다.
μ ≤ 0.25 곡선에서는 '얼마나 빨리 속도를 줄이나'의 물리 한계(≈ μg)가 결과를 정한다.
직선 빙판 위 정지한 앞차(정지거리 판단이 갈릴 자리)에서도 제어표·μ 인식 판단은 고정 게인을 넘지 못했다 — 결과는 운전자 제동 시점이 정했다 (19번 문서, s10·s11).

## 4단계 — 대리 모델 (감속 통과, 자세는 μ ≤ 0.034 에서만)

동역학 자전거 + 축별 PhysX 형 타이어(종·횡 슬립, 마찰원) + 축별 바퀴 회전(암시적 적분) + 엔진 브레이크(CARLA 차량은 발을 떼면 1.95 m/s², 거의 뒷축) + 원호 차로.
보정 = 개루프 시험 81회 + 폐루프 주행 일부, 검증 = 보정에 안 쓴 폐루프 주행.

| 기준 (작업 지시) | 사용 범위 전체 | 보정에 안 쓴 몫 | 목표 |
|---|---|---|---|
| μ별 속도 감소량 Δv 오차 | {', '.join(f'μ {k}: {v*100:.0f}%' for k, v in sorted(dom['dv_err_by_mu'].items()))} | {', '.join(f'μ {k}: {v*100:.0f}%' for k, v in sorted(ho['dv_err_by_mu'].items()))} | ≤ 15 % |
| 정지거리 오차 (멈춘 주행만) | {', '.join(f'μ {k}: {v*100:.0f}%' for k, v in sorted(dom['stop_err_by_mu'].items()))} | {', '.join(f'μ {k}: {v*100:.0f}%' for k, v in sorted(ho['stop_err_by_mu'].items()))} | ≤ 15 % |
| 최대 방향오차 차이 ≤ 10° 비율 (비스핀) | {dom['head_diff_le10_frac']*100:.0f} % | {ho['head_diff_le10_frac']*100:.0f} % | ≥ 90 % |
| 스핀 여부 일치 | {dom['spin_agree']*100:.0f} % | {ho['spin_agree']*100:.0f} % | ≥ 90 % |

μ별로 나누면 방향오차가 갈린다 (보정에 안 쓴 몫 포함 전체, 비스핀):

| μ | 주행 | 방향오차 차이 ≤ 10° |
|---|---|---|
""" + "\n".join(head_rows) + f"""

검증 데이터는 트리거 오염을 지운 s4d 곡선 빙판 주행이다. **감속(Δv·정지거리)은 기준을 통과한다.**
방향(자세)은 μ ≤ 0.034 에서만 기준을 넘고, μ 가 클수록 어긋난다 — 타이어가 아직 붙잡고 있는 영역의 횡력 곡선 모양이 PhysX 와 다르다는 뜻이다.
따라서 대리 모델은 **μ ≤ 0.034 블랙아이스에서만** 탐색에 쓸 수 있고, 그 위의 칸은 CARLA 로 직접 확인해야 한다.
**작업 지시대로 대리 모델이 기준을 통과하기 전에는 제어표 탐색(5단계)으로 넘어가지 않는다.**

## 연산량 (리눅스 — RTOS 최악값 근거 아님)

| 구성 | 평균 | 99 분위 | 평균 비율 |
|---|---|---|---|
""" + "\n".join(f"| {k} | {v['mean_ns']:.0f} ns | {v['p99_ns']:.0f} ns | {v['mean_ratio']:.2f} |" for k, v in bench.items()) + f"""

추가 연산은 고정 횟수다(구간 찾기 비교 ≤ 14, 보간 곱 30, logf 1, sqrtf 1). 리눅스 최댓값은 스케줄러 지터가 섞여 의미가 없다 — 한 구성에서 2.2 ms 가 튀었다(문서 10 이 말하는 바로 그것).
보드 ThreadX 실측은 보드 복귀 후 같은 펌웨어(`patch_fw_slip.py --gain-sched`)로 잰다.

## 재현

```bash
python3 sw/fw/npu_lib/test_slip_core.py          # C = 파이썬 (v7 · 마찰 추정 · 제어표 경로)
python3 sw/fw/npu_lib/test_flag0_regression.py   # 플래그 0 = 작업 전 헤더, 비트 단위
python3 sw/fw/npu_lib/test_patch_fw.py           # 펌웨어 패처 세 구성 문법 검사
python3 sw/scripts/analysis/gain_sched_report.py # 이 문서와 그림
```
"""
DOC.write_text(txt)
print("저장:", DOC, "+ 그림 4장")
