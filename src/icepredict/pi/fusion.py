"""베이지안 융합: 위험도 = α·P(블랙아이스) + β·반사도 + γ·(1 - 차선 가시성).

가중치는 Pi가 기상·시간대에 따라 동적으로 조정한다. 히스테리시스로 경고 깜빡임을 막는다.
N6에서는 같은 식을 C로 구현하며, 이 모듈은 Pi 측 검증·로깅·시뮬용 참조 구현이다.
"""
from __future__ import annotations
from dataclasses import dataclass
from icepredict.common.protocol import Weights, ROAD_CLASSES

ICE_IDX = ROAD_CLASSES.index("black_ice")

@dataclass
class FusionResult:
    risk: float
    alarm: bool
    level: int          # 0 none / 1 caution / 2 danger
    contrib: dict       # 각 항 기여도 (디버깅·복기 리포트용)

class RiskFuser:
    def __init__(self, weights: Weights | None = None, threshold: float = 0.7,
                 hysteresis: float = 0.1, caution_ratio: float = 0.75):
        self.w = weights or Weights()
        self.threshold = threshold
        self.hyst = hysteresis
        self.caution_ratio = caution_ratio
        self._alarm = False

    def update(self, weights: Weights | None = None, threshold: float | None = None):
        if weights is not None:
            self.w = weights
        if threshold is not None:
            self.threshold = max(0.05, min(0.95, threshold))

    def fuse(self, p_cls: list[float], spec: float, lane: float) -> FusionResult:
        p_ice = float(p_cls[ICE_IDX])
        spec = min(1.0, max(0.0, float(spec)))
        lane = min(1.0, max(0.0, float(lane)))
        w = self.w
        s = w.alpha + w.beta + w.gamma
        a, b, g = w.alpha / s, w.beta / s, w.gamma / s   # 합이 1이 되도록 정규화
        contrib = {"cls": a * p_ice, "spec": b * spec, "lane": g * (1.0 - lane)}
        risk = sum(contrib.values())

        # 히스테리시스: 켜질 땐 threshold, 꺼질 땐 threshold - hyst
        if self._alarm:
            self._alarm = risk >= self.threshold - self.hyst
        else:
            self._alarm = risk >= self.threshold
        if self._alarm:
            level = 2
        elif risk >= self.threshold * self.caution_ratio:
            level = 1
        else:
            level = 0
        return FusionResult(risk=risk, alarm=self._alarm, level=level, contrib=contrib)
