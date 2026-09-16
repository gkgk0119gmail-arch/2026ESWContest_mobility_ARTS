#!/usr/bin/env python3
"""RSCD 실사진으로 OpenCV 반사도·차선 알고리즘 검증.
사용: python3 scripts/eval_reflectance.py --root dataset/rscd/rscd/test_50k --per-class 400
출력: 클래스별 spec/lane 평균·표준편차, ice vs 나머지 AUC, 히스토그램 PNG(logs/reflectance_eval.png)
"""
import argparse, os, random, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, cv2
from icepredict.common.rscd import parse
from icepredict.pi.reflectance import analyze

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / "dataset/rscd/rscd/test_50k"))
ap.add_argument("--per-class", type=int, default=400)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--roi", action="store_true", help="전방 프레임처럼 ROI 자르기 (RSCD 패치는 끄는 게 맞음)")
a = ap.parse_args()
random.seed(a.seed)

groups = collections.defaultdict(list)
for f in os.listdir(a.root):
    l = parse(f)
    if l is None: continue
    key = l.friction if l.material is None else f"{l.friction}-{l.material}-{l.uneven}"
    groups[key].append(f)
targets = ["ice", "melted_snow", "fresh_snow", "dry-asphalt-smooth", "wet-asphalt-smooth", "water-asphalt-smooth", "dry-concrete-smooth", "wet-concrete-smooth"]
res = {}
for g in targets:
    fs = random.sample(groups[g], min(a.per_class, len(groups[g])))
    spec, lane = [], []
    for f in fs:
        img = cv2.imread(os.path.join(a.root, f))
        r = analyze(img, use_roi=a.roi)
        spec.append(r.spec); lane.append(r.lane)
    res[g] = (np.array(spec), np.array(lane))
    print(f"{g:24s} n={len(fs):4d}  spec {np.mean(spec):.3f}±{np.std(spec):.3f}   lane {np.mean(lane):.3f}±{np.std(lane):.3f}")

def auc(pos, neg):
    # Mann-Whitney U 기반 AUC
    allv = np.concatenate([pos, neg]); ranks = allv.argsort().argsort() + 1
    rp = ranks[:len(pos)].sum()
    return (rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
print("\n[spec 점수로 ice 구분 AUC] (0.5=무작위, 1.0=완벽)")
for g in ["dry-asphalt-smooth", "wet-asphalt-smooth", "water-asphalt-smooth", "dry-concrete-smooth", "fresh_snow"]:
    print(f"  ice vs {g:22s}: {auc(res['ice'][0], res[g][0]):.3f}")
print("[lane 점수로 ice 구분 AUC (낮을수록 ice)]")
for g in ["dry-asphalt-smooth", "wet-asphalt-smooth"]:
    print(f"  ice vs {g:22s}: {1 - auc(res['ice'][1], res[g][1]):.3f}")

import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
for g in ["ice", "dry-asphalt-smooth", "wet-asphalt-smooth", "water-asphalt-smooth"]:
    ax[0].hist(res[g][0], bins=30, range=(0, 1), alpha=0.5, label=g)
    ax[1].hist(res[g][1], bins=30, range=(0, 1), alpha=0.5, label=g)
ax[0].set_title("specular score"); ax[1].set_title("lane visibility"); ax[0].legend()
out = Path(__file__).resolve().parents[1] / "logs/reflectance_eval.png"
plt.tight_layout(); plt.savefig(out, dpi=110); print("\nsaved", out)
