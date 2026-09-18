"""베이지안 융합: 위험도 = α·P(블랙아이스) + β·반사도 + γ·(1 - 차선 가시성).

가중치는 Pi가 기상·시간대에 따라 동적으로 조정한다. 히스테리시스로 경고 깜빡임을 막는다.
N6에서는 같은 식을 C로 구현하며, 이 모듈은 Pi 측 검증·로깅·시뮬용 참조 구현이다.
아직 학습하지 않은 신호(반사도·차선)는 spec=None / lane=None으로 넘겨 제외한다.
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

    def fuse(self, p_cls: list[float], spec: float | None, lane: float | None) -> FusionResult:
        """spec/lane에 None을 주면 그 신호가 없는 것으로 보고 가중치를 재정규화한다.

        0을 넣어 '중립'으로 쓰면 안 된다. 가중 합에서 0은 중립이 아니라 최솟값이고, 가장 큰
        가중치(beta=0.45)를 죽이면 위험도 상한이 0.35로 잘려 임계값 0.441에 도달할 수 없다.
        실제로 반사도 헤드가 미학습인 동안 CARLA 데모의 1차 방어가 p_ice=0.92에서도 발화하지
        못한 원인이 이것이었다. 없는 신호는 빼고 있는 신호로만 정규화하는 것이 맞다.
        """
        p_ice = float(p_cls[ICE_IDX])
        w = self.w
        terms = {"cls": (w.alpha, p_ice)}
        if spec is not None:
            terms["spec"] = (w.beta, min(1.0, max(0.0, float(spec))))
        if lane is not None:
            terms["lane"] = (w.gamma, 1.0 - min(1.0, max(0.0, float(lane))))
        s = sum(wt for wt, _ in terms.values()) or 1.0
        contrib = {k: (wt / s) * v for k, (wt, v) in terms.items()}
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
