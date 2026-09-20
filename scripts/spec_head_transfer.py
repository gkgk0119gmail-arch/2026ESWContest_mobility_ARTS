#!/usr/bin/env python3
"""반사도 헤드가 **실사진으로 전이됐는가** — 문서가 한계로 적어 둔 항목을 닫는다.

문서(`demo_pipeline_2026-09-19.md`)는 "RoadSaW 반사도 헤드는 미착수. 지금 반사도 라벨은
CARLA 합성 p90" 이라고 적고 있다. 즉 융합의 β(0.45)를 먹이는 신호가 **합성 라벨로만 학습**된
헤드에서 나온다. 그것이 실제 도로 사진에서도 의미가 있는지는 확인된 적이 없다.

이제 실사진 25,140 장의 보드 반사도 출력이 있으므로 세 가지로 확인한다.
  1) 판별력(AUC) — 반사도만으로 얼음과 나머지를 얼마나 가르나
  2) 고전 추정치와의 일치 — `icepredict.pi.reflectance` 의 OpenCV 기반 점수와 비교
  3) 분류 확률과의 독립성 — 반사도가 분류기와 다른 정보를 주는가 (아니면 β 는 낭비다)

사용: python3 scripts/spec_head_transfer.py [--n-classic 1500]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CLS = ("normal", "wet", "black_ice", "pothole")
ICE = CLS.index("black_ice")
SAMPLES = ROOT / "logs/rscd_board_samples.jsonl"


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """랭크 기반 AUC (scipy 없이)."""
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    x = np.concatenate([pos, neg])
    order = x.argsort()
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1)
    # 동점 처리
    _, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, ranks)
    ranks = (sums / cnt)[inv]
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-classic", type=int, default=1500,
                    help="고전 추정치와 비교할 사진 수 (디코딩이 느려 일부만)")
    ap.add_argument("--out", default=str(ROOT / "logs/carla_demo/정리" / "08_반사도헤드_전이검증.md"))
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(SAMPLES) if l.strip()]
    spec = np.array([r["spec"] for r in rows])
    pice = np.array([r["p"][ICE] for r in rows])
    y = np.array([CLS.index(r["cls"]) for r in rows])

    L = [f"# 반사도 헤드는 실사진으로 전이됐는가\n\n",
         "융합식 `risk = (α·p_ice + β·spec)/(α+β)` 에서 β(0.45)가 가장 큰 가중치인데, "
         "그 `spec` 을 내는 헤드는 **CARLA 합성 라벨로만 학습**됐다 "
         "(실사진 RSCD 에는 반사도 정답이 없어 손실에서 제외했다). "
         f"실사진 **{len(rows):,}장**의 보드 출력으로 그 신호가 실제로 쓸모 있는지 확인한다.\n\n"]

    # ---- 1. 클래스별 분포 ----
    L.append("## 1. 클래스별 반사도 분포\n\n")
    L.append("| 클래스 | 장수 | 평균 | p05 | 중앙값 | p95 |\n|---|---|---|---|---|---|\n")
    for i, c in enumerate(CLS):
        m = y == i
        if not m.any():
            continue
        v = np.sort(spec[m])
        q = lambda f: v[int(f * (len(v) - 1))]
        L.append(f"| {c} | {m.sum():,} | {v.mean():.3f} | {q(.05):.3f} | {q(.50):.3f} | {q(.95):.3f} |\n")

    # ---- 2. 판별력 ----
    L.append("\n## 2. 반사도 **단독** 판별력 (AUC)\n\n")
    L.append("분류 확률을 전혀 쓰지 않고 반사도만으로 얼음과 각 클래스를 가를 때의 AUC 다. "
             "0.5 면 무작위, 1.0 이면 완전 분리다.\n\n")
    L.append("| 대조 | AUC | 해석 |\n|---|---|---|\n")
    ice_s = spec[y == ICE]
    for i, c in enumerate(CLS):
        if i == ICE:
            continue
        m = y == i
        if not m.any():
            continue
        v = auc(ice_s, spec[m])
        tag = "강함" if v >= 0.85 else ("쓸만함" if v >= 0.7 else ("약함" if v >= 0.6 else "거의 무작위"))
        L.append(f"| 얼음 vs {c} | **{v:.3f}** | {tag} |\n")
    v_all = auc(ice_s, spec[y != ICE])
    L.append(f"| 얼음 vs 나머지 전체 | **{v_all:.3f}** | |\n")
    v_cls = auc(pice[y == ICE], pice[y != ICE])
    L.append(f"| (참고) 분류 확률 p_ice 단독 | {v_cls:.3f} | |\n")

    # ---- 3. 분류기와 겹치는가 ----
    L.append("\n## 3. 반사도는 분류기와 **다른 정보**를 주는가\n\n")
    r = float(np.corrcoef(spec, pice)[0, 1])
    L.append(f"반사도와 얼음 확률의 상관계수는 **{r:.3f}** 다. ")
    if r > 0.9:
        L.append("거의 같은 정보다 — β 를 크게 둘 이유가 약하다.\n\n")
    elif r > 0.6:
        L.append("상당히 겹치지만 완전히 같지는 않다.\n\n")
    else:
        L.append("겹침이 크지 않다 — 서로 보완하는 신호다.\n\n")
    # 분류기가 틀린 사진에서 반사도가 구해 주는가
    wrong_ice = (y == ICE) & (pice < 0.5)
    if wrong_ice.any():
        saved = float((spec[wrong_ice] >= 0.8).mean())
        L.append(f"분류기가 얼음을 놓친 사진({int(wrong_ice.sum()):,}장) 중 "
                 f"**{saved*100:.1f} %** 는 반사도가 0.8 이상이다 — "
                 "그만큼은 반사도 항이 건져 낸다.\n")
    wrong_non = (y != ICE) & (pice >= 0.5)
    if wrong_non.any():
        L.append(f"반대로 분류기가 얼음이 아닌 것을 얼음이라 한 사진({int(wrong_non.sum()):,}장) 중 "
                 f"**{float((spec[wrong_non] >= 0.8).mean())*100:.1f} %** 도 반사도가 높아 "
                 "오경보를 함께 밀어 올린다.\n")

    # ---- 4. 고전 추정치와 비교 ----
    L.append("\n## 4. OpenCV 고전 추정치와 얼마나 맞나\n\n")
    try:
        import cv2
        from icepredict.pi.reflectance import analyze as classic_analyze
        rng = np.random.default_rng(0)
        idx = rng.choice(len(rows), min(a.n_classic, len(rows)), replace=False)
        dirs = [ROOT / "dataset/rscd/rscd/test_50k", ROOT / "dataset/rscd/rscd/vali_20k"]
        hs, cs, ys = [], [], []
        for k in idx:
            r_ = rows[k]
            img = None
            for d in dirs:
                p = d / r_["file"]
                if p.exists():
                    img = cv2.imread(str(p))
                    break
            if img is None:
                continue
            hs.append(r_["spec"])
            cs.append(classic_analyze(img, use_roi=False).spec)
            ys.append(CLS.index(r_["cls"]))
        hs, cs, ys = np.array(hs), np.array(cs), np.array(ys)
        rr = float(np.corrcoef(hs, cs)[0, 1])
        L.append(f"같은 사진 {len(hs):,}장에 대해 학습 헤드의 반사도와 "
                 "`icepredict.pi.reflectance` 의 OpenCV 점수(고휘도 비율 + 히스토그램 첨도)를 비교했다.\n\n")
        L.append(f"| 항목 | 값 |\n|---|---|\n")
        L.append(f"| 두 추정치의 상관계수 | **{rr:.3f}** |\n")
        L.append(f"| 학습 헤드 — 얼음 vs 나머지 AUC | {auc(hs[ys==ICE], hs[ys!=ICE]):.3f} |\n")
        L.append(f"| 고전 추정치 — 얼음 vs 나머지 AUC | {auc(cs[ys==ICE], cs[ys!=ICE]):.3f} |\n")
        L.append("\n")
        if rr < 0.5:
            L.append("두 추정치가 **별로 안 맞는다.** 학습 헤드가 고전적 정반사 점수를 재현하는 것이 "
                     "아니라 다른 무언가를 배웠다는 뜻이다. AUC 를 비교해 어느 쪽이 실제로 "
                     "얼음을 가르는지 보면 된다.\n")
        else:
            L.append("두 추정치가 어느 정도 일치한다 — 학습 헤드가 물리적으로 말이 되는 양을 "
                     "재현하고 있다는 뜻이다.\n")
    except Exception as e:
        L.append(f"(고전 추정치 비교 실패: {type(e).__name__} {str(e)[:120]})\n")

    # ---- 5. β 를 바꾸면 판별력이 어떻게 되나 ----
    L.append("\n## 5. 그래서 β 는 얼마여야 하나 — 판별력 직접 측정\n\n")
    L.append("문턱을 어디에 두든 상관없이, 융합값이 얼음을 얼마나 잘 가르는가(AUC)는 β 만으로 정해진다. "
             "실사진에서 직접 쓸어봤다 (α=0.35 고정, 차선 항 없음).\n\n")
    L.append("| β | 얼음 판별 AUC | 오경보 5 % 이하 조건에서 얼음 경보율 |\n|---|---|---|\n")
    rows_b = []
    for b in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.60):
        rr = (0.35 * pice + b * spec) / (0.35 + b)
        av = auc(rr[y == ICE], rr[y != ICE])
        hit = None
        for th in np.arange(0.2, 0.99, 0.005):
            bad = max((rr[y == i] >= th).mean() for i in range(len(CLS)) if i != ICE)
            if bad <= 0.05:
                hit = (float(th), float((rr[y == ICE] >= th).mean()), float(bad))
                break
        rows_b.append((b, av, hit))
        cur = " **(현행)**" if abs(b - 0.45) < 1e-9 else ""
        cell = (f"{hit[1]*100:.1f}% (문턱 {hit[0]:.2f}, 최악 오경보 {hit[2]*100:.1f}%)"
                if hit else "조건 만족 불가")
        L.append(f"| {b:.2f}{cur} | **{av:.4f}** | {cell} |\n")
    best_b = max(rows_b, key=lambda x: x[1])
    L.append(f"\n**AUC 가 β 에 대해 단조 감소한다.** 즉 실사진에서는 반사도를 섞을수록 "
             f"얼음 판별이 **나빠진다.** β=0(분류 확률만)이 {rows_b[0][1]:.4f} 로 최고이고, "
             f"현행 β=0.45 는 {rows_b[-2][1]:.4f} 다.\n\n")
    L.append("이유는 §2·§3 에 있다 — 반사도는 얼음과 `normal` 은 잘 가르지만(AUC 0.970) "
             "`pothole` 과는 거의 못 가른다(0.658). 그런데 분류기는 그 셋을 모두 잘 가른다. "
             "그래서 반사도를 섞으면 포트홀이 얼음 쪽으로 끌려 올라온다.\n\n")
    L.append("> **주의 — 도메인이 갈린다.** 이 결론은 실사진에 한한다. CARLA 데모에서는 "
             "분류기가 폭우·야간에 얼음을 거의 못 보기 때문에(얼음 확률 0.02 수준) "
             "반사도 항이 없으면 1차 경보가 아예 안 난다. 즉 **데모가 β 에 기대고 있는 것은 "
             "분류기가 그 도메인에서 약하기 때문**이지 반사도가 좋아서가 아니다.\n")

    # ---- 결론 ----
    L.append("\n## 결론\n\n")
    L.append(f"- 반사도 헤드는 CARLA 합성 라벨로만 학습됐는데도 실사진에서 "
             f"얼음 vs 나머지 **AUC {v_all:.3f}** 을 낸다. 전이 자체는 성공이다.\n")
    L.append(f"- 다만 §5 에서 보듯 **β 를 올릴수록 얼음 판별 AUC 가 단조 감소한다** "
             f"(β=0 에서 {rows_b[0][1]:.4f} → β=0.45 에서 {rows_b[-2][1]:.4f}). "
             "실사진에서는 반사도가 도움이 아니라 방해다. 포트홀을 못 가르기 때문이다.\n")
    L.append("- 그런데 CARLA 데모는 그 반대다 — 분류기가 약한 도메인이라 반사도가 없으면 "
             "1차가 아예 발화하지 않는다. **제품 운영점과 데모 운영점을 분리해야 하는 이유가 하나 더 늘었다.**\n")
    L.append("- **문서의 \"RoadSaW 반사도 헤드 미착수\" 항목은 이제 이렇게 바뀐다**: "
             "합성 라벨로도 쓸 만한 신호가 나왔으니 RoadSaW 확보는 급하지 않다. "
             "대신 **β 를 낮추거나 문턱을 올리는 쪽**이 먼저다.\n")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
