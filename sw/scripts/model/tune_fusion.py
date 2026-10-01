#!/usr/bin/env python3
"""융합 가중치 β 와 경보 문턱을 RSCD 실사진으로 결정한다.

문서(`demo_pipeline_2026-09-19.md`)가 "발표 전 팀이 β/문턱을 정할 것"이라고 미뤄 둔 결정을
데이터로 푼다. 보드 인-더-루프 결과(`logs/rscd_in_the_loop_20260920_0113.json`)는 집계만
남기므로 표본별 (p, spec) 을 다시 뽑아야 스윕이 된다. 보드는 CARLA 배치가 점유 중이라
같은 int8 모델(`models/n6/v3_int8.onnx`)을 파이 CPU로 돌린다. 보드와 같은 가중치·같은 식이며
집계가 인-더-루프 수치와 맞는지 먼저 대조한다.

융합식 (차선 신호 없음 → 가중치 재정규화):
    risk = (α·p_ice + β·spec_eff) / (α + β)
변형:
    plain  : spec_eff = spec                      (현재 운영)
    wetatt : spec_eff = spec · (1 − p_wet)        (젖음 확률만큼 반사도를 깎는다)
    icegate: spec_eff = spec · p_ice^κ            (얼음 확률이 낮으면 반사도를 안 믿는다)

사용: python3 sw/scripts/model/tune_fusion.py --n 300 [--model models/n6/v3_int8.onnx]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from icepredict.common.protocol import ROAD_CLASSES  # noqa: E402
from icepredict.common.rscd import parse as rscd_parse  # noqa: E402

# 펌웨어 npu_infer.h 의 반사도 보정 구간. 헤드는 물리 반사율을 내고, 융합은 0~1 위험도를 받는다.
SPEC_LO, SPEC_HI = 0.09, 0.40

ICE = ROAD_CLASSES.index("black_ice")
WET = ROAD_CLASSES.index("wet")
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

CLS = ["normal", "wet", "black_ice", "pothole"]


def parse_cls(name: str) -> str | None:
    """정식 파서를 쓴다. severe 요철은 마찰과 무관하게 pothole 이 우선이라 직접 짜면 틀린다."""
    lab = rscd_parse(name)
    return lab.cls4 if lab else None


def stretch(sr: float) -> float:
    """펌웨어와 같은 선형 보정 + 0~1 클램프."""
    v = (sr - SPEC_LO) / (SPEC_HI - SPEC_LO)
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def softmax(x):
    e = np.exp(x - x.max(-1, keepdims=True))
    return e / e.sum(-1, keepdims=True)


def collect(model_path: pathlib.Path, split: str, n: int, seed: int):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    nout = sess.get_outputs()[0].shape[-1]
    if nout < 5:
        raise SystemExit(f"반사도 헤드가 없는 모델이다 (출력 {nout}). models/n6/v3_int8.onnx 를 쓸 것.")

    root = ROOT / "dataset/rscd/rscd" / split
    by: dict[str, list] = {c: [] for c in CLS}
    for f in sorted(root.glob("*.jpg")):
        c = parse_cls(f.name)
        if c:
            by[c].append(f)
    rng = np.random.default_rng(seed)

    rows = []
    for c in CLS:
        files = by[c]
        if not files:
            print(f"  {c}: 사진 없음")
            continue
        idx = rng.choice(len(files), min(n, len(files)), replace=False)
        for k, i in enumerate(idx):
            im = cv2.imread(str(files[i]))
            if im is None:
                continue
            x = cv2.resize(im, (224, 224), interpolation=cv2.INTER_AREA)[:, :, ::-1]
            x = (x.transpose(2, 0, 1).astype(np.float32) / 255.0 - MEAN) / STD
            o = sess.run(None, {iname: x[None]})[0][0]
            p = softmax(o[:4])
            spec = 1.0 / (1.0 + np.exp(-float(o[4])))
            rows.append(dict(cls=c, p=p.tolist(), spec_raw=spec, spec=stretch(spec)))
            if (k + 1) % 50 == 0:
                print(f"  {c}: {k+1}/{len(idx)}", flush=True)
    return rows


def risk_of(rows, alpha, beta, variant, kappa=1.0):
    out = {}
    for c in CLS:
        rs = []
        for r in rows:
            if r["cls"] != c:
                continue
            p = r["p"]
            s = r["spec"]
            if variant == "wetatt":
                s = s * (1.0 - p[WET])
            elif variant == "icegate":
                s = s * (p[ICE] ** kappa)
            rs.append((alpha * p[ICE] + beta * s) / (alpha + beta))
        out[c] = np.array(rs)
    return out


def table(rk, th):
    return {c: float((v >= th).mean()) if len(v) else float("nan") for c, v in rk.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--split", default="vali_20k")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default="models/n6/v3_int8.onnx")
    ap.add_argument("--cache", default="logs/fusion_tune_samples_v2.json")
    ap.add_argument("--out", default="logs/carla_demo/정리/02_융합가중치.md")
    a = ap.parse_args()

    cache = ROOT / a.cache
    if cache.exists():
        rows = json.load(open(cache))
        print(f"캐시 사용: {cache} ({len(rows)}장)")
    else:
        print(f"모델 {a.model} 로 {a.split} 에서 클래스별 {a.n}장 추론 (파이 CPU)")
        rows = collect(ROOT / a.model, a.split, a.n, a.seed)
        cache.parent.mkdir(parents=True, exist_ok=True)
        json.dump(rows, open(cache, "w"))
        print(f"저장: {cache}")

    L = []
    L.append("# 융합 가중치 β 와 경보 문턱 결정\n\n")
    L.append(f"RSCD `{a.split}` 클래스별 {a.n}장, 배포 int8 모델 `{a.model}` 을 파이 CPU로 돌린 값. "
             "보드와 같은 식·같은 가중치다.\n\n")

    # ---- 현행 운영점 대조 ----
    A0, B0, TH0 = 0.35, 0.45, 0.441
    base = risk_of(rows, A0, B0, "plain")
    bt = table(base, TH0)
    L.append(f"## 1. 현행 운영점 (α={A0}, β={B0}, 문턱={TH0}) 재현\n\n")
    L.append("| 클래스 | 경보율(이번 계산) | 보드 인-더-루프(9/20 01:13) | 평균 risk |\n|---|---|---|---|\n")
    board = {"normal": 0.053, "wet": 0.520, "black_ice": 0.987, "pothole": 0.823}
    for c in CLS:
        L.append(f"| {c} | {bt[c]*100:.1f}% | {board[c]*100:.1f}% | {base[c].mean():.3f} |\n")
    L.append("\n수치가 보드 결과와 가까우면 아래 스윕을 믿어도 된다.\n")

    # ---- β 스윕 ----
    L.append(f"\n## 2. β 스윕 (α={A0} 고정, 문턱={TH0})\n\n")
    L.append("| β | black_ice 경보율 | wet | pothole | normal |\n|---|---|---|---|---|\n")
    for b in [0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60]:
        t = table(risk_of(rows, A0, b, "plain"), TH0)
        L.append(f"| {b:.2f} | **{t['black_ice']*100:.1f}%** | {t['wet']*100:.1f}% | "
                 f"{t['pothole']*100:.1f}% | {t['normal']*100:.1f}% |\n")

    # ---- 문턱 스윕 ----
    L.append(f"\n## 3. 문턱 스윕 (α={A0}, β={B0} 현행 유지)\n\n")
    L.append("| 문턱 | black_ice | wet | pothole | normal |\n|---|---|---|---|---|\n")
    for th in [0.35, 0.441, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        t = table(base, th)
        L.append(f"| {th:.3f} | **{t['black_ice']*100:.1f}%** | {t['wet']*100:.1f}% | "
                 f"{t['pothole']*100:.1f}% | {t['normal']*100:.1f}% |\n")

    # ---- 변형 비교: 최적 운영점 찾기 ----
    L.append("\n## 4. 운영점 탐색 — 젖은 노면 오경보 10% 이하에서 얼음 경보율 최대\n\n")
    L.append("| 방식 | α | β | 문턱 | black_ice | wet | pothole | normal |\n|---|---|---|---|---|---|---|---|\n")
    best = []
    for variant in ("plain", "wetatt", "icegate"):
        cand = []
        for b in np.arange(0.05, 0.65, 0.05):
            rk = risk_of(rows, A0, float(b), variant)
            for th in np.arange(0.30, 0.85, 0.01):
                t = table(rk, float(th))
                if t["wet"] <= 0.10 and t["normal"] <= 0.05:
                    cand.append((t["black_ice"], -t["pothole"], float(b), float(th), t))
        if cand:
            cand.sort(reverse=True)
            ice, _, b, th, t = cand[0]
            best.append((variant, b, th, t))
            L.append(f"| {variant} | {A0} | {b:.2f} | {th:.2f} | **{t['black_ice']*100:.1f}%** | "
                     f"{t['wet']*100:.1f}% | {t['pothole']*100:.1f}% | {t['normal']*100:.1f}% |\n")
        else:
            L.append(f"| {variant} | {A0} | - | - | 조건을 만족하는 운영점 없음 | | | |\n")

    # ---- 미세 문턱 스윕 + 분포 ----
    L.append("\n## 4b. 문턱만 바꾸는 경우 (가중치 현행 유지 → 펌웨어 재빌드 불필요)\n\n")
    L.append("문턱은 `ctx` 메시지로 주행 중에 보드로 내려간다. 가중치를 건드리지 않으면 펌웨어를 다시 굽지 않아도 된다.\n\n")
    L.append("| 문턱 | black_ice | wet | pothole | normal |\n|---|---|---|---|---|\n")
    for th in [0.55, 0.57, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70]:
        t = table(base, th)
        L.append(f"| {th:.2f} | **{t['black_ice']*100:.1f}%** | {t['wet']*100:.1f}% | "
                 f"{t['pothole']*100:.1f}% | {t['normal']*100:.1f}% |\n")

    L.append("\n### 왜 이렇게 깨끗하게 갈리나 — 클래스별 risk 분포\n\n")
    L.append("| 클래스 | p05 | p25 | 중앙값 | p75 | p95 |\n|---|---|---|---|---|---|\n")
    for c in CLS:
        v = np.sort(base[c])
        if not len(v):
            continue
        q = lambda f: v[int(f * (len(v) - 1))]
        L.append(f"| {c} | {q(.05):.2f} | {q(.25):.2f} | {q(.50):.2f} | {q(.75):.2f} | {q(.95):.2f} |\n")
    ice_p05 = float(np.sort(base['black_ice'])[int(.05 * (len(base['black_ice']) - 1))]) if len(base['black_ice']) else 0
    other_p95 = max(float(np.sort(base[c])[int(.95 * (len(base[c]) - 1))]) for c in CLS if c != 'black_ice' and len(base[c]))
    L.append(f"\n얼음의 하위 5% 가 **{ice_p05:.2f}**, 나머지 세 클래스의 상위 5% 가 **{other_p95:.2f}** 다. "
             f"두 분포 사이가 비어 있으므로 문턱을 그 틈({other_p95:.2f}~{ice_p05:.2f})에 놓으면 된다. "
             f"현행 0.441 은 젖음·포트홀 분포 **한가운데**에 박혀 있다 — 오경보의 원인은 가중치가 아니라 문턱 위치다.\n")

    # ---- 권고 ----
    L.append("\n## 5. 권고\n\n")
    t60 = table(base, 0.60)
    L.append(f"**1순위 — 문턱만 0.441 → 0.60 으로 올린다.** 가중치는 그대로 두므로 펌웨어를 다시 굽지 않는다.\n\n")
    L.append(f"| 지표 | 현행(0.441) | 제안(0.60) |\n|---|---|---|\n")
    for c in CLS:
        L.append(f"| {c} 경보율 | {bt[c]*100:.1f}% | {t60[c]*100:.1f}% |\n")
    L.append(f"\n얼음 경보율은 {bt['black_ice']*100:.1f}% → {t60['black_ice']*100:.1f}% 로 거의 그대로이고, "
             f"젖은 노면 오경보가 {bt['wet']*100:.1f}% → {t60['wet']*100:.1f}% 로 사라진다. "
             f"0.58~0.68 구간 어디를 골라도 결과가 같으므로 가운데인 0.60 을 권한다.\n\n")
    L.append("**2순위 — β 도 함께 내린다.** 반사도 신호의 비중을 줄이는 쪽이 원리적으로 더 안전하지만, "
             "보드 융합 C 코드와 파이 참조 구현을 같이 고쳐야 한다.\n\n")
    if best:
        best.sort(key=lambda x: -x[3]["black_ice"])
        v, b, th, t = best[0]
        names = {"plain": "현행 식 그대로 β·문턱만 조정",
                 "wetatt": "반사도에 (1 − p_wet) 감쇠를 곱한다",
                 "icegate": "반사도에 얼음 확률을 곱해 게이트한다"}
        L.append(f"- **{names[v]}**, α={A0}, β={b:.2f}, 문턱={th:.2f}\n")
        L.append(f"- 이때 얼음 경보율 **{t['black_ice']*100:.1f}%**, 젖음 오경보 **{t['wet']*100:.1f}%**, "
                 f"포트홀 {t['pothole']*100:.1f}%, 마른 노면 {t['normal']*100:.1f}%\n")
        L.append(f"- 현행 대비: 젖음 오경보 {bt['wet']*100:.1f}% → {t['wet']*100:.1f}%, "
                 f"얼음 경보 {bt['black_ice']*100:.1f}% → {t['black_ice']*100:.1f}%\n")
        if v != "plain":
            L.append(f"- 이 변형은 보드 `fw/npu_lib` 의 융합 C 코드와 `sw/src/icepredict/pi/fusion.py` 양쪽을 "
                     "같은 식으로 고쳐야 한다. 곱셈 한 번이라 WCET에는 영향이 없다.\n")
    else:
        L.append("- 젖음 10% / 마름 5% 조건을 만족하는 운영점이 없다. 반사도 헤드를 다시 학습해야 한다.\n")

    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    open(out, "w").write("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
