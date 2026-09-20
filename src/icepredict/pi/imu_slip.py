"""2차 방어: IMU 기반 미끄러짐 감지 (칼만 필터 + 기대 yaw rate 비교).

원리
- 자전거 모델: 정상 접지 시 yaw_rate_expected = v * tan(δ) / L
- 미끄러지면 실제 yaw rate가 기대값에서 벗어나고(오버/언더스티어), 측면 가속도가 튄다.
- 1kHz 샘플에 2상태 칼만 필터(값, 변화율)를 적용해 노이즈를 걸러낸 뒤,
  |ay| >= 0.3g 또는 |yaw - yaw_expected| >= thr 가 N샘플 연속이면 확정.
이 파이썬 구현은 N6 C 코드의 참조·검증용이다. 상태 수를 2로 제한해 M55에서 10μs 이내에 돌도록 했다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np

G = 9.80665

class Kalman2:
    """상태 x=[value, rate], 관측 z=value. 상수속도 모델."""
    def __init__(self, dt: float, q: float = 1.0, r: float = 0.05):
        self.dt = dt
        self.x = np.zeros(2)
        self.P = np.eye(2)
        self.F = np.array([[1.0, dt], [0.0, 1.0]])
        self.H = np.array([[1.0, 0.0]])
        self.Q = q * np.array([[dt**4 / 4, dt**3 / 2], [dt**3 / 2, dt**2]])
        self.R = np.array([[r]])
        self._init = False

    def step(self, z: float) -> float:
        if not self._init:
            self.x[0] = z
            self._init = True
            return z
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        y = z - (self.H @ self.x)[0]
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T / S
        self.x = self.x + (K * y).ravel()
        self.P = (np.eye(2) - K @ self.H) @ self.P
        return float(self.x[0])

@dataclass
class SlipEvent:
    t: float
    trigger: str        # "lat_acc" / "yaw_rate" / "combo" (둘 다 임계 아래지만 합쳐서 넘음)
    ay_g: float
    yaw_err: float

@dataclass
class SlipDetector:
    rate_hz: float = 1000.0
    wheelbase_m: float = 2.7
    max_steer_rad: float = math.radians(35)  # steer -1~1 → 조향각
    v_ch_mps: float = 17.0                    # 특성속도 — 고속 언더스티어 보정 (C 코어 SLIP_V_CH_MPS 와 동일)
    lat_acc_thr_g: float = 0.3
    yaw_err_thr: float = 0.35                 # rad/s
    # 판정 형태: True = 타원, False = 예전 직사각형(OR). C 코어의 SLIP_RULE_ELLIPSE 와 같이 움직여야 한다.
    # 35 km/h 빙판에서 ay_g 가 0.287 로 임계 0.300 에 4 % 못 미쳐 3.2 s 를 더 기다린 것을 고친다.
    ellipse_rule: bool = True
    confirm_samples: int = 5                  # 5ms 연속
    min_speed_mps: float = 2.0
    kf_ay: Kalman2 = field(init=False)
    kf_yaw: Kalman2 = field(init=False)
    _hits: int = 0
    _armed: bool = True

    def __post_init__(self):
        dt = 1.0 / self.rate_hz
        # R = 센서 노이즈 분산(ay σ0.4 → 0.16, gyro σ0.02 → 0.0005), Q 크게 = 빠른 추종.
        # 합성 스윕: 임계 도달 후 확정 지연 8.7ms, 노이즈 2배(0.8m/s²) 정상 주행 8회 오탐 0
        self.kf_ay = Kalman2(dt, q=2000.0, r=0.16)
        self.kf_yaw = Kalman2(dt, q=3000.0, r=0.0005)

    def expected_yaw(self, speed_mps: float, steer: float) -> float:
        delta = steer * self.max_steer_rad
        return speed_mps * math.tan(delta) / self.wheelbase_m / (1.0 + (speed_mps / self.v_ch_mps) ** 2)

    def reset(self):
        self._hits = 0
        self._armed = True

    def step(self, t: float, ay: float, gz: float, speed_mps: float, steer: float) -> SlipEvent | None:
        """ay: 측면 가속도(m/s²), gz: yaw rate(rad/s). 정상 코너링 성분을 뺀 잔차로 판정. 확정 시 SlipEvent 반환."""
        ay_f = self.kf_ay.step(ay)
        yaw_f = self.kf_yaw.step(gz)
        if speed_mps < self.min_speed_mps:
            self._hits = 0
            return None
        yaw_exp = self.expected_yaw(speed_mps, steer)
        ay_g = (ay_f - speed_mps * yaw_exp) / G      # 조향으로 설명되는 원심가속도 제거 → 잔차
        yaw_err = yaw_f - yaw_exp
        lat_hit = abs(ay_g) >= self.lat_acc_thr_g
        yaw_hit = abs(yaw_err) >= self.yaw_err_thr
        if self.ellipse_rule:
            hit = (ay_g / self.lat_acc_thr_g) ** 2 + (yaw_err / self.yaw_err_thr) ** 2 >= 1.0
        else:
            hit = lat_hit or yaw_hit
        if hit:
            self._hits += 1
        else:
            self._hits = 0
            self._armed = True
        if self._hits >= self.confirm_samples and self._armed:
            self._armed = False   # 한 번 확정하면 정상 복귀 전까지 재발화 금지
            trig = "lat_acc" if lat_hit else ("yaw_rate" if yaw_hit else "combo")
            return SlipEvent(t=t, trigger=trig, ay_g=ay_g, yaw_err=yaw_err)
        return None

def emergency_command(ev: SlipEvent) -> dict:
    """미끄러짐 확정 → CARLA 명령. 카운터 스티어는 yaw 오차 반대 방향 5도."""
    steer_cmd = -math.copysign(math.radians(5) / math.radians(35), ev.yaw_err) if ev.yaw_err != 0 else 0.0
    return {"brake": 0.6, "steer": round(steer_cmd, 3), "throttle": 0.0, "pulse_ms": 200, "trigger": ev.trigger}

# ---- 합성 시나리오 (테스트·튜닝용) --------------------------------------------
def synth_run(rate_hz=1000.0, duration_s=4.0, slip_at_s=2.0, speed_mps=20.0, steer=0.02,
              noise_ay=0.4, noise_gz=0.02, seed=0):
    """정상 주행 후 slip_at_s에 블랙아이스 진입: yaw rate 급증 + 측면 가속도 튐."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * rate_hz)
    t = np.arange(n) / rate_hz
    det = SlipDetector(rate_hz=rate_hz)
    yaw_nominal = det.expected_yaw(speed_mps, steer)
    ay = rng.normal(0.0, noise_ay, n) + speed_mps * yaw_nominal
    gz = rng.normal(yaw_nominal, noise_gz, n)
    slip = t >= slip_at_s
    ramp = np.clip((t - slip_at_s) / 0.15, 0, 1)   # 150ms에 걸쳐 전개
    gz[slip] += 0.9 * ramp[slip]                   # 오버스티어
    ay[slip] += 0.45 * G * ramp[slip]
    return t, ay, gz, speed_mps, steer
