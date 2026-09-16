"""OpenCV 노면 반사도(specular) 분석과 차선 가시성 평가.

- 반사도: 전방 도로 ROI의 밝기 히스토그램에서 고휘도 피크(전반사)를 찾는다.
  아스팔트는 난반사로 분포가 넓고 낮으며, 얼음/물막은 광원이 그대로 비쳐 좁고 높은 피크가 생긴다.
- 차선 가시성: Canny + HoughLinesP로 차선 각도 범위(20~70°)의 선분을 세어 점수화.
두 값 모두 0~1로 정규화해 fusion.RiskFuser에 넣는다.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import cv2

@dataclass
class ReflectanceResult:
    spec: float             # 0~1 전반사 점수
    bright_frac: float      # 고휘도 픽셀 비율
    peak_ratio: float       # 히스토그램 상위 피크 첨도 (피크/평균)
    lane: float             # 0~1 차선 가시성
    n_lane_lines: int

def road_roi(img: np.ndarray, top: float = 0.55, bottom: float = 0.95) -> np.ndarray:
    h = img.shape[0]
    return img[int(h * top):int(h * bottom), :]

def specular_score(gray_roi: np.ndarray, bright_thr: int = 215) -> tuple[float, float, float]:
    hist = cv2.calcHist([gray_roi], [0], None, [64], [0, 256]).ravel()
    hist /= max(hist.sum(), 1.0)
    bright_frac = float((gray_roi >= bright_thr).mean())
    hi = hist[48:]                                   # 상위 1/4 밝기 구간 (192~255)
    peak_ratio = float(hi.max() / max(hist.mean(), 1e-6))
    # 상위 구간에 뾰족한 피크가 있고 그 비율이 어느 정도면 전반사로 본다
    s = 0.6 * min(1.0, bright_frac / 0.12) + 0.4 * min(1.0, max(0.0, (peak_ratio - 1.5) / 4.0))
    return min(1.0, s), bright_frac, peak_ratio

def lane_visibility(gray_roi: np.ndarray) -> tuple[float, int]:
    edges = cv2.Canny(gray_roi, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40, minLineLength=max(20, gray_roi.shape[1] // 12), maxLineGap=15)
    if lines is None:
        return 0.0, 0
    n = 0
    total_len = 0.0
    for x1, y1, x2, y2 in lines[:, 0]:
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if 20 <= ang <= 70:                          # 원근으로 기운 차선 각도
            n += 1
            total_len += float(np.hypot(x2 - x1, y2 - y1))
    score = min(1.0, 0.5 * min(1.0, n / 6.0) + 0.5 * min(1.0, total_len / (gray_roi.shape[0] * 2.0)))
    return score, n

def analyze(img_bgr: np.ndarray) -> ReflectanceResult:
    roi = road_roi(img_bgr)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    spec, bf, pr = specular_score(gray)
    lane, n = lane_visibility(gray)
    return ReflectanceResult(spec=spec, bright_frac=bf, peak_ratio=pr, lane=lane, n_lane_lines=n)
