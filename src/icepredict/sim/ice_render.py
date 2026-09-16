"""CARLA 프레임에 물리적으로 그럴듯한 블랙아이스 외형을 합성한다.

왜 필요한가
  CARLA의 friction trigger는 마찰계수만 바꾸고 노면 렌더링은 그대로다. 따라서 패치 위/밖
  프레임이 화면상 구별되지 않아, 그대로 학습하면 모델이 배울 신호가 없다.

무엇을 하는가
  블랙아이스의 광학적 특징을 노면 픽셀에 재현한다.
    1) 전반사(specular)  : 난반사하는 아스팔트와 달리 하늘/주변을 거울처럼 비춘다 → 하늘색이 노면에 섞임
    2) 어두워짐          : 얇은 얼음막 아래 아스팔트가 비쳐 검게 보인다 ("블랙"아이스)
    3) 질감 소실         : 아스팔트 표면 요철이 얼음에 덮여 국소 대비가 낮아진다
    4) 차선 흐림         : 얼음막 위로 차선 도색이 번져 보인다
  세기는 0~1 파라미터로 무작위화해 도메인 랜덤화 효과를 준다.

한계 (문서화 필수)
  합성된 외형은 우리가 정의한 것이므로, 이 데이터로 학습한 모델의 성능은 "CARLA 안에서의
  검증"이지 실차 성능 근거가 아니다. 실차 근거는 RSCD 실사진 학습 결과가 제공한다.
"""
from __future__ import annotations
import numpy as np
import cv2

# CARLA 0.9.14+ semantic tag: Roads=1, RoadLine=24, Sky=11 (구버전 7/6/13 에서 변경됨)
ROAD_TAG, ROADLINE_TAG, SKY_TAG = 1, 24, 11

def road_mask(semantic: np.ndarray) -> np.ndarray:
    """semantic: CARLA raw semseg의 R채널(태그). 도로+차선 픽셀."""
    return np.isin(semantic, (ROAD_TAG, ROADLINE_TAG))

def sky_color(bgr: np.ndarray, semantic: np.ndarray) -> np.ndarray:
    m = semantic == SKY_TAG
    if m.sum() > 200:
        return bgr[m].reshape(-1, 3).mean(0)
    return bgr[: bgr.shape[0] // 4].reshape(-1, 3).mean(0)   # 상단 1/4로 대체

def composite_ice(bgr: np.ndarray, semantic: np.ndarray, ice_mask: np.ndarray, *,
                  specular: float = 0.45, darkening: float = 0.35, smoothing: float = 0.7,
                  streak: float = 0.35, rng: np.random.Generator | None = None) -> np.ndarray:
    """ice_mask(도로 영역과 교집합) 픽셀에 블랙아이스 외형을 합성."""
    rng = rng or np.random.default_rng()
    m = ice_mask & road_mask(semantic)
    if m.sum() < 50:
        return bgr
    out = bgr.astype(np.float32).copy()
    sky = sky_color(bgr, semantic).astype(np.float32)

    # 3) 질감 소실: 강하게 블러한 버전과 섞어 국소 대비를 낮춘다
    blur = cv2.GaussianBlur(out, (0, 0), 3.0)
    out[m] = out[m] * (1 - smoothing) + blur[m] * smoothing

    # 2) 어두워짐
    out[m] *= (1.0 - darkening)

    # 1) 전반사: 하늘색을 섞되, 시점에서 멀수록(화면 위쪽) 강하게 (그레이징 앵글 = 반사율↑)
    h = bgr.shape[0]
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]      # 0=위, 1=아래
    grazing = (1.0 - yy) ** 1.5                                    # 위쪽일수록 큼
    gz = np.repeat(grazing, bgr.shape[1], axis=1)
    w = (specular * gz)[m][:, None]
    out[m] = out[m] * (1 - w) + sky[None, :] * w

    # 4) 얼룩/줄무늬: 얼음막 두께 불균일
    if streak > 0:
        noise = rng.normal(0.0, 1.0, (h // 8, bgr.shape[1] // 8)).astype(np.float32)
        noise = cv2.resize(noise, (bgr.shape[1], h), interpolation=cv2.INTER_CUBIC)
        noise = cv2.GaussianBlur(noise, (0, 0), 4.0)
        out[m] *= (1.0 + streak * 0.35 * noise[m])[:, None]

    return np.clip(out, 0, 255).astype(np.uint8)

def random_ice_params(rng: np.random.Generator, night: bool = False) -> dict:
    """도메인 랜덤화: 얼음 두께·조명에 따른 외형 변화."""
    if night:
        # 야간: 헤드라이트 전반사가 강해 국소적으로 매우 밝아짐
        return dict(specular=float(rng.uniform(0.15, 0.40)), darkening=float(rng.uniform(0.10, 0.30)),
                    smoothing=float(rng.uniform(0.5, 0.85)), streak=float(rng.uniform(0.2, 0.6)))
    return dict(specular=float(rng.uniform(0.30, 0.65)), darkening=float(rng.uniform(0.20, 0.50)),
                smoothing=float(rng.uniform(0.5, 0.9)), streak=float(rng.uniform(0.1, 0.5)))

def composite_wet(bgr: np.ndarray, semantic: np.ndarray, mask: np.ndarray,
                  rng: np.random.Generator | None = None) -> np.ndarray:
    """젖은 노면: 얼음보다 반사 약하고 어두워짐은 비슷, 질감은 어느 정도 유지."""
    rng = rng or np.random.default_rng()
    return composite_ice(bgr, semantic, mask, specular=float(rng.uniform(0.10, 0.25)),
                         darkening=float(rng.uniform(0.15, 0.35)), smoothing=float(rng.uniform(0.15, 0.4)),
                         streak=float(rng.uniform(0.3, 0.7)), rng=rng)
