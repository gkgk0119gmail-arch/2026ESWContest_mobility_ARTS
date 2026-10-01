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

# 실사진 25,140 장(STM32N6 인-더-루프, `logs/rscd_board_samples.jsonl`)으로 교정한 문턱 구간.
#
# 왜 바꿨나: 예전 값은 hi=0.7 / lo=0.4 였다. 결빙 가능성이 높을수록 문턱을 낮춘다는 설계 자체는
# 맞지만, 낮추는 **바닥**이 근거 없이 0.4 였다. 실측 결과 위험도 분포에는 절벽이 있다 —
#
#   문턱 0.560 → 얼음 97.3 %, 최악 오경보 60.3 %
#   문턱 0.570 → 얼음 97.2 %, 최악 오경보  6.8 %     ← 여기서 한 번에 떨어진다
#   문턱 0.600 → 얼음 96.8 %, 최악 오경보  1.9 %
#
# 즉 0.57 아래로 내려가면 얼음 탐지는 거의 안 늘면서 오경보만 폭증한다. 그런데 예전 map 은
# 결빙 prior 가 높을 때(데모 기본 0.863) 문턱을 0.441 로 — **절벽 아래로** 내리고 있었다.
# 그것이 대조군 주행에서 관측된 40 % 오경보의 직접 원인이다.
#
# 새 구간: lo 를 절벽 위(0.58)로 올리고 hi 는 0.75 로 둔다. 그러면
#   prior 0.000(결빙 불가)  → 0.750  최악 오경보 0.7 %
#   prior 0.863(데모 기본)  → 0.603  최악 오경보 1.9 %   ← 실측 권고 운영점과 일치
#   prior 0.956(영하 폭우)  → 0.588  최악 오경보 2.5 %
# 어느 경우에도 절벽 아래로 내려가지 않는다.
#
# 이 값은 `ctx` 메시지로 주행 중 보드에 내려가므로 **펌웨어를 다시 굽지 않아도 된다.**
THRESHOLD_HI = 0.75      # 결빙 가능성 0 — 가장 보수적
THRESHOLD_LO = 0.58      # 결빙 가능성 1 — 가장 허용적이되 절벽(0.57) 위
THRESHOLD_CLIFF = 0.57   # 이 아래로는 오경보가 한 자릿수 → 60 % 로 튄다 (실측)


def threshold_from_prior(prior: float, hi: float = THRESHOLD_HI, lo: float = THRESHOLD_LO) -> float:
    """결빙 prior 0 → hi, 1 → lo 로 선형 보간. 실측 절벽 아래로는 내려가지 않는다.

    구간 근거는 위 주석과 `logs/carla_demo/정리/05_실사진_대규모평가.md` 참조.
    """
    th = hi - (hi - lo) * max(0.0, min(1.0, prior))
    return max(th, THRESHOLD_CLIFF)

def weights_from_context(loc: LocationCtx, w: WeatherObs) -> Weights:
    night = loc.hour >= 19 or loc.hour < 6
    if night:
        # 야간: 분류기 정확도 하락, 헤드라이트 전반사 신호는 강해짐 → 반사도 가중
        return Weights(alpha=0.35, beta=0.45, gamma=0.20)
    if w.precip_mm > 0:
        # 강수 중: 젖은 노면도 반사 → 반사도 신뢰 하락, 분류·차선 가중
        return Weights(alpha=0.55, beta=0.15, gamma=0.30)
    return Weights(alpha=0.5, beta=0.3, gamma=0.2)

# 이 강수량을 넘으면 카메라(1차)를 믿지 않는다. 젖은 노면의 반사가 얼음과 구분되지 않기 때문이다.
# 근거: 빙판이 없는 대조군에서 폭우 주행의 최대 위험도 0.963, 젖은노면+교통 0.727 —
# 문턱·연속프레임·가중치 재조정 어느 것으로도 막지 못했다 (`정리/07`, `06`).
# 5 mm/h 는 '강한 비'의 통상 기준이고, 그 이하(약한 비·젖은 노면)는 문턱 교정으로 충분했다.
PRECIP_DISTRUST_MM = 5.0


