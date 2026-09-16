import math
from icepredict.pi.fusion import RiskFuser
from icepredict.pi.context import WeatherObs, LocationCtx, freezing_prior, threshold_from_prior, build_context, dew_point_c
from icepredict.common.protocol import Weights

def test_dew_point_sane():
    assert abs(dew_point_c(20.0, 100.0) - 20.0) < 0.2
    assert dew_point_c(0.0, 50.0) < 0.0

def test_plan_scenario_minus3_dawn_bridge():
    # 계획서: -3°C, 새벽, 다리 진입 → 위험도 ≈0.92 → 임계값 0.7→0.4 부근
    w = WeatherObs(temp_c=-3.0, humidity=90.0, temp_trend_c_per_h=-1.0)
    loc = LocationCtx(feature="bridge", hour=5)
    prior, reason = freezing_prior(w, loc)
    assert prior >= 0.85, (prior, reason)
    thr = threshold_from_prior(prior)
    assert 0.40 <= thr <= 0.46

def test_warm_day_low_prior():
    w = WeatherObs(temp_c=15.0, humidity=40.0)
    prior, _ = freezing_prior(w, LocationCtx(hour=14))
    assert prior <= 0.05
    assert threshold_from_prior(prior) >= 0.68

def test_context_msg_fields():
    ctx = build_context(WeatherObs(temp_c=-1.0, humidity=85.0), LocationCtx(hour=23))
    assert 0 <= ctx.risk_prior <= 1 and 0.4 <= ctx.threshold <= 0.7
    assert ctx.weights.beta > ctx.weights.alpha  # 야간엔 반사도 가중

def test_fuser_threshold_and_hysteresis():
    f = RiskFuser(Weights(0.5, 0.3, 0.2), threshold=0.7, hysteresis=0.1)
    r = f.fuse([0.9, 0.05, 0.03, 0.02], spec=0.1, lane=0.9)
    assert not r.alarm and r.level == 0
    r = f.fuse([0.05, 0.05, 0.88, 0.02], spec=0.8, lane=0.3)   # 0.44+0.24+0.14 = 0.82
    assert r.alarm and r.level == 2
    r = f.fuse([0.1, 0.1, 0.7, 0.1], spec=0.5, lane=0.6)        # 0.35+0.15+0.08 = 0.58
    assert not r.alarm                                           # 0.58 < 0.6 → 해제
    f.update(threshold=0.4)
    r = f.fuse([0.1, 0.1, 0.5, 0.3], spec=0.4, lane=0.6)        # 0.25+0.12+0.08 = 0.45
    assert r.alarm                                               # 낮아진 임계값으로 경고

def test_weights_normalized():
    f = RiskFuser(Weights(1.0, 1.0, 2.0))
    r = f.fuse([0, 0, 1.0, 0], spec=1.0, lane=0.0)
    assert math.isclose(r.risk, 1.0, abs_tol=1e-9)
