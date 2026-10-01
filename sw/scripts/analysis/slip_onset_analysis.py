#!/usr/bin/env python3
"""저속에서 2차 방어가 늦는 이유를 **감지기 탓과 물리 탓으로 가른다**.

변동 스윕에서 "빙판 진입 → 미끄러짐 확정"이 40 km/h 1.78 s, 35 km/h 4.23 s 로 갈렸다.
발표에서 "왜 느리냐"를 물으면 답이 두 개 중 하나여야 한다.

  (가) 감지기 탓  — 임계값이 고정(0.3 g / 0.35 rad/s)이라 저속의 작은 잔차가 못 닿는다
  (나) 물리 탓    — 저속에서는 요구 횡가속도 v²/R 이 작아 **미끄러짐 자체가 늦게 시작**한다

가르는 법: 원시 동역학 로그(--log-dyn)에서
  · 진입      = inside 가 처음 1 이 된 시각
  · 시작(onset)= 잔차가 빙판 밖 바닥 잡음의 k 배를 처음 넘은 시각
  · 확정      = 감지기 규칙(임계 N샘플 연속)이 성립한 시각
시작이 늦으면 (나), 시작은 같은데 확정이 늦으면 (가)다.

같이 잰다: 속도 정규화 지표들이 확정을 앞당기는지.
  ay_g     = (ay − v·yaw_exp)/g        지금 쓰는 횡가속도 잔차 (g)
  yaw_err  = gz − yaw_exp              지금 쓰는 yaw rate 잔차 (rad/s)
  beta_dot = ay/v − gz                 사이드슬립 각속도 — 모델 없이 순수 기구학 (rad/s)
  kappa_err= yaw_err / v               경로 곡률 오차 (1/m) — 같은 yaw 오차라도 저속이 더 급하게 휜다

사용: python3 sw/scripts/analysis/slip_onset_analysis.py
"""
from __future__ import annotations

import glob
import math
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.imu_slip import Kalman2, SlipDetector  # noqa: E402

G = 9.80665
WB, VCH, MAXSTEER = 2.7, 17.0, math.radians(35)
LAT_THR, YAW_THR = 0.3, 0.35
ONSET_K = 4.0          # 바닥 잡음의 몇 배를 "시작"으로 볼까
ONSET_HOLD = 3         # 그 상태가 몇 샘플 이어져야 시작으로 인정
# 바닥 잡음이 유난히 조용하면 4배도 의미 없는 크기가 된다. 절대 바닥을 같이 둔다.
# 값은 "사람이 타고 있으면 느낄 만한" 최소치 — 0.05 g, 0.05 rad/s(≈3°/s).
ONSET_FLOOR = {"ay_g": 0.05, "yaw_err": 0.05, "beta_dot": 0.02, "kappa_err": 0.004}


def residuals(d):
    """CSV 한 편을 칼만 필터에 통과시켜 네 잔차의 시계열을 만든다."""
    t, v, st_, gz, ay = (d["t"], d["speed"], d["steer_eq"], d["gz"], d["ay"])
    dt = float(np.median(np.diff(t))) if len(t) > 2 else 0.05
    kay, kyaw = Kalman2(dt, q=2000.0, r=0.16), Kalman2(dt, q=3000.0, r=0.0005)
    n = len(t)
    out = {k: np.zeros(n) for k in ("ay_g", "yaw_err", "beta_dot", "kappa_err")}
    for i in range(n):
        ay_f, yaw_f = kay.step(float(ay[i])), kyaw.step(float(gz[i]))
        vi = max(float(v[i]), 1e-3)
        delta = float(st_[i]) * MAXSTEER
        yaw_exp = vi * math.tan(delta) / WB / (1.0 + (vi / VCH) ** 2)
        out["ay_g"][i] = (ay_f - vi * yaw_exp) / G
        out["yaw_err"][i] = yaw_f - yaw_exp
        out["beta_dot"][i] = ay_f / vi - yaw_f
        out["kappa_err"][i] = out["yaw_err"][i] / vi
    return t, out, dt


def first_run(mask, hold):
    """mask 가 hold 샘플 연속으로 참이 되는 첫 인덱스."""
    c = 0
    for i, m in enumerate(mask):
        c = c + 1 if m else 0
        if c >= hold:
            return i - hold + 1
    return None


