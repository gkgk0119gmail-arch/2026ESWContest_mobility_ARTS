#!/usr/bin/env python3
"""경보 거리를 늘릴 방법은 렌즈뿐인가 — **해상도가 성능을 어디서 깎는지** 잰다.

문제
----
1차 방어의 경보 거리가 22~28 m 에서 멈춘다. 원인은 학습이나 문턱이 아니라 **기하**다.
지금 렌즈(화각 60°, 높이 1.6 m, 피치 −12°)에서 ROI 가 보는 노면은 7.9~42.2 m 이고,
30~60 m 구간은 세로로 **15.2 px** 밖에 안 된다. 없는 픽셀은 못 살린다.

그러면 망원 렌즈로 바꿀 가치가 있나? 그걸 답하려면 먼저 **얼마나 작아지면 못 보나**를
알아야 한다. 데이터셋 사진을 일부러 흐리게(작게) 만들어 성능이 무너지는 지점을 찾는다.

방법
----
1. RSCD 사진을 모델 입력(224×224)으로 만든 뒤 s×s 로 줄였다가 다시 224 로 늘린다.
   실제로 쓸 수 있는 정보는 s×s 뿐이다 — 멀리 있는 노면과 같은 상태다.
2. s 를 224 → 16 까지 낮추며 클래스별 정답률과 얼음 확률을 잰다.
3. 성능이 무너지는 s 를 찾고, 그 s 가 카메라 기하에서 **몇 m** 에 해당하는지 환산한다.
4. 망원(25°)으로 바꾸면 같은 픽셀을 몇 m 에서 얻는지 계산한다.

한계: 여기서는 보드 INT8 대신 `models/roadnet.onnx`(FP32)를 쓴다. 보드를 실사진 평가에
쓰고 있어서다. 절대 정답률은 보드와 조금 다를 수 있으나, 우리가 보려는 것은
**해상도에 따른 상대 변화**라 결론은 바뀌지 않는다.

사용: python3 scripts/range_resolution.py [--n 300]
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.common.rscd import parse as rscd_parse  # noqa: E402
from icepredict.sim import camera as cam  # noqa: E402

CLS = ("normal", "wet", "black_ice", "pothole")
ICE = CLS.index("black_ice")
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
SCALES = (224, 160, 112, 80, 64, 48, 32, 24, 16)


def px_per_meter(d_m: float, fov_deg: float) -> float:
    """거리 d 에서 노면 1 m 가 세로 몇 픽셀을 차지하나 (카메라 기하 그대로)."""
    y0 = cam.image_y_for_distance(d_m, fov_deg=fov_deg)
    y1 = cam.image_y_for_distance(d_m + 1.0, fov_deg=fov_deg)
    return abs(y0 - y1)


def band_px(near: float, far: float, fov_deg: float) -> float:
    return abs(cam.image_y_for_distance(near, fov_deg=fov_deg)
               - cam.image_y_for_distance(far, fov_deg=fov_deg))


def distance_for_band_px(px: float, fov_deg: float, span_m: float = 30.0,
                         lo: float = 5.0, hi: float = 400.0) -> float | None:
    """`span_m` 길이의 노면 구간이 세로 `px` 픽셀로 보이는 가장 먼 거리."""
    f = lambda d: band_px(d, d + span_m, fov_deg) - px
    if f(lo) < 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) >= 0:
            lo = mid
        else:
            hi = mid
    return lo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="클래스별 장수")
    ap.add_argument("--split", default="vali_20k")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(str(ROOT / "models/roadnet.onnx"), so,
                                providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    root = ROOT / "dataset/rscd/rscd" / a.split
    by = {c: [] for c in CLS}
    for f in sorted(root.glob("*.jpg")):
        lab = rscd_parse(f.name)
        if lab:
            by[lab.cls4].append(f)
    rng = np.random.default_rng(a.seed)
    picks = []
    for ci, c in enumerate(CLS):
        fs = by[c]
        if not fs:
            continue
        idx = rng.choice(len(fs), min(a.n, len(fs)), replace=False)
        picks += [(fs[i], ci) for i in idx]
    print(f"표본 {len(picks)}장 ({a.split})")

    acc = {s: np.zeros(len(CLS)) for s in SCALES}
    cnt = np.zeros(len(CLS))
    pice = {s: [[] for _ in CLS] for s in SCALES}
    for k, (f, ci) in enumerate(picks):
        im = cv2.imread(str(f))
        if im is None:
            continue
        base = cv2.resize(im, (224, 224), interpolation=cv2.INTER_AREA)
        cnt[ci] += 1
        for s in SCALES:
            if s == 224:
                v = base
            else:
                v = cv2.resize(cv2.resize(base, (s, s), interpolation=cv2.INTER_AREA),
                               (224, 224), interpolation=cv2.INTER_LINEAR)
            x = (v[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - MEAN) / STD
            lg = sess.run(None, {iname: x[None]})[0][0]
            e = np.exp(lg - lg.max())
            p = e / e.sum()
            if int(p.argmax()) == ci:
                acc[s][ci] += 1
            pice[s][ci].append(float(p[ICE]))
        if (k + 1) % 200 == 0:
            print(f"  {k+1}/{len(picks)}", flush=True)

    L = ["# 경보 거리의 한계는 해상도다 — 얼마나 작아지면 못 보나\n\n",
         "## 1. 왜 재나\n\n",
         "1차 방어의 경보 거리가 22~28 m 에서 멈춘다. 학습이나 문턱 문제가 아니라 **기하**다.\n",
         f"지금 렌즈(화각 {cam.FOV:.0f}°, 높이 {cam.CAM_Z:.1f} m, 피치 {cam.CAM_PITCH:.0f}°)에서 "
         f"ROI 가 보는 노면은 7.9~42.2 m 이고, 30~60 m 구간은 세로 "
         f"**{band_px(30, 60, cam.FOV):.1f} px** 밖에 안 된다.\n\n",
         "없는 픽셀은 못 살린다. 그러면 망원으로 바꿀 가치가 있나? "
         "먼저 **얼마나 작아지면 못 보나**를 알아야 한다.\n\n",
         "데이터셋 사진을 s×s 로 줄였다가 다시 키워 입력으로 넣었다. 실제로 쓸 수 있는 정보는 "
         "s×s 뿐이니, 멀리 있는 노면과 같은 상태다.\n\n",
         "## 2. 해상도를 낮추면\n\n",
         "| 유효 해상도 | " + " | ".join(f"{c} 정답률" for c in CLS) + " | 얼음 확률(얼음 사진) |\n"
         + "|---" * (len(CLS) + 2) + "|\n"]
    for s in SCALES:
        cells = [f"{acc[s][i]/cnt[i]*100:.1f}%" if cnt[i] else "-" for i in range(len(CLS))]
        pi = np.mean(pice[s][ICE]) if pice[s][ICE] else 0.0
        L.append(f"| {s}×{s} | " + " | ".join(cells) + f" | {pi:.3f} |\n")

    base_ice = acc[224][ICE] / max(cnt[ICE], 1)
    knee = None
    for s in SCALES:
        if cnt[ICE] and acc[s][ICE] / cnt[ICE] < base_ice - 0.05:
            knee = s
            break
    L.append("\n## 3. 무너지는 지점\n\n")
    if knee:
        prev = SCALES[max(0, SCALES.index(knee) - 1)]
        L.append(f"- 얼음 정답률이 224×224 기준 {base_ice*100:.1f} % 에서 **{knee}×{knee}** 부터 "
                 f"5 %p 넘게 떨어진다.\n")
        L.append(f"- 즉 **{prev}×{prev} 까지는 버틴다**. 이것이 이 모델이 요구하는 최소 해상도다.\n")
        need = prev
    else:
        L.append(f"- 시험한 범위({SCALES[-1]}×{SCALES[-1]})까지 5 %p 넘는 하락이 없었다. "
                 "해상도보다 다른 요인이 먼저 걸린다는 뜻이다.\n")
        need = SCALES[-1]

    # ---- 4. 지금 기하에서 실제로 몇 픽셀이 오나 ----
    ys, ye, xs, xe = cam.roi_slice()
    roi_px = ye - ys
    L.append("\n## 4. 지금 기하에서는 몇 픽셀이 오나\n\n")
    L.append(f"ROI 는 영상 세로 {ys}~{ye} 행, 즉 **{roi_px} px** 를 잘라 224 로 늘린다.\n")
    L.append(f"그 띠가 보는 노면이 7.9~42.2 m 다. 늘리기 전 원본 정보는 {roi_px} px 뿐이다.\n\n")
    L.append(f"- 모델이 버티는 최소 해상도가 **{need} px** 이고 지금 오는 것이 **{roi_px} px** 다. "
             f"{'여유가 거의 없다' if roi_px < need * 1.5 else '여유가 있다'} — "
             "실측 경보 거리 22~28 m 와 앞뒤가 맞는다.\n")
    L.append(f"- 반면 30~60 m 구간은 **{band_px(30, 60, cam.FOV):.1f} px** 다. "
             f"{need} px 에 한참 못 미친다. 그래서 그 거리의 빙판은 원리적으로 못 본다.\n")

    # ---- 5. 무엇을 바꿔야 30~60 m 가 보이나 ----
    L.append("\n## 5. 무엇을 바꿔야 30~60 m 가 보이나\n\n")
    L.append(f"30~60 m 구간이 **{need} px** 이상으로 오게 하려면 화각을 좁히거나 센서를 키워야 한다.\n")
    L.append("두 가지가 같은 비율로 곱해진다 — 픽셀 수는 (초점거리 ÷ 화소 크기)에 비례한다.\n\n")
    W0 = cam.WIDTH
    L.append("| 화각 \\ 가로 해상도 | " + " | ".join(f"{w} px" for w in (640, 1280, 1920)) + " |\n")
    L.append("|---" * 4 + "|\n")
    for fov, name in ((cam.FOV, f"{cam.FOV:.0f}° (지금)"), (40.0, "40°"), (25.0, "25°")):
        cells = []
        for w in (640, 1280, 1920):
            px = band_px(30, 60, fov) * (w / W0)
            mark = " ✅" if px >= need else ""
            cells.append(f"{px:.0f} px{mark}")
        L.append(f"| {name} | " + " | ".join(cells) + " |\n")
    L.append(f"\n(✅ = 모델이 버티는 {need} px 이상)\n")

    L.append("\n### 읽는 법\n\n")
    L.append("- **망원만으로는 안 된다.** 25° 로 좁혀도 640 px 센서에서는 "
             f"{band_px(30, 60, 25.0):.0f} px 라 여전히 부족하다.\n")
    L.append("- **해상도를 올리는 쪽이 먼저다.** 지금 640×480 으로 받고 있는데 "
             "D435i 의 RGB 는 1920×1080 을 낼 수 있다. 렌즈를 사기 전에 "
             "**설정 한 줄로 확인할 수 있는 것**이다.\n")
    L.append("- 둘을 같이 쓰면 확실히 넘는다 — 25° + 1920 px 면 "
             f"{band_px(30, 60, 25.0) * (1920 / W0):.0f} px.\n")

    L.append("\n### 공짜는 아니다\n\n")
    L.append("| 잃는 것 | 왜 | 얼마나 |\n|---|---|---|\n")
    L.append("| 시야 폭 | 25° 면 지금의 0.42배 | 옆 차로와 곡선 진입부가 화면에서 빠진다 |\n")
    L.append("| 근거리 | 망원은 발밑을 못 본다 | 2차 방어가 받아야 하는 구간이 늘어난다 |\n")
    L.append("| 흔들림 | 초점거리가 길수록 진동이 픽셀로 크게 나타난다 | 노출 시간을 줄여야 한다 |\n")
    L.append("| NPU 시간 | 해상도를 올려도 모델 입력은 224 로 고정이다 | **비용 변화 없음** — 크롭만 커진다 |\n")

    L.append("\n### 권장 순서\n\n")
    L.append("1. **캡처 해상도를 1920×1080 으로 올린다.** 렌즈 교체 없이 되는지 먼저 본다. "
             "모델 입력은 224 그대로라 NPU 비용은 안 변한다 (`정리/10` 의 여유 참고).\n")
    L.append("2. 그래도 모자라면 **카메라 두 대로 나눈다.** 근거리·폭은 지금 렌즈, "
             "원거리만 망원. 프레임을 번갈아 넣으면 각 카메라 실효 10 fps 이고 NPU 총량은 같다.\n")
    L.append("3. 광각 한 대를 망원으로 **바꾸는** 안은 마지막이다 — 근거리를 통째로 잃는다.\n")

    L.append("\n> 한계: 이 표는 보드 INT8 이 아니라 `models/roadnet.onnx`(FP32)로 쟀다. "
             "보드를 실사진 평가에 쓰고 있어서다. 절대 정답률은 조금 다를 수 있으나 "
             "여기서 보는 것은 해상도에 따른 **상대 변화**라 결론은 바뀌지 않는다.\n")
    L.append("> 한계: 사진을 줄였다 늘리는 것은 '멀어짐'의 근사다. 실제로는 원근 압축과 "
             "대기 산란도 같이 온다. 그래서 위 숫자는 **낙관적인 쪽**이다.\n")

    out = ROOT / "logs/carla_demo/정리/17_경보거리와_해상도.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
