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

def soft_mask(ice_mask: np.ndarray, semantic: np.ndarray, *, feather_px: float = 9.0,
              edge_noise: float = 0.45, rng: np.random.Generator | None = None) -> np.ndarray:
    """기하학적 폴리곤 경계를 자연스러운 얼음 가장자리로 바꾼 0~1 알파 마스크.

    직선 경계를 그대로 두면 모델이 '노면 외형'이 아니라 '대각선 모서리'를 학습한다.
    저주파 노이즈로 경계를 흔든 뒤 블러로 페더링한다. 도로 밖은 항상 0.
    """
    rng = rng or np.random.default_rng()
    h, w = ice_mask.shape
    a = ice_mask.astype(np.float32)
    if edge_noise > 0:
        n = rng.normal(0.0, 1.0, (max(2, h // 24), max(2, w // 24))).astype(np.float32)
        n = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
        n = cv2.GaussianBlur(n, (0, 0), 6.0)
        n /= (np.abs(n).max() + 1e-6)
        a = np.clip(a + edge_noise * n * (cv2.GaussianBlur(a, (0, 0), 6.0) * (1 - a) * 4 + a * (1 - a) * 4), 0, 1)
        a = (a > 0.5).astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), feather_px)
    a *= road_mask(semantic).astype(np.float32)
    return np.clip(a, 0, 1)

def composite_ice(bgr: np.ndarray, semantic: np.ndarray, ice_mask: np.ndarray, *,
                  specular: float = 0.45, darkening: float = 0.35, smoothing: float = 0.7,
                  streak: float = 0.35, feather_px: float = 9.0, edge_noise: float = 0.45,
                  rng: np.random.Generator | None = None) -> np.ndarray:
    """ice_mask(도로 영역과 교집합) 픽셀에 블랙아이스 외형을 합성. 경계는 부드럽게 페더링."""
    rng = rng or np.random.default_rng()
    if ice_mask.dtype == bool:
        alpha = soft_mask(ice_mask, semantic, feather_px=feather_px, edge_noise=edge_noise, rng=rng)
    else:
        alpha = np.clip(ice_mask.astype(np.float32), 0, 1) * road_mask(semantic)
    if alpha.sum() < 50:
        return bgr
    src = bgr.astype(np.float32)
    out = src.copy()
    sky = sky_color(bgr, semantic).astype(np.float32)
    h, w = bgr.shape[:2]

    # 3) 질감 소실: 강하게 블러한 버전과 섞어 국소 대비를 낮춘다
    blur = cv2.GaussianBlur(out, (0, 0), 3.0)
    out = out * (1 - smoothing) + blur * smoothing

    # 2) 어두워짐
    out *= (1.0 - darkening)

    # 1) 전반사: 하늘색을 섞되, 그레이징 앵글(화면 위쪽)일수록 반사율이 높다
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    gz = np.repeat(((1.0 - yy) ** 1.5), w, axis=1)[:, :, None]
    ws = specular * gz
    out = out * (1 - ws) + sky[None, None, :] * ws

    # 4) 얼룩/줄무늬: 얼음막 두께 불균일
    if streak > 0:
        n = rng.normal(0.0, 1.0, (max(2, h // 8), max(2, w // 8))).astype(np.float32)
        n = cv2.GaussianBlur(cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 4.0)
        out *= (1.0 + streak * 0.08 * n)[:, :, None]   # 약하게: 노이즈가 클래스 단서가 되면 안 됨

    a3 = alpha[:, :, None]
    return np.clip(src * (1 - a3) + out * a3, 0, 255).astype(np.uint8)

def random_ice_params(rng: np.random.Generator, night: bool = False) -> dict:
    """도메인 랜덤화: 얼음 두께·조명에 따른 외형 변화."""
    if night:
        # 야간: 헤드라이트 전반사가 강해 국소적으로 밝아지고, 얼음 자체는 덜 어둡게 보인다
        return dict(specular=float(rng.uniform(0.10, 0.28)), darkening=float(rng.uniform(0.08, 0.22)),
                    smoothing=float(rng.uniform(0.45, 0.80)), streak=float(rng.uniform(0.2, 0.6)),
                    feather_px=float(rng.uniform(6, 16)), edge_noise=float(rng.uniform(0.3, 0.7)))
    return dict(specular=float(rng.uniform(0.22, 0.45)), darkening=float(rng.uniform(0.18, 0.38)),
                smoothing=float(rng.uniform(0.45, 0.80)), streak=float(rng.uniform(0.1, 0.5)),
                feather_px=float(rng.uniform(6, 16)), edge_noise=float(rng.uniform(0.3, 0.7)))

def composite_wet(bgr: np.ndarray, semantic: np.ndarray, mask: np.ndarray,
                  rng: np.random.Generator | None = None) -> np.ndarray:
    """(보존용, 기본 경로에서는 미사용) 젖은 노면 합성.

    수집기는 CARLA WeatherParameters의 wetness/precipitation_deposits가 엔진에서 실제로
    렌더링하는 젖은 노면을 그대로 'wet' 클래스로 쓴다. 합성으로 만든 젖음은 우리가 정의한
    아티팩트라서 모델이 그것을 학습할 위험이 크다."""
    rng = rng or np.random.default_rng()
    return composite_ice(bgr, semantic, mask, specular=float(rng.uniform(0.10, 0.25)),
                         darkening=float(rng.uniform(0.15, 0.35)), smoothing=float(rng.uniform(0.15, 0.4)),
                         streak=float(rng.uniform(0.3, 0.7)), feather_px=float(rng.uniform(6, 14)),
                         edge_noise=float(rng.uniform(0.3, 0.7)), rng=rng)
