"""CARLA 프레임에 물리적으로 그럴듯한 블랙아이스 외형을 합성한다.

왜 필요한가
  CARLA의 friction trigger는 마찰계수만 바꾸고 노면 렌더링은 그대로다. 따라서 패치 위/밖
  프레임이 화면상 구별되지 않아, 그대로 학습하면 모델이 배울 신호가 없다.
  수집기(carla_collect.py)와 데모(carla_demo.py)가 **같은 합성기**를 써야 한다. 학습한
  외형과 데모 화면의 외형이 다르면 미세조정이 데모에서 동작하지 않는다.

물리 모델 (v2)
  블랙아이스는 아스팔트 위에 덮인 얇고 **매끄러운 투명 유전체 막**이다. 따라서
    1) 정반사(프레넬)  : 입사각이 스칠수록 반사율이 급증한다. 카메라 기하상 화면 위쪽(원거리)
                         이 그레이징이므로 원거리는 하늘을 거울처럼 비추고 근거리는 어둡다.
                         ROI 안에서 R이 0.82 → 0.34로 변하는 이 **구배**가 주 단서다.
    2) 거울 반사       : 표면이 매끄러우므로 반사상이 **선명**하다. 수평선을 대칭축으로 장면을
                         뒤집어 반사상을 만든다(평면 거울 근사). 젖은 노면은 표면이 거칠어
                         반사상이 흐리다 — roughness_px가 얼음/젖음을 가르는 물리량이다.
    3) 어두워짐        : 막을 투과해 되돌아오는 확산광이 (1-R) 배로 줄고 흡수도 있다 → "블랙"
    4) 두께 불균일     : 저주파 노이즈로 반사율을 변조해 얼룩을 만든다
    5) 야간 글레어     : 헤드라이트가 매끄러운 면에서 되돌아와 넓은 밝은 로브를 만든다

v1에서 무엇이 잘못됐나 (반드시 유지할 교훈)
  v1은 "질감 소실"을 전체 프레임 GaussianBlur 혼합으로 구현했다. 그 결과 black_ice의
  선명도(라플라시안 분산) 중앙값이 12.8, normal 27.0 / wet 22.5 — 블러가 **유일한 클래스
  단서**가 됐다. 모델은 얼음이 아니라 저주파를 배우고, 실차에서는 모션블러·안개·렌즈 오염이
  같은 저주파를 만들어 오탐이 쏟아진다. v2는 블러를 클래스 단서로 쓰지 않는다. 질감 감소는
  프레넬 반사율 R에 묶여 공간적으로만 일어난다(원거리에서 반사광이 확산광을 덮으므로 자연히
  질감이 사라지고, 근거리에는 아스팔트 질감이 남는다).

한계 (문서화 필수)
  합성된 외형은 우리가 정의한 것이므로, 이 데이터로 학습한 모델의 성능은 "CARLA 안에서의
  검증"이지 실차 성능 근거가 아니다. 실차 근거는 RSCD 실사진 학습 결과가 제공한다.
"""
from __future__ import annotations
import numpy as np
import cv2

from icepredict.sim.camera import horizon_y as _horizon_y, ROI_TOP, ROI_BOTTOM

# CARLA 0.9.14+ semantic tag: Roads=1, RoadLine=24, Sky=11 (구버전 7/6/13 에서 변경됨)
ROAD_TAG, ROADLINE_TAG, SKY_TAG = 1, 24, 11

# 얼음(n=1.31)의 수직 입사 반사율. R0 = ((n-1)/(n+1))^2
ICE_R0 = 0.02

def road_mask(semantic: np.ndarray) -> np.ndarray:
    """semantic: CARLA raw semseg의 R채널(태그). 도로+차선 픽셀."""
    return np.isin(semantic, (ROAD_TAG, ROADLINE_TAG))

