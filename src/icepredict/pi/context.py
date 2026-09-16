"""기상·위치 컨텍스트로 사전 위험도(prior)와 N6 임계값·가중치를 결정한다.

결빙 4유형(채연 교수 연구): ①기온 하강+습도 증가 ②약한 비+급격한 기온 하강
③어는 비 ④쌓인 눈 해동·재결빙. 다리·터널 출구는 가중.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from icepredict.common.protocol import Weights, ContextMsg

def dew_point_c(temp_c: float, rh: float) -> float:
    """Magnus 공식. rh는 % (0~100)."""
    rh = max(1.0, min(100.0, rh))
    a, b = 17.62, 243.12
    g = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * g / (a - g)

@dataclass
class WeatherObs:
    temp_c: float
    humidity: float           # %
    precip_mm: float = 0.0    # 최근 1시간 강수량
    wind_mps: float = 0.0
    temp_trend_c_per_h: float = 0.0   # 최근 기온 변화율 (음수 = 하강)
    snow_on_ground: bool = False

@dataclass
class LocationCtx:
    lat: float = 37.56
    lon: float = 126.97
    feature: str = "none"     # none / bridge / tunnel_exit / mountain / shade
    hour: int = 12            # 로컬 시각 (0~23)

FEATURE_BONUS = {"none": 0.0, "bridge": 0.20, "tunnel_exit": 0.18, "mountain": 0.12, "shade": 0.10}

def freezing_prior(w: WeatherObs, loc: LocationCtx) -> tuple[float, str]:
    """0~1 사전 위험도와 근거 문자열."""
    reasons = []
    dp = dew_point_c(w.temp_c, w.humidity)
    # 기온 항: 1.5°C 이상이면 0, -3°C 이하면 1 (선형)
    t_score = min(1.0, max(0.0, (1.5 - w.temp_c) / 4.5))
    if t_score > 0:
        reasons.append(f"temp {w.temp_c:.1f}C")
    # 노점 근접: 노면 온도(≈기온-1)가 노점 이하면 결로 → 결빙
    road_t = w.temp_c - 1.0
    dp_score = 1.0 if (road_t <= dp + 1.0 and w.temp_c <= 1.0) else max(0.0, 1.0 - (road_t - dp) / 3.0) * 0.6
    if dp_score > 0.3:
        reasons.append(f"dew {dp:.1f}C")
    # 습도/강수: 유형 ①②③
    h_score = max(0.0, (w.humidity - 70.0) / 30.0)
    p_score = 0.0
    if w.precip_mm > 0 and w.temp_c <= 2.0:
        p_score = 0.8                    # 어는 비/눈
        reasons.append("precip@cold")
    if w.temp_trend_c_per_h <= -1.5 and w.humidity >= 70:
        p_score = max(p_score, 0.6)      # 급격한 기온 하강 + 습함
        reasons.append("rapid cooling")
    if w.snow_on_ground and w.temp_c <= 1.0:
        p_score = max(p_score, 0.7)      # 유형 ④ 재결빙
        reasons.append("snow refreeze")
    # 시간대: 새벽 3~9시 가중 (출근 시간대 사고 집중)
    hour_bonus = 0.15 if 3 <= loc.hour <= 9 else (0.08 if loc.hour >= 22 or loc.hour < 3 else 0.0)
    if hour_bonus:
        reasons.append(f"hour {loc.hour}")
    feat = FEATURE_BONUS.get(loc.feature, 0.0)
    if feat:
        reasons.append(loc.feature)

    base = 0.45 * t_score + 0.25 * dp_score + 0.15 * h_score + 0.15 * p_score
    prior = base + (1.0 - base) * (hour_bonus + feat)   # 위치·시간대는 남은 여유분을 채우는 식으로 가중
    # 기온이 6°C 이상이면 물리적으로 결빙 불가에 가깝다
    if w.temp_c >= 6.0:
        prior = min(prior, 0.05)
    return min(1.0, max(0.0, prior)), ", ".join(reasons)

def threshold_from_prior(prior: float, hi: float = 0.7, lo: float = 0.4) -> float:
    """prior 0 → hi(0.7), prior 1 → lo(0.4). 계획서: 위험도 0.92 → 임계값 0.4 부근."""
    return hi - (hi - lo) * prior

def weights_from_context(loc: LocationCtx, w: WeatherObs) -> Weights:
    night = loc.hour >= 19 or loc.hour < 6
    if night:
        # 야간: 분류기 정확도 하락, 헤드라이트 전반사 신호는 강해짐 → 반사도 가중
        return Weights(alpha=0.35, beta=0.45, gamma=0.20)
    if w.precip_mm > 0:
        # 강수 중: 젖은 노면도 반사 → 반사도 신뢰 하락, 분류·차선 가중
        return Weights(alpha=0.55, beta=0.15, gamma=0.30)
    return Weights(alpha=0.5, beta=0.3, gamma=0.2)

def build_context(w: WeatherObs, loc: LocationCtx) -> ContextMsg:
    prior, reason = freezing_prior(w, loc)
    return ContextMsg(
        risk_prior=round(prior, 3),
        threshold=round(threshold_from_prior(prior), 3),
        weights=weights_from_context(loc, w),
        weather={"temp_c": w.temp_c, "humidity": w.humidity, "dew_point_c": round(dew_point_c(w.temp_c, w.humidity), 2),
                 "precip_mm": w.precip_mm, "wind_mps": w.wind_mps},
        location={"lat": loc.lat, "lon": loc.lon, "feature": loc.feature, "hour": loc.hour},
        reason=reason,
    )