def analyze(path):
    d = np.genfromtxt(path, delimiter=",", names=True)
    if d.ndim == 0 or len(d) < 20:
        return None
    ins = d["inside"].astype(int)
    if ins.sum() < 5:
        return None
    t, R, dt = residuals(d)
    i_enter = int(np.argmax(ins > 0))
    t_enter = float(t[i_enter])
    pre = slice(0, max(i_enter - 2, 3))          # 빙판 밖 = 바닥 잡음
    row = {"file": pathlib.Path(path).name, "dt": dt,
           "kph": round(float(d["speed"][i_enter]) * 3.6, 1), "t_enter": t_enter}

    # 시작(onset): 어느 잔차든 바닥 잡음의 ONSET_K 배를 넘은 첫 순간
    onsets = {}
    for k, y in R.items():
        base = float(np.std(y[pre])) or 1e-6
        thr = max(ONSET_K * base, ONSET_FLOOR.get(k, 0.0))
        j = first_run(np.abs(y[i_enter:]) > thr, ONSET_HOLD)
        onsets[k] = None if j is None else float(t[i_enter + j]) - t_enter
        row[f"base_{k}"] = base
    row["onset"] = min([v for v in onsets.values() if v is not None], default=None)
    row["onset_by"] = min(((v, k) for k, v in onsets.items() if v is not None), default=(None, "-"))[1]

    # 확정: 지금 규칙 (|ay_g|≥0.3 또는 |yaw_err|≥0.35) 5샘플 연속
    hit = (np.abs(R["ay_g"]) >= LAT_THR) | (np.abs(R["yaw_err"]) >= YAW_THR)
    j = first_run(hit[i_enter:], 5)
    row["confirm"] = None if j is None else float(t[i_enter + j]) - t_enter

    # 빙판 구간에서 각 잔차가 도달한 최대치 — 임계값과의 거리
    seg = slice(i_enter, len(t))
    for k, y in R.items():
        row[f"peak_{k}"] = float(np.max(np.abs(y[seg])))
    row["_t"], row["_R"], row["_i0"] = t, R, i_enter
    return row


KPH_RE = re.compile(r"_k(\d+)\.csv$")


def collect():
    """dyn_k*.csv = 빙판 주행, dync_k*.csv = 같은 속도의 빙판 없는 대조군."""
    ice, ctrl = [], []
    for f in sorted(glob.glob(str(ROOT / "logs/dyn*_k*.csv"))) + sorted(glob.glob(str(ROOT / "logs/dyn_k*.csv"))):
        name = pathlib.Path(f).name
        m = KPH_RE.search(name)
        if not m:
            continue
        (ctrl if name.startswith("dync_") else ice).append((int(m.group(1)), f))
    return sorted(set(ice)), sorted(set(ctrl))


