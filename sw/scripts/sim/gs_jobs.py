#!/usr/bin/env python3
"""게인 스케줄링 6단계(CARLA 후보 검증)·8단계(A/B) 작업 목록 생성 — gs_runner.py 가 읽는 JSON.

공통 시나리오 (2차 방어만, 1차 끔):
  · 빙판 200 m × 60 m, 시작은 차 앞 60 m (CARLA 는 트리거를 벗어나면 타이어 마찰을 3.5 로 되돌린다 — 그 결함이 끼지 않게)
  · 주변 차량 8대, 앞차 정차(--lead-stop) 20/35/50 m, 날씨 맑은 낮·밤·젖음·눈 (2차 방어 물리에는 영향 없음 — 다양성용)
  · 시드는 시나리오마다 고정 → 고정 게인과 후보가 같은 교통·같은 배치에서 달린다
  · 필수: 60 km/h 앞차 사례(원래 39 km/h 충돌) — 원래 조건(빙판 40 m × 7 m, 1차가 31.7 m 앞에서 늦게 경고, 주변 차량 8대)을
    1차 GT 감지(--gt-detect --detect-range 31.7)로 재현한다. 보드 NPU 가 없어서다.
μ 칸 → CARLA friction: 2단계 보정 μ_eff ≈ 0.82 × friction (friction ≤ 0.3). μ 0.47 이상은 블랙아이스가 아니라 검증하지 않는다.
사용: python3 gs_jobs.py validate --cells cells.json --out jobs/s6.json
      python3 gs_jobs.py ab --lut-tag v1 --out jobs/s8.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib

WEATHERS = ["ClearNoon", "ClearNight", "WetNoon", "Snow"]
LEADS = [20.0, 35.0, 50.0]
MU2FR = 1 / 0.82
BASE = ["--disable-primary", "--views", "split", "--patch-ahead", "160", "--patch-len", "200", "--patch-width", "60", "--traffic", "8"]
LEAD60 = ["--weather", "ClearNoon", "--target-kph", "60", "--traffic", "8", "--friction", "0.02", "--patch-len", "40", "--patch-ahead", "90",
          "--patch-width", "7", "--gt-detect", "--detect-range", "31.7"]


def scen_list(n, seed):
    combos = list(itertools.product(WEATHERS, LEADS))
    import random
    rnd = random.Random(seed); rnd.shuffle(combos)
    return combos[:n]


def ctl_args(kind, gains=None):
    if kind == "fixed": return ["--slip-ctypes", "--slip-variant", "mu"]
    if kind == "lut": return ["--slip-ctypes", "--slip-variant", "gs"]
    return ["--slip-ctypes", "--slip-variant", "gs", "--slip-gains", json.dumps(gains)]


def job(tag, fr, kph, w, lead, seed, ctl, R):
    return {"tag": tag, "args": ["--friction", f"{fr:.4f}", "--target-kph", str(kph), "--weather", w, "--lead-stop", str(lead), *BASE,
                                 "--seed", str(seed), *ctl, "--log-state", f"{R}/state_{tag}.csv", "--log-core", f"{R}/core_{tag}.csv"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["validate", "ab"])
    ap.add_argument("--cells"); ap.add_argument("--out", required=True)
    ap.add_argument("--rows", default="0,1,2,3,4,5"); ap.add_argument("--kph", default="40,60")
    ap.add_argument("--per-cell", type=int, default=6); ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--seeds60", type=int, default=5); ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    R = a.run_dir
    MU = [0.02, 0.034, 0.057, 0.096, 0.163, 0.276, 0.47, 0.8]; VK = [30, 40, 50, 60, 70, 80]
    rows = [int(x) for x in a.rows.split(",")]; kphs = [int(x) for x in a.kph.split(",")]
    jobs = []
    cells = {tuple(c["cell"]): c for c in json.loads(pathlib.Path(a.cells).read_text())["cells"]} if a.cells else {}
    for i in rows:
        for kph in kphs:
            j = VK.index(kph); fr = MU[i] * MU2FR
            for si, (w, lead) in enumerate(scen_list(a.per_cell, 1000 * i + j)):
                seed = 5000 + 100 * i + 10 * j + si
                base_tag = f"c{i}{j}_s{si}"
                if a.mode == "validate":
                    jobs.append(job(f"s6_{base_tag}_fixed", fr, kph, w, lead, seed, ctl_args("fixed"), R))
                    for t, cand in enumerate(cells[(i, j)]["top"][:a.top]):
                        jobs.append(job(f"s6_{base_tag}_top{t}", fr, kph, w, lead, seed, ctl_args("cand", cand["params"]), R))
                else:
                    jobs.append(job(f"s8_{base_tag}_fixed", fr, kph, w, lead, seed, ctl_args("fixed"), R))
                    jobs.append(job(f"s8_{base_tag}_lut", fr, kph, w, lead, seed, ctl_args("lut"), R))
    # 60 km/h 앞차 사례 (필수)
    for sd in range(a.seeds60):
        pre = "s6" if a.mode == "validate" else "s8"
        for kind in (["fixed", "lut"] if a.mode == "ab" else ["fixed"]):
            tag = f"{pre}_lead60_s{sd}_{kind}"
            jobs.append({"tag": tag, "args": [*LEAD60, "--seed", str(107 + sd), *ctl_args(kind),
                                              "--log-state", f"{R}/state_{tag}.csv", "--log-core", f"{R}/core_{tag}.csv"]})
        if a.mode == "validate":
            for i60, j60 in [(0, 3)]:
                for t, cand in enumerate(cells.get((i60, j60), {"top": []})["top"][:a.top]):
                    tag = f"s6_lead60_s{sd}_top{t}"
                    jobs.append({"tag": tag, "args": [*LEAD60, "--seed", str(107 + sd), *ctl_args("cand", cand["params"]),
                                                      "--log-state", f"{R}/state_{tag}.csv", "--log-core", f"{R}/core_{tag}.csv"]})
    pathlib.Path(a.out).write_text(json.dumps(jobs, indent=0))
    print(f"작업 {len(jobs)}개 → {a.out}")


if __name__ == "__main__":
    main()
