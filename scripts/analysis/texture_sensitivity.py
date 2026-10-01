#!/usr/bin/env python3
"""모델이 노면 **미세 질감**에 얼마나 기대고 있는지 시험한다.

`03_도메인갭.md` 에서 잰 결과: CARLA 원본 프레임의 국소 대비(라플라시안 분산)는 7.8,
RSCD 실사진은 60~65 다. 약 8배 차이다. 가설은 이것이다 —
**모델이 실사진의 미세 질감에 기대어 배웠다면, 질감을 지운 CARLA 화면에서는 무너진다.**

시험 방법: RSCD 실사진을 흐리게 만들어 국소 대비를 CARLA 수준까지 단계적으로 낮추고,
같은 배포 모델(`models/n6/v3_int8.onnx`)로 클래스 확률이 어떻게 변하는지 본다.
질감이 원인이면 흐림이 깊어질수록 얼음 확률이 무너져야 한다. 아니면 다른 원인을 찾아야 한다.

이 시험은 GPU 도 보드도 CARLA 도 필요 없다 — 파이 CPU 에서 끝난다.

사용: python3 scripts/analysis/texture_sensitivity.py [--n 120]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from icepredict.common.rscd import parse as rscd_parse  # noqa: E402

CLS = ["normal", "wet", "black_ice", "pothole"]
ICE = 2
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
SPEC_LO, SPEC_HI = 0.09, 0.40


def softmax(x):
    e = np.exp(x - x.max(-1, keepdims=True))
    return e / e.sum(-1, keepdims=True)


def lapvar(im):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(cv2.GaussianBlur(g, (5, 5), 0), cv2.CV_32F).var())


def smooth(im, sigma):
    """질감만 지우고 큰 구조는 남기는 흐림. sigma=0 이면 원본."""
    if sigma <= 0:
        return im
    return cv2.GaussianBlur(im, (0, 0), sigma)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--split", default="vali_20k")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default="models/n6/v3_int8.onnx")
    ap.add_argument("--out", default=str(ROOT / "logs/carla_demo/정리" / "04_질감의존.md"))
    a = ap.parse_args()

    import onnxruntime as ort
    so = ort.SessionOptions(); so.log_severity_level = 3
    sess = ort.InferenceSession(str(ROOT / a.model), so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    root = ROOT / "dataset/rscd/rscd" / a.split
    by = {c: [] for c in CLS}
    for f in sorted(root.glob("*.jpg")):
        lab = rscd_parse(f.name)
        if lab:
            by[lab.cls4].append(f)
    rng = np.random.default_rng(a.seed)

    sigmas = [0.0, 1.0, 2.0, 3.0, 4.0, 6.0]
    res: dict[str, dict[float, dict]] = {c: {} for c in CLS}

    for c in ("normal", "wet", "black_ice"):
        files = by[c]
        if not files:
            continue
        idx = rng.choice(len(files), min(a.n, len(files)), replace=False)
        imgs = [cv2.imread(str(files[i])) for i in idx]
        imgs = [im for im in imgs if im is not None]
        for sg in sigmas:
            ps, lv, sp = [], [], []
            for im in imgs:
                b = smooth(im, sg)
                lv.append(lapvar(cv2.resize(b, (224, 224), interpolation=cv2.INTER_AREA)))
                x = cv2.resize(b, (224, 224), interpolation=cv2.INTER_AREA)[:, :, ::-1]
                x = (x.transpose(2, 0, 1).astype(np.float32) / 255.0 - MEAN) / STD
                o = sess.run(None, {iname: x[None]})[0][0]
                p = softmax(o[:4])
                ps.append(p)
                sr = 1.0 / (1.0 + np.exp(-float(o[4])))
                sp.append(min(1.0, max(0.0, (sr - SPEC_LO) / (SPEC_HI - SPEC_LO))))
            P = np.array(ps)
            res[c][sg] = dict(
                lap=float(np.mean(lv)),
                acc=float((P.argmax(1) == CLS.index(c)).mean()),
                p_ice=float(P[:, ICE].mean()),
                spec=float(np.mean(sp)),
                risk=float(np.mean((0.35 * P[:, ICE] + 0.45 * np.array(sp)) / 0.80)),
            )
            print(f"  {c:10s} sigma={sg:3.1f} 대비={res[c][sg]['lap']:6.1f} "
                  f"정답률={res[c][sg]['acc']:.3f} p_ice={res[c][sg]['p_ice']:.3f} "
                  f"risk={res[c][sg]['risk']:.3f}", flush=True)

    L = ["# 모델이 노면 미세 질감에 얼마나 기대는가\n\n",
         "RSCD 실사진을 가우시안으로 흐리게 해 국소 대비를 CARLA 수준(약 8)까지 낮추면서 "
         f"배포 int8 모델(`{a.model}`)의 출력이 어떻게 변하는지 본다. 클래스별 {a.n}장.\n\n",
         "참고 값 — CARLA 원본 프레임 국소 대비 **7.8**, RSCD 원본 **60~65**.\n\n"]

    for c in ("black_ice", "normal", "wet"):
        if not res[c]:
            continue
        L.append(f"\n## {c}\n\n| 흐림 sigma | 국소 대비 | 정답률 | p(black_ice) | 반사도 | risk |\n")
        L.append("|---|---|---|---|---|---|\n")
        for sg in sigmas:
            if sg not in res[c]:
                continue
            r = res[c][sg]
            L.append(f"| {sg:.1f} | {r['lap']:.1f} | {r['acc']:.3f} | {r['p_ice']:.3f} | "
                     f"{r['spec']:.3f} | {r['risk']:.3f} |\n")

    # ---- 결론 ----
    ice0 = res["black_ice"].get(0.0)
    # CARLA 대비(7.8)에 가장 가까운 sigma 를 고른다
    cand = [(abs(r["lap"] - 7.8), sg) for sg, r in res["black_ice"].items()]
    if ice0 and cand:
        _, sg_c = min(cand)
        iceC = res["black_ice"][sg_c]
        L.append(f"\n## 결론\n\n")
        L.append(f"CARLA 수준의 매끄러움은 sigma≈{sg_c:.1f} (국소 대비 {iceC['lap']:.1f}) 에 해당한다. "
                 f"그때 얼음 사진의 결과는 이렇다.\n\n")
        L.append("| 지표 | 원본 | CARLA 수준으로 흐림 |\n|---|---|---|\n")
        L.append(f"| 정답률 | {ice0['acc']:.3f} | {iceC['acc']:.3f} |\n")
        L.append(f"| p(black_ice) | {ice0['p_ice']:.3f} | {iceC['p_ice']:.3f} |\n")
        L.append(f"| 반사도 | {ice0['spec']:.3f} | {iceC['spec']:.3f} |\n")
        L.append(f"| risk | {ice0['risk']:.3f} | {iceC['risk']:.3f} |\n")
        drop = ice0["p_ice"] - iceC["p_ice"]
        if drop > 0.25:
            L.append(f"\n얼음 확률이 **{drop:.2f} 떨어진다**. 모델은 미세 질감에 크게 기대고 있고, "
                     "CARLA 화면이 그 질감을 주지 못하는 것이 도메인 갭의 핵심이다. "
                     "해법은 둘 중 하나다 — (1) 학습 때 같은 정도의 흐림을 증강으로 넣어 질감 없이도 "
                     "판단하게 만든다(재수집 불필요, 오늘 바로 가능), (2) CARLA 카메라 프레임에 "
                     "센서 노이즈·노면 미세질감을 더해 실사진 쪽으로 끌어온다.\n")
        else:
            L.append(f"\n얼음 확률이 {drop:.2f} 밖에 안 떨어진다. 질감은 주된 원인이 아니다 — "
                     "밝기·색·반사 구조 쪽에서 원인을 더 찾아야 한다.\n")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L[-12:]))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
