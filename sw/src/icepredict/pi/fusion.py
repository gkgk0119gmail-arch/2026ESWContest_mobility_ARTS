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

# 1차 경보 확정에 필요한 연속 프레임 수.
#
# 왜 필요한가: 빙판이 **없는** 대조군 주행에서 위험도가 특정 지점에서만 0.1 초쯤 튀는 일이
# 있다 (2026-09-21 WetNoon 대조군: 489 프레임 중 18 프레임이 문턱을 넘었고, 연속 구간
# 최대 길이가 6 프레임 = 0.12 초). 진짜 빙판은 그렇지 않다 — 한 번 오르면 정지까지 유지된다.
#
# 실측으로 고른 값: 그 주행에서 K=5 면 아직 발화하고 K=8 이면 발화하지 않는다.
# 대가는 확정 지연 8 프레임 = 0.16 초 = 38 km/h 에서 1.7 m 다. 경보 거리 중앙값이 11 m 이므로
# 감당할 수 있다. 2차 방어가 이미 같은 개념(confirm 8 샘플)을 쓰고 있어 구조도 일관된다.
ALARM_CONFIRM_FRAMES = 8


class RiskFuser:
    def __init__(self, weights: Weights | None = None, threshold: float = 0.7,
                 hysteresis: float = 0.1, caution_ratio: float = 0.75,
                 confirm_frames: int = ALARM_CONFIRM_FRAMES):
        self.w = weights or Weights()
        self.threshold = threshold
        self.hyst = hysteresis
        self.caution_ratio = caution_ratio
        self.confirm = max(1, int(confirm_frames))
        self._alarm = False
        self._hits = 0          # 문턱을 연속으로 넘은 프레임 수

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
        # 확정: 켜질 때는 연속 confirm 프레임을 요구한다 (0.1 초짜리 깜빡임을 거른다).
        # 꺼질 때는 즉시 — 위험을 놓치지 않는 쪽으로 비대칭을 둔다.
        if self._alarm:
            self._alarm = risk >= self.threshold - self.hyst
            if not self._alarm:
                self._hits = 0
        else:
            if risk >= self.threshold:
                self._hits += 1
                if self._hits >= self.confirm:
                    self._alarm = True
            else:
                self._hits = 0
        if self._alarm:
            level = 2
        elif risk >= self.threshold * self.caution_ratio:
            level = 1
        else:
            level = 0
        return FusionResult(risk=risk, alarm=self._alarm, level=level, contrib=contrib)
