import math
from icepredict.pi.fusion import RiskFuser
from icepredict.pi.context import (WeatherObs, LocationCtx, freezing_prior, threshold_from_prior,
                                   build_context, dew_point_c, THRESHOLD_HI, THRESHOLD_LO)
from icepredict.common.protocol import Weights

def test_dew_point_sane():
    assert abs(dew_point_c(20.0, 100.0) - 20.0) < 0.2
    assert dew_point_c(0.0, 50.0) < 0.0

def test_plan_scenario_minus3_dawn_bridge():
    """-3°C·새벽·다리 → 결빙 가능성이 높으니 임계값이 가장 허용적인 쪽(THRESHOLD_LO)으로 내려간다.

    처음에는 계획서대로 0.4 부근을 기대했지만, 실사진 69,358장 스윕에서 0.57 아래로 내리면
    젖은 노면 오경보가 급격히 늘어나는 절벽이 나왔다. 그래서 바닥을 THRESHOLD_LO 로 올렸다.
    숫자를 다시 박지 않고 상수를 불러와 비교한다 — 재교정해도 이 테스트는 살아 있어야 한다.
    """
    w = WeatherObs(temp_c=-3.0, humidity=90.0, temp_trend_c_per_h=-1.0)
    loc = LocationCtx(feature="bridge", hour=5)
    prior, reason = freezing_prior(w, loc)
    assert prior >= 0.85, (prior, reason)
    thr = threshold_from_prior(prior)
    assert THRESHOLD_LO <= thr <= THRESHOLD_LO + 0.05, (thr, reason)
    # 위험할수록 임계값이 낮아야 한다 (단조성)
    assert thr < threshold_from_prior(0.0) <= THRESHOLD_HI

def test_warm_day_low_prior():
    w = WeatherObs(temp_c=15.0, humidity=40.0)
    prior, _ = freezing_prior(w, LocationCtx(hour=14))
    assert prior <= 0.05
    assert threshold_from_prior(prior) >= 0.68

def test_context_msg_fields():
    ctx = build_context(WeatherObs(temp_c=-1.0, humidity=85.0), LocationCtx(hour=23))
    assert 0 <= ctx.risk_prior <= 1 and 0.4 <= ctx.threshold <= 0.7
    assert ctx.weights.beta > ctx.weights.alpha  # 야간엔 반사도 가중

ICE_HIGH = [0.05, 0.05, 0.88, 0.02]      # 위험도 0.82
ICE_MID = [0.1, 0.1, 0.7, 0.1]           # 위험도 0.58
ICE_LOW = [0.1, 0.1, 0.5, 0.3]           # 위험도 0.45


def test_fuser_requires_consecutive_frames():
    """한 프레임만 넘겨서는 경보가 켜지지 않는다.

    폭우 주행에서 위험도가 1~6 프레임(0.12 초)짜리 뾰족한 봉우리로 문턱을 스쳤다. 진짜 빙판은
    한 번 오르면 정지까지 유지된다. 그래서 켜질 때만 연속 확정을 요구한다 (끌 때는 즉시).
    """
    f = RiskFuser(Weights(0.5, 0.3, 0.2), threshold=0.7, hysteresis=0.1, confirm_frames=3)
    r = f.fuse(ICE_HIGH, spec=0.8, lane=0.3)
    assert not r.alarm and r.level == 2 - 1          # 아직 경보는 아니고 주의 단계
    assert f.fuse(ICE_HIGH, spec=0.8, lane=0.3).alarm is False
    r = f.fuse(ICE_HIGH, spec=0.8, lane=0.3)         # 3프레임째 확정
    assert r.alarm and r.level == 2

    # 깜빡임은 걸러진다 — 넘었다가 내려오면 횟수가 초기화된다
    g = RiskFuser(Weights(0.5, 0.3, 0.2), threshold=0.7, hysteresis=0.1, confirm_frames=3)
    for _ in range(2):
        g.fuse(ICE_HIGH, spec=0.8, lane=0.3)
    g.fuse(ICE_LOW, spec=0.4, lane=0.6)              # 문턱 아래로 내려옴
    assert not g.fuse(ICE_HIGH, spec=0.8, lane=0.3).alarm


def test_fuser_threshold_and_hysteresis():
    f = RiskFuser(Weights(0.5, 0.3, 0.2), threshold=0.7, hysteresis=0.1, confirm_frames=2)
    r = f.fuse([0.9, 0.05, 0.03, 0.02], spec=0.1, lane=0.9)
    assert not r.alarm and r.level == 0
    for _ in range(2):
        r = f.fuse(ICE_HIGH, spec=0.8, lane=0.3)                 # 0.44+0.24+0.14 = 0.82
    assert r.alarm and r.level == 2
    r = f.fuse(ICE_MID, spec=0.5, lane=0.6)                      # 0.58 < 0.7-0.1 → 즉시 해제
    assert not r.alarm
    f.update(threshold=0.4)
    for _ in range(2):
        r = f.fuse(ICE_LOW, spec=0.4, lane=0.6)                  # 0.45 >= 0.4 → 다시 경보
    assert r.alarm

def test_weights_normalized():
    f = RiskFuser(Weights(1.0, 1.0, 2.0))
    r = f.fuse([0, 0, 1.0, 0], spec=1.0, lane=0.0)
    assert math.isclose(r.risk, 1.0, abs_tol=1e-9)
