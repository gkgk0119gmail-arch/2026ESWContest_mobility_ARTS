#!/usr/bin/env python3
"""7단계: 셀별 탐색 결과(cells.json) → μ 방향 단조·평활 → 보드용 C 헤더 sw/fw/npu_lib/slip_gain_lut.h 자동 생성.

  · 셀마다 CARLA 검증을 통과한 후보(있으면) 또는 대리 모델 CVaR 1위를 고른다.
  · μ 방향 단조: 마찰이 낮을수록 제동 강도(B_HI, B_LO, B_EVADE)는 같거나 작아야 하고(바퀴가 더 쉽게 잠긴다),
    정지거리 여유(margin)는 같거나 커야 한다. 위반하면 이웃과의 평균으로 깎는다(등위 회귀, pool-adjacent-violators).
  · 속도 방향으로는 이웃 셀과 1:2:1 평활 (탐색 잡음으로 표가 들쭉날쭉하면 보간 값이 어느 칸에도 없던 조합이 된다).
  · --fixed 를 주면 현재 보드 고정 게인으로 채운 표를 만든다 (SLIP_GAIN_SCHED=1 이어도 동작이 고정 게인과 같다 → 회귀 시험용).
사용: python3 build_gain_lut.py --cells cells.json [--carla carla_select.json] --out sw/fw/npu_lib/slip_gain_lut.h
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from icepredict.pi.gain_lut import BOUNDS, FIXED, PARAM_NAMES  # noqa: E402

MU_AXIS = [0.02, 0.034, 0.057, 0.096, 0.163, 0.276, 0.47, 0.8]
V_AXIS = [30, 40, 50, 60, 70, 80]
MONO_UP_WITH_MU = ["B_HI", "B_LO", "B_EVADE"]      # μ 가 커지면 커지거나 같다
MONO_DOWN_WITH_MU = ["margin"]                     # μ 가 커지면 작아지거나 같다


def pav(y, increasing=True):
    """등위 회귀 (pool-adjacent-violators)."""
    y = list(y) if increasing else [-v for v in y]
    blocks = [[v, 1] for v in y]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] / blocks[i][1] > blocks[i + 1][0] / blocks[i + 1][1]:
            blocks[i] = [blocks[i][0] + blocks[i + 1][0], blocks[i][1] + blocks[i + 1][1]]; del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for s, n in blocks: out += [s / n] * n
    return out if increasing else [-v for v in out]


def fixed_table():
    T = np.zeros((len(MU_AXIS), len(V_AXIS), len(PARAM_NAMES)))
    for i, mu in enumerate(MU_AXIS):
        for j in range(len(V_AXIS)):
            v = dict(FIXED); v["a_stop_scale"] = 1.0 / (mu * 9.80665)       # 고정 a_ice 1.0 m/s² 를 그대로 재현
            T[i, j] = [v[k] for k in PARAM_NAMES]
    return T


def from_cells(cells_json, carla_sel=None):
    d = json.loads(pathlib.Path(cells_json).read_text())
    mu_axis = d.get("mu_axis") or MU_AXIS; v_axis = d.get("v_axis") or V_AXIS
    T = fixed_table(); src = {}
    pick = json.loads(pathlib.Path(carla_sel).read_text()) if carla_sel else {}
    for c in d["cells"]:
        i, j = c["cell"]
        key = f"{i}-{j}"
        if key in pick:
            p = pick[key]["params"]; src[key] = "carla"
        else:
            p = c["top"][0]["params"]; src[key] = "surrogate"
        T[i, j] = [p[k] for k in PARAM_NAMES]
    return T, src


def regularize(T):
    T = T.copy()
    for k in MONO_UP_WITH_MU + MONO_DOWN_WITH_MU:
        t = PARAM_NAMES.index(k)
        for j in range(T.shape[1]):
            T[:, j, t] = pav(T[:, j, t], increasing=k in MONO_UP_WITH_MU)
    S = T.copy()
    for j in range(T.shape[1]):
        lo, hi = max(j - 1, 0), min(j + 1, T.shape[1] - 1)
        S[:, j] = (T[:, lo] + 2 * T[:, j] + T[:, hi]) / 4.0
    S[:, :, PARAM_NAMES.index("PULSE_HALF")] = np.round(S[:, :, PARAM_NAMES.index("PULSE_HALF")])
    for t, k in enumerate(PARAM_NAMES):
        lo, hi = BOUNDS[k]
        if k == "a_stop_scale": hi = max(hi, float(S[:, :, t].max()))         # 고정 표는 범위 밖 값을 쓴다
        S[:, :, t] = np.clip(S[:, :, t], lo, hi)
    return S


def write_header(T, out, note):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    L = [f"/* 자동 생성 — 손으로 고치지 말 것. sw/scripts/model/build_gain_lut.py ({now})",
         f"   {note}",
         "   축: μ̂(마찰 추정, 로그 보간) × 속도(km/h). 값 순서: " + ", ".join(PARAM_NAMES),
         "   slip_core.h 의 SLIP_GAIN_SCHED=1 일 때만 쓰인다. */",
         "#ifndef SLIP_GAIN_LUT_H", "#define SLIP_GAIN_LUT_H",
         f"#define SLIP_GS_NMU {len(MU_AXIS)}", f"#define SLIP_GS_NV  {len(V_AXIS)}",
         "static const float SLIP_GS_MU[SLIP_GS_NMU]    = {" + ", ".join(f"{m:.4f}f" for m in MU_AXIS) + "};",
         "static const float SLIP_GS_LOGMU[SLIP_GS_NMU] = {" + ", ".join(f"{math.log(m):.6f}f" for m in MU_AXIS) + "};",
         "static const float SLIP_GS_KPH[SLIP_GS_NV]    = {" + ", ".join(f"{v:.1f}f" for v in V_AXIS) + "};",
         "static const float SLIP_GS_TABLE[SLIP_GS_NMU][SLIP_GS_NV][10] = {"]
    for i in range(T.shape[0]):
        L.append(f"  {{ /* μ {MU_AXIS[i]:.3f} */")
        for j in range(T.shape[1]):
            L.append("    {" + ", ".join(f"{x:.5f}f" for x in T[i, j]) + f"}},  /* {V_AXIS[j]} km/h */")
        L.append("  },")
    L += ["};", "#endif", ""]
    pathlib.Path(out).write_text("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells"); ap.add_argument("--carla", default=None); ap.add_argument("--fixed", action="store_true")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parents[2] / "fw" / "npu_lib" / "slip_gain_lut.h"))
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    if a.fixed:
        T = fixed_table(); note = "고정 게인 표 (회귀 시험용): SLIP_GAIN_SCHED=1 이어도 보드 고정 게인과 같은 제어"
        write_header(T, a.out, note); print("저장:", a.out); sys.exit(0)
    T, src = from_cells(a.cells, a.carla)
    R = regularize(T)
    n_c = sum(v == "carla" for v in src.values())
    note = f"근거: {a.cells}" + (f" + CARLA 검증 선택 {a.carla} ({n_c}칸)" if a.carla else "") + " — μ 방향 단조(제동 ↑, 여유 ↓), 속도 방향 1:2:1 평활"
    write_header(R, a.out, note)
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps({"mu_axis": MU_AXIS, "v_axis": V_AXIS, "params": PARAM_NAMES,
                                                        "raw": T.tolist(), "table": R.tolist(), "source": src}, indent=1))
    print("저장:", a.out)