# ---- 기온으로 얼음 자체가 불가능한 조건 -------------------------------------
# 강수 게이트와 의미가 다르다. 강수 게이트는 "카메라를 못 믿겠다 → 2차에 맡긴다"이고,
# 이쪽은 "얼음이 물리적으로 있을 수 없다 → 얼음 경보를 내지 않는다"이다.
# 2차 방어는 그대로 돈다 — 따뜻해도 젖은 노면은 미끄럽다.
#
# 왜 필요한가: 빙판 없는 젖은 노면 대조군(WetNoon)에서 1차가 노면 확률 0.87 로 얼음이라고
# 단언했다. 확인 10프레임을 통과했고, 문턱을 +10 °C 수준(0.742)까지 올려도 못 막는다.
# 영상만으로는 젖음과 얼음이 갈리지 않는다는 뜻이고, 그러면 갈라 줄 쪽은 맥락뿐이다.
#
# 노면은 공기보다 차가울 수 있다. 맑은 밤 복사냉각이 가장 심하고, 다리·터널 출구는 더하다.
# 아래 여유값은 그 최악을 잡은 것이며, 이만큼 빼고도 0 °C 를 넘어야 "불가능"이라고 말한다.
# 낮에도 흐리면 노면이 공기보다 차가울 수 있다. 그늘·다리는 더하다. 전부 최악으로 잡는다.
ROAD_BELOW_AIR_C = {"day": 2.0, "day_exposed": 3.0, "night": 4.0, "night_exposed": 6.0}
ICE_IMPOSSIBLE_MARGIN_C = 0.5     # 0 °C 딱 붙는 것은 불가능이라 하지 않는다
_EXPOSED = ("bridge", "tunnel_exit", "mountain", "shade")


def road_temp_worst_c(w: WeatherObs, loc: LocationCtx) -> float:
    """노면이 공기보다 얼마나 차가울 수 있는지를 최악으로 잡은 추정 노면 온도."""
    night = loc.hour >= 19 or loc.hour < 7
    exposed = loc.feature in _EXPOSED
    key = (("night_exposed" if exposed else "night") if night
           else ("day_exposed" if exposed else "day"))
    return w.temp_c - ROAD_BELOW_AIR_C[key]


def ice_possible(w: WeatherObs, loc: LocationCtx) -> tuple[bool, str]:
    """이 기상·위치에서 블랙아이스가 있을 수 있나. (가능한가, 불가능하다면 이유)"""
    if w.snow_on_ground:
        return True, ""          # 쌓인 눈이 있으면 국소적으로 0 °C 이하 구간이 남는다
    rt = road_temp_worst_c(w, loc)
    if rt > ICE_IMPOSSIBLE_MARGIN_C:
        return False, (f"기온 {w.temp_c:+.1f}°C — 최악으로 잡은 노면 온도 {rt:+.1f}°C 가 "
                       f"어는점보다 높다. 얼음이 아니라 젖은 노면이다")
    return True, ""


def primary_trust(w: WeatherObs) -> tuple[bool, str]:
    """1차 방어를 믿을 수 있는 기상인가. (믿어도 되나, 이유)"""
    if w.precip_mm >= PRECIP_DISTRUST_MM:
        return False, f"강수 {w.precip_mm:.1f} mm/h ≥ {PRECIP_DISTRUST_MM:.0f} — 젖은 노면과 얼음 구분 불가"
    return True, ""


def build_context(w: WeatherObs, loc: LocationCtx) -> ContextMsg:
    prior, reason = freezing_prior(w, loc)
    trust, why = primary_trust(w)
    possible, no_ice_why = ice_possible(w, loc)
    return ContextMsg(
        primary_trustworthy=trust,
        distrust_reason=why,
        ice_possible=possible,
        no_ice_reason=no_ice_why,
        risk_prior=round(prior, 3),
        threshold=round(threshold_from_prior(prior), 3),
        weights=weights_from_context(loc, w),
        weather={"temp_c": w.temp_c, "humidity": w.humidity, "dew_point_c": round(dew_point_c(w.temp_c, w.humidity), 2),
                 "precip_mm": w.precip_mm, "wind_mps": w.wind_mps},
        location={"lat": loc.lat, "lon": loc.lon, "feature": loc.feature, "hour": loc.hour},
        reason=reason,
    )
