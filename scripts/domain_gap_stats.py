#!/usr/bin/env python3
"""CARLA 합성 얼음과 RSCD 실사진 얼음의 **영상 통계** 차이를 잰다.

"도메인 갭이 있다"는 말은 지금까지 정성적이었다. 모델이 보는 것은 224×224 ROI 한 장이므로,
그 ROI 의 통계를 두 도메인에서 같은 방식으로 재면 무엇이 얼마나 다른지 숫자로 말할 수 있다.

주의: 이 스크립트가 재는 차이(특히 국소 대비 8 배)는 **도메인 갭의 원인이 아니다.**
`scripts/texture_sensitivity.py` 가 그 가설을 기각했다 — 모델은 질감·밝기 양쪽에 강인하다.
따라서 결과를 재수집·재학습의 근거로 쓰지 말 것. 기록과 반증의 근거로만 남긴다.

재는 것 (모두 224×224 ROI 기준):
  밝기 L*        — 얼음이 노면보다 밝은가 어두운가
  채도 chroma    — 합성은 하늘색을 반사해 푸르게 치우치기 쉽다
  국소 대비      — 라플라시안 분산. 실사진 얼음은 노면 질감이 비쳐 대비가 살아 있다
  하이라이트 비율 — 상위 5% 밝기 화소가 차지하는 비중 (정반사 얼룩의 세기)
  수평 줄무늬    — 평면 반사는 세로 방향으로 늘어나 가로 에지가 강해진다

사용:
  python3 scripts/domain_gap_stats.py                 # 기본 경로로 알아서
  python3 scripts/domain_gap_stats.py --n 200
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from icepredict.common.rscd import parse as rscd_parse  # noqa: E402
from icepredict.sim.camera import ROI_TOP, ROI_BOTTOM, ROI_LEFT, ROI_RIGHT  # noqa: E402

DEMO = ROOT / "logs/carla_demo"


def roi_of_front(frame: np.ndarray) -> np.ndarray:
    """split 영상의 왼쪽 절반이 전방 카메라다. 거기서 NPU 가 실제로 먹는 ROI 를 자른다."""
    h, w = frame.shape[:2]
    front = frame[:, : w // 2]
    fh, fw = front.shape[:2]
    return front[int(fh * ROI_TOP):int(fh * ROI_BOTTOM), int(fw * ROI_LEFT):int(fw * ROI_RIGHT)]


def stats(img: np.ndarray) -> dict:
    im = cv2.resize(img, (224, 224), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, A, B = lab[:, :, 0], lab[:, :, 1] - 128.0, lab[:, :, 2] - 128.0
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    hi = float(np.percentile(L, 95))
    return dict(
        L_mean=float(L.mean()), L_std=float(L.std()),
        chroma=float(np.hypot(A, B).mean()),
        blue=float(-B.mean()),                       # B* 가 음수면 푸른 쪽
        lap_var=float(cv2.Laplacian(cv2.GaussianBlur(g, (5, 5), 0), cv2.CV_32F).var()),
        hi_frac=float((L >= hi).mean() * 100.0),
        hi_L=hi,
        # 가로 에지 / 세로 에지 — 평면 반사는 세로로 늘어나 가로 에지가 상대적으로 강해진다
        edge_hv=float((np.abs(gy).mean() + 1e-6) / (np.abs(gx).mean() + 1e-6)),
    )


def agg(rows: list[dict]) -> dict:
    if not rows:
        return {}
    return {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}


def carla_rois(n: int, want_ice: bool) -> list[np.ndarray]:
    """trace 에서 얼음 확률이 확실한(또는 확실히 정상인) 시점을 골라 그 프레임의 ROI 를 뽑는다."""
    out: list[np.ndarray] = []
    for tf in sorted(glob.glob(str(DEMO / "trace_*_detect*.json"))):
        tag = os.path.basename(tf)[len("trace_"):-len(".json")]
        vid = DEMO / f"demo_{tag}_split.mp4"
        if not vid.exists():
            continue
        try:
            tr = json.load(open(tf))
        except Exception:
            continue
        # 얼음: p[2] >= 0.8 / 정상: p[0] >= 0.9 이고 빙판에서 멀다
        picks = [s for s in tr if s.get("p") and
                 ((s["p"][2] >= 0.8) if want_ice else (s["p"][0] >= 0.9 and s.get("dist_m", 0) > 60))]
        if not picks:
            continue
        cap = cv2.VideoCapture(str(vid))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, len(picks) // 8)
        for s in picks[::step]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(s["t"] * fps))
            ok, fr = cap.read()
            if ok:
                out.append(roi_of_front(fr))
            if len(out) >= n:
                break
        cap.release()
        if len(out) >= n:
            break
    return out


def rscd_imgs(n: int, classes: tuple[str, ...], split="vali_20k", seed=0) -> list[np.ndarray]:
    root = ROOT / "dataset/rscd/rscd" / split
    files = []
    for f in sorted(root.glob("*.jpg")):
        lab = rscd_parse(f.name)
        if lab and lab.cls4 in classes:
            files.append(f)
    if not files:
        return []
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(files), min(n, len(files)), replace=False)
    out = []
    for i in idx:
        im = cv2.imread(str(files[i]))
        if im is not None:
            out.append(im)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--out", default=str(DEMO / "정리" / "03_도메인갭.md"))
    a = ap.parse_args()

    groups = {
        "CARLA 합성 얼음": carla_rois(a.n, want_ice=True),
        "CARLA 정상 노면": carla_rois(a.n, want_ice=False),
        "RSCD 실사진 얼음": rscd_imgs(a.n, ("black_ice",)),
        "RSCD 실사진 정상": rscd_imgs(a.n, ("normal",)),
    }
    res = {k: agg([stats(im) for im in v]) for k, v in groups.items()}

    cols = [("L_mean", "밝기 L*"), ("L_std", "밝기 표준편차"), ("chroma", "채도"),
            ("blue", "푸른 정도"), ("lap_var", "국소 대비"), ("hi_frac", "하이라이트 %"),
            ("edge_hv", "가로/세로 에지비")]

    L = ["# CARLA 합성 얼음 vs RSCD 실사진 — ROI 영상 통계\n\n",
         "모델이 실제로 먹는 224×224 ROI 기준. CARLA 쪽은 `trace` 에서 얼음 확률이 0.8 이상인 "
         "시점(정상은 0.9 이상이고 빙판에서 60 m 밖)의 프레임에서 잘랐다.\n\n"]
    L.append("| 지표 | " + " | ".join(groups) + " |\n" + "|---" * (len(groups) + 1) + "|\n")
    for key, name in cols:
        cells = [f"{res[g][key]:.2f}" if res.get(g) else "-" for g in groups]
        L.append(f"| {name} | " + " | ".join(cells) + " |\n")
    L.append("\n표본 수: " + ", ".join(f"{g} {len(v)}장" for g, v in groups.items()) + "\n")

    # ---- 해석: 얼음 − 정상 차이를 두 도메인에서 비교한다 ----
    if res.get("CARLA 합성 얼음") and res.get("RSCD 실사진 얼음"):
        L.append("\n## 얼음이 정상 노면과 얼마나 다른가 (도메인별 차이값)\n\n")
        L.append("| 지표 | CARLA (얼음−정상) | RSCD (얼음−정상) | 부호 일치 |\n|---|---|---|---|\n")
        for key, name in cols:
            dc = res["CARLA 합성 얼음"][key] - res["CARLA 정상 노면"][key]
            dr = res["RSCD 실사진 얼음"][key] - res["RSCD 실사진 정상"][key]
            same = "예" if (dc >= 0) == (dr >= 0) else "**아니오**"
            L.append(f"| {name} | {dc:+.2f} | {dr:+.2f} | {same} |\n")
        L.append("\n## 이 표를 어떻게 읽어야 하나 (후속 실험 결과 반영)\n\n")
        L.append("처음에는 이 차이 — 특히 국소 대비 8 배 — 가 도메인 갭의 원인이라고 보고 "
                 "`ice_render.random_ice_params` 를 맞추려 했다. **그 가설은 기각됐다.**\n\n")
        L.append("| 시험 (`04_질감의존.md`) | 결과 |\n|---|---|\n")
        L.append("| RSCD 얼음을 CARLA 수준으로 흐리게 (대비 52.7 → 8.5) | p(ice) 0.898 → 0.881 |\n")
        L.append("| RSCD 얼음을 CARLA 수준으로 밝게 (L\\* +36) | p(ice) 0.902 → 0.885 |\n")
        L.append("| 렌더 품질 Low → Epic | 경보 거리 10.3 m → 9.7 m |\n")
        L.append("\n모델은 질감·밝기 양쪽에 충분히 강인하다. 즉 **이 표의 차이는 크지만 모델이 신경 쓰지 않는 차이다.**\n\n")
        L.append("실제 제약은 카메라 기하다 — ROI(행 144~235)가 보는 노면은 7.9~42.2 m 이고 "
                 "30~60 m 구간은 15.2 px 에 눌려 있다. 확률 곡선도 그와 맞는다(43 m 에서 0.006, "
                 "31.6 m 에서 0.219, 27.4 m 에서 0.943). 자세한 것은 "
                 "`docs/research_directions_2026-09-20.md` §A·§B 를 볼 것.\n\n")
        L.append("그러므로 이 표는 **재수집·재학습의 근거로 쓰면 안 된다.** 기록으로만 남긴다.\n")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