def main():
    ice_files, ctrl_files = collect()
    rows = [r for r in (analyze(f) for _, f in ice_files) if r]
    if not rows:
        print("동역학 로그가 없다. sw/scripts/archive/speed_dyn_sweep.sh 를 먼저 돌릴 것.")
        return
    rows.sort(key=lambda r: r["kph"])

    L = ["# 저속에서 2차 방어가 늦는 이유 — 감지기 탓인가 물리 탓인가\n\n",
         "변동 스윕에서 빙판 진입 → 미끄러짐 확정이 40 km/h 1.78 s, 35 km/h 4.23 s 로 갈렸다.\n",
         "감지기가 둔한 것인지, 저속에서 미끄러짐 자체가 늦게 시작하는 것인지 갈라야 처방이 나온다.\n\n",
         "원시 동역학 로그에서 **시작**(잔차가 빙판 밖 바닥 잡음의 4배를 3샘플 연속 초과)과 ",
         "**확정**(현재 규칙이 5샘플 연속 성립)을 따로 쟀다.\n\n"]

    L.append("## 1. 시작과 확정을 나눠 보면\n\n")
    L.append("| 진입 속도 | 진입→시작 | 진입→확정 | 확정−시작 | 먼저 움직인 잔차 |\n|---|---|---|---|---|\n")
    for r in rows:
        f = lambda x: "-" if x is None else f"{x:.2f} s"
        gap = "-" if (r["onset"] is None or r["confirm"] is None) else f"{r['confirm']-r['onset']:.2f} s"
        L.append(f"| {r['kph']:.0f} km/h | {f(r['onset'])} | {f(r['confirm'])} | {gap} | {r['onset_by']} |\n")

    L.append("\n## 2. 빙판 구간에서 각 잔차가 도달한 최대치\n\n")
    L.append("임계값에 **얼마나 가까스로 닿는가**를 본다. 여유가 없으면 조건이 조금만 바뀌어도 못 잡는다.\n\n")
    L.append("| 속도 | ay_g (임계 0.30 g) | yaw_err (임계 0.35) | beta_dot | kappa_err |\n|---|---|---|---|---|\n")
    for r in rows:
        L.append(f"| {r['kph']:.0f} km/h | {r['peak_ay_g']:.3f} ({r['peak_ay_g']/LAT_THR:.2f}배) | "
                 f"{r['peak_yaw_err']:.3f} ({r['peak_yaw_err']/YAW_THR:.2f}배) | "
                 f"{r['peak_beta_dot']:.3f} | {r['peak_kappa_err']:.4f} |\n")

    # 판정
    ons = [r["onset"] for r in rows if r["onset"] is not None]
    gaps = [r["confirm"] - r["onset"] for r in rows if r["onset"] is not None and r["confirm"] is not None]
    L.append("\n## 3. 판정\n\n")
    if len(rows) >= 2 and ons:
        slow = max(rows, key=lambda r: r["confirm"] if r["confirm"] is not None else -1)
        fast = min((r for r in rows if r["confirm"] is not None), key=lambda r: r["confirm"], default=None)
        if fast and slow["confirm"] is not None and slow is not fast:
            d_on = (slow["onset"] or 0) - (fast["onset"] or 0)
            d_cf = slow["confirm"] - fast["confirm"]
            share = 0.0 if d_cf == 0 else max(0.0, min(1.0, d_on / d_cf))
            L.append(f"- {slow['kph']:.0f} km/h 가 {fast['kph']:.0f} km/h 보다 확정이 **{d_cf:.2f} s** 늦다.\n")
            L.append(f"- 그중 **{d_on:.2f} s** 는 미끄러짐이 늦게 *시작*해서 생긴 차이다 — 전체의 {share*100:.0f} %.\n")
            L.append(f"- 나머지 {d_cf-d_on:.2f} s 가 감지기가 임계에 닿기까지 걸린 시간이다.\n\n")
            if share >= 0.6:
                L.append("**결론: 주로 물리 탓이다.** 저속에서는 요구 횡가속도 v²/R 이 작아 그만큼 늦게 미끄러진다.\n"
                         "감지기 임계값을 낮춰도 없는 신호를 당겨 쓸 수는 없다. 다만 늦게 미끄러진다는 것은 "
                         "**그만큼 늦게 위험해진다**는 뜻이기도 하므로, 정지 여유는 오히려 저속 쪽이 크다.\n")
            else:
                L.append("**결론: 감지기 탓이 크다.** 미끄러짐은 비슷한 시점에 시작하는데 확정이 늦다.\n"
                         "고정 임계값이 저속의 작은 잔차에 닿지 못한다 — 속도 정규화가 필요하다.\n")
    if gaps:
        L.append(f"\n- 시작 → 확정 간격: {min(gaps):.2f} ~ {max(gaps):.2f} s "
                 f"(평균 {sum(gaps)/len(gaps):.2f} s). 이 구간이 감지기가 실제로 책임지는 몫이다.\n")

    # ---- 4. 판정 규칙 세 가지를 같은 궤적에 놓고 비교 ----
    # 지금 규칙은 두 잔차를 OR 로 묶은 **직사각형** 판정이다. 35 km/h 에서 ay_g 가 0.287 까지
    # 올라가고 멈춰 임계 0.300 을 4 % 차이로 못 넘었고, 그래서 훨씬 느린 yaw 경로를 기다렸다.
    # 모서리에 딱 걸린 것이다. 대안 둘을 같은 궤적에 얹어 본다.
    #
    #   R1 사각형(현재)  |ay_g| ≥ 0.30  또는  |yaw_err| ≥ 0.35
    #   R2 타원          √((ay_g/0.30)² + (yaw_err/0.35)²) ≥ 1
    #                    한쪽만 넘어도 발화하는 것은 같고, **둘 다 0.71 쯤일 때도** 발화한다.
    #                    정의상 R1 보다 절대 늦을 수 없다. 새 상수도 필요 없다.
    #   R3 속도 정규화   |ay_g| ≥ 0.30·(v/11.11)²  또는  |yaw_err| ≥ 0.35
    #                    잔차가 v² 에 비례한다면 임계도 따라가야 민감도가 속도에 무관해진다.
    #                    단, 고속에서는 임계가 올라가 오히려 늦어질 수 있다 — 그래서 재 본다.
    V_REF = 40.0 / 3.6

    def rules(ay_g, yaw_err, v):
        r1 = (np.abs(ay_g) >= LAT_THR) | (np.abs(yaw_err) >= YAW_THR)
        r2 = np.hypot(ay_g / LAT_THR, yaw_err / YAW_THR) >= 1.0
        sc = np.clip((np.maximum(v, 1e-3) / V_REF) ** 2, 0.35, 1.6)
        r3 = (np.abs(ay_g) >= LAT_THR * sc) | (np.abs(yaw_err) >= YAW_THR)
        return {"R1 사각형(현재)": r1, "R2 타원": r2, "R3 속도정규화": r3}

    NAMES = ["R1 사각형(현재)", "R2 타원", "R3 속도정규화"]

    L.append("\n## 4. 판정 규칙을 바꾸면 얼마나 빨라지나\n\n")
    L.append("35 km/h 주행의 확정 순간 잔차는 **ay_g 0.287 · yaw_err 0.353** 이었다.\n")
    L.append("횡가속도가 임계 0.300 을 **4 % 차이로** 못 넘어, 훨씬 느린 yaw 경로를 기다린 것이다.\n")
    L.append("두 잔차를 OR 로 묶은 직사각형 판정의 모서리에 딱 걸렸다.\n\n")
    L.append("- **R1 사각형(현재)**: `|ay_g| ≥ 0.30` 또는 `|yaw_err| ≥ 0.35`\n")
    L.append("- **R2 타원**: `√((ay_g/0.30)² + (yaw_err/0.35)²) ≥ 1` — 한쪽만 넘어도 발화하는 건 같고, "
             "둘 다 0.71 쯤일 때도 발화한다. 정의상 R1 보다 늦을 수 없고 새 상수도 없다.\n")
    L.append(f"- **R3 속도정규화**: 임계를 `0.30 × (v/{V_REF:.2f})²` 로 속도에 따라 움직인다.\n\n")
    L.append("| 속도 | " + " | ".join(NAMES) + " | R2 이득 |\n|---|" + "---|" * (len(NAMES) + 1) + "\n")
    for r in rows:
        f = [x for _, x in ice_files if pathlib.Path(x).name == r["file"]]
        d = np.genfromtxt(f[0], delimiter=",", names=True)
        t_, R_, i0 = r["_t"], r["_R"], r["_i0"]
        got = {}
        for nm, mask in rules(R_["ay_g"], R_["yaw_err"], d["speed"]).items():
            j = first_run(mask[i0:], 5)
            got[nm] = None if j is None else float(t_[i0 + j]) - r["t_enter"]
        fmt = lambda x: "-" if x is None else f"{x:.2f} s"
        gain = ("-" if (got[NAMES[0]] is None or got[NAMES[1]] is None)
                else f"**{got[NAMES[0]] - got[NAMES[1]]:+.2f} s**")
        L.append(f"| {r['kph']:.0f} km/h | " + " | ".join(fmt(got[n]) for n in NAMES) + f" | {gain} |\n")
        r["rule_times"] = got

    # 대가: 같은 속도의 빙판 없는 주행에서 헛발질하는가
    if ctrl_files:
        L.append("\n## 5. 대가 — 빙판 없는 같은 속도에서 헛발질하는가\n\n")
        L.append("민감하게 만들었으면 오경보가 늘었는지 같이 재야 한다. 빙판 없는 대조군 주행에서\n")
        L.append("각 규칙이 발화하는지 본다. **전부 0 이어야 한다.**\n\n")
        L.append("| 속도 | " + " | ".join(NAMES) + " | 정상주행 여유 |\n|---|" + "---|" * (len(NAMES) + 1) + "\n")
        for kph, f in ctrl_files:
            d = np.genfromtxt(f, delimiter=",", names=True)
            if d.ndim == 0 or len(d) < 20:
                continue
            t_, R_, _ = residuals(d)
            cells = []
            for nm, mask in rules(R_["ay_g"], R_["yaw_err"], d["speed"]).items():
                cells.append("0" if first_run(mask, 5) is None else "**발화**")
            margin = float(np.max(np.hypot(R_["ay_g"] / LAT_THR, R_["yaw_err"] / YAW_THR)))
            L.append(f"| {kph} km/h | " + " | ".join(cells) + f" | 타원 반지름 최대 {margin:.2f} (1.0 이면 발화) |\n")
        L.append("\n타원 반지름 최대값이 1.0 에서 멀수록 정상 주행과 미끄러짐 사이가 넓다는 뜻이다.\n")

    out = ROOT / "logs/carla_demo/정리/12_저속지연_원인분해.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")
    return rows


if __name__ == "__main__":
    main()