def sky_color(bgr: np.ndarray, semantic: np.ndarray) -> np.ndarray:
    m = semantic == SKY_TAG
    if m.sum() > 200:
        return bgr[m].reshape(-1, 3).mean(0)
    return bgr[: bgr.shape[0] // 4].reshape(-1, 3).mean(0)   # 상단 1/4로 대체

def fresnel_map(h: int, w: int, *, horizon: float | None = None, focal: float | None = None,
                r0: float = ICE_R0) -> np.ndarray:
    """행마다의 프레넬 반사율 R(y). 노면을 수평면으로 보고 Schlick 근사를 쓴다.

    수평선에서 y픽셀 아래인 노면의 내려본각 θd = atan((y-h0)/f), 면 법선 기준 입사각의
    코사인은 sin(θd)다. 수평선 근처(원거리)는 θd→0이라 cosθi→0, 즉 R→1로 치솟는다.
    """
    from icepredict.sim.camera import focal_px
    h0 = _horizon_y(height=h, width=w) if horizon is None else horizon
    f = focal_px(w) if focal is None else focal
    y = np.arange(h, dtype=np.float32)
    theta_d = np.arctan(np.maximum(y - h0, 0.0) / f)      # 수평선 위는 0으로 클램프
    cos_i = np.sin(theta_d)
    R = r0 + (1.0 - r0) * (1.0 - cos_i) ** 5
    R[y < h0] = 0.0                                        # 수평선 위는 노면이 아니다
    return np.repeat(R[:, None], w, axis=1)

def planar_reflection(bgr: np.ndarray, *, horizon: float | None = None,
                      compress: float = 1.0, roughness_px: float = 0.0) -> np.ndarray:
    """수평선을 대칭축으로 장면을 뒤집어 노면 반사상을 만든다(평면 거울 근사).

    노면 행 y가 비추는 것은 수평선 기준 반대편 행 h0-(y-h0)이다. roughness_px로 흐리게
    하면 젖은 노면(거친 표면), 0에 가까우면 얼음(매끄러운 표면)이 된다.
    """
    h, w = bgr.shape[:2]
    h0 = _horizon_y(height=h, width=w) if horizon is None else horizon
    rows = np.arange(h, dtype=np.float32)
    src_y = np.clip(h0 - (rows - h0) * compress, 0, h - 1)
    map_y = np.repeat(src_y[:, None], w, axis=1)
    map_x = np.repeat(np.arange(w, dtype=np.float32)[None, :], h, axis=0)
    refl = cv2.remap(bgr, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if roughness_px > 0.3:
        refl = cv2.GaussianBlur(refl, (0, 0), roughness_px)
    return refl

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

def _lowfreq(rng: np.random.Generator, h: int, w: int, sigma: float = 4.0, div: int = 8) -> np.ndarray:
    n = rng.normal(0.0, 1.0, (max(2, h // div), max(2, w // div))).astype(np.float32)
    n = cv2.GaussianBlur(cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), sigma)
    return n / (np.abs(n).max() + 1e-6)

def composite_ice(bgr: np.ndarray, semantic: np.ndarray, ice_mask: np.ndarray, *,
                  specular: float = 1.0, darkening: float = 0.20, roughness_px: float = 0.6,
                  thickness: float = 0.35, glare: float = 0.0, compress: float = 1.0, luma: float = 0.95,
                  feather_px: float = 9.0, edge_noise: float = 0.45,
                  rng: np.random.Generator | None = None) -> np.ndarray:
    """ice_mask(도로 영역과 교집합) 픽셀에 블랙아이스 외형을 합성. 경계는 부드럽게 페더링.

    specular   프레넬 반사율 전체 배율 (1.0 = 이론값)
    darkening  막 투과 흡수로 인한 확산광 추가 감쇠
    roughness_px 반사상 흐림. 얼음은 작게(매끄러움), 젖음은 크게(거침)
    thickness  막 두께 불균일 → 반사율 얼룩 세기
    glare      야간 헤드라이트 정반사 로브 세기
    luma       얼음 영역 평균 휘도를 원본의 몇 배로 맞출지 (0 = 보존 안 함)
    """
    rng = rng or np.random.default_rng()
    if ice_mask.dtype == bool:
        alpha = soft_mask(ice_mask, semantic, feather_px=feather_px, edge_noise=edge_noise, rng=rng)
    else:
        alpha = np.clip(ice_mask.astype(np.float32), 0, 1) * road_mask(semantic)
    if alpha.sum() < 50:
        return bgr
    src = bgr.astype(np.float32)
    h, w = bgr.shape[:2]

    # --- 1) 프레넬 반사율. 두께 불균일로 변조 ---
    R = fresnel_map(h, w) * specular
    if thickness > 0:
        R *= np.clip(1.0 + thickness * 0.5 * _lowfreq(rng, h, w, sigma=5.0, div=10), 0.3, 1.7)
    R = np.clip(R, 0.0, 0.95)[:, :, None]

    # --- 2) 거울 반사상 (매끄러운 면이므로 선명하게) ---
    refl = planar_reflection(bgr, compress=compress, roughness_px=roughness_px).astype(np.float32)
    # 반사상 상단이 화면 밖으로 나가 복제된 영역은 하늘색으로 메운다
    sky = sky_color(bgr, semantic).astype(np.float32)
    refl = refl * 0.85 + sky[None, None, :] * 0.15

    # --- 3) 확산광: 막 투과 흡수만큼 어둡게. 질감은 그대로 둔다 ---
    #     (질감 감소는 R이 큰 원거리에서 반사광이 확산광을 덮으며 자연히 일어난다.
    #      v1처럼 전역 블러를 섞으면 선명도가 클래스 단서가 되므로 절대 하지 않는다.)
    diffuse = src * (1.0 - darkening)

    out = diffuse * (1.0 - R) + refl * R

    # --- 4) 야간 헤드라이트 글레어: 화면 중앙 하단에 넓은 밝은 로브 ---
    if glare > 0:
        yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
        # 로브 중심은 반드시 ROI(전방 8~30m) 안이어야 한다. v2 초기값 0.62는 ROI(0.30~0.49)
        # 밖이라 모델 입력에 글레어가 전혀 들어가지 않았다.
        yc = (ROI_TOP + ROI_BOTTOM) / 2.0
        lobe = np.exp(-(xx ** 2) / 0.35) * np.exp(-((yy - yc) ** 2) / 0.02)
        out = out + (glare * 140.0 * lobe * R[:, :, 0])[:, :, None]

    # --- 5) 평균 휘도 보존: 밝기를 클래스 단서로 만들지 않는다 ---
    # 반사원이 하늘이냐 건물이냐에 따라 합성 결과의 평균 밝기가 크게 흔들린다(도심 Town03에서
    # 얼음 49 vs 엔진 젖음 145). 그 전역 오프셋을 그대로 두면 모델이 '어두움 = 얼음'을 배운다.
    # 여기서는 얼음 영역의 평균 휘도를 원본 대비 luma 배로 맞추고, 공간 구조(프레넬 구배·
    # 선명한 반사상·두께 얼룩)만 클래스 단서로 남긴다.
    if luma > 0:
        wsum = float(alpha.sum()) + 1e-6
        m0 = float((src.mean(2) * alpha).sum()) / wsum
        m1 = float((out.mean(2) * alpha).sum()) / wsum
        if m1 > 1.0:
            out = out * np.clip(m0 * luma / m1, 0.35, 3.0)

    a3 = alpha[:, :, None]
    return np.clip(src * (1 - a3) + out * a3, 0, 255).astype(np.uint8)

def random_ice_params(rng: np.random.Generator, night: bool = False) -> dict:
    """도메인 랜덤화: 얼음 두께·조명에 따른 외형 변화.

    roughness_px를 0.2~1.6으로 좁게 유지하는 것이 핵심이다. 이보다 크면 반사상이 흐려져
    젖은 노면과 구별되지 않고, 0이면 완벽한 거울이 되어 비현실적이다.
    """
    if night:
        # 야간: 하늘이 어두워 거울 반사만으로는 거의 안 보인다. 헤드라이트 글레어가 주 단서.
        return dict(specular=float(rng.uniform(0.75, 1.0)), darkening=float(rng.uniform(0.05, 0.18)),
                    roughness_px=float(rng.uniform(0.2, 1.4)), thickness=float(rng.uniform(0.2, 0.6)),
                    glare=float(rng.uniform(0.35, 0.85)), compress=float(rng.uniform(0.8, 1.1)),
                    luma=float(rng.uniform(0.95, 1.35)),
                    feather_px=float(rng.uniform(6, 16)), edge_noise=float(rng.uniform(0.3, 0.7)))
    return dict(specular=float(rng.uniform(0.7, 1.0)), darkening=float(rng.uniform(0.10, 0.30)),
                roughness_px=float(rng.uniform(0.2, 1.6)), thickness=float(rng.uniform(0.15, 0.5)),
                glare=0.0, compress=float(rng.uniform(0.8, 1.1)),
                luma=float(rng.uniform(0.80, 1.05)),
                feather_px=float(rng.uniform(6, 16)), edge_noise=float(rng.uniform(0.3, 0.7)))

def composite_wet(bgr: np.ndarray, semantic: np.ndarray, mask: np.ndarray,
                  rng: np.random.Generator | None = None) -> np.ndarray:
    """(보존용, 기본 경로에서는 미사용) 젖은 노면 합성.

    수집기는 CARLA WeatherParameters의 wetness/precipitation_deposits가 엔진에서 실제로
    렌더링하는 젖은 노면을 그대로 'wet' 클래스로 쓴다. 합성으로 만든 젖음은 우리가 정의한
    아티팩트라서 모델이 그것을 학습할 위험이 크다. 굳이 쓸 때는 표면이 거칠다는 점을
    roughness_px로 표현한다 — 얼음과 젖음을 가르는 물리량이 그것이다."""
    rng = rng or np.random.default_rng()
    return composite_ice(bgr, semantic, mask, specular=float(rng.uniform(0.5, 0.8)),
                         darkening=float(rng.uniform(0.05, 0.20)),
                         roughness_px=float(rng.uniform(4.0, 10.0)),
                         thickness=float(rng.uniform(0.5, 1.0)), glare=0.0,
                         feather_px=float(rng.uniform(6, 14)),
                         edge_noise=float(rng.uniform(0.3, 0.7)), rng=rng)
