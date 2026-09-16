"""Pi <-> STM32N6 <-> CARLA 간 ZeroMQ 메시지 프로토콜.

전송 형식: multipart [topic(bytes), json(bytes)] (+ frame일 때 raw bytes 1개 추가)
모든 메시지에 ts(송신 시각, epoch 초)와 seq(송신자별 증가 번호)를 넣어 지연·유실을 측정한다.
"""
from __future__ import annotations
import json, time, itertools
from dataclasses import dataclass, asdict, field
from typing import Any

# ---- 토픽 정의 -----------------------------------------------------------
TOPIC_CTX   = b"ctx"    # Pi -> N6   : 기상/위치 컨텍스트, 임계값·융합 가중치
TOPIC_INFER = b"infer"  # N6 -> Pi   : NPU 추론 결과 (30ms 주기)
TOPIC_WARN  = b"warn"   # N6 -> Pi/PC: 1차 경고 (햅틱·LCD·음성 동기)
TOPIC_EMERG = b"emerg"  # N6 -> PC   : 2차 비상 제어 명령
TOPIC_IMU   = b"imu"    # PC -> N6   : CARLA IMU 1kHz
TOPIC_FRAME = b"frame"  # PC -> N6   : 카메라 프레임 (JPEG)
TOPIC_VEH   = b"veh"    # PC -> Pi/N6: 차량 상태 (속도, 조향, GPS)
TOPIC_LOG   = b"log"    # any -> Pi  : 이벤트 로깅

# 포트 규약 (동일 서브넷 가정)
PORT_PI_PUB   = 5556   # Pi가 ctx 발행
PORT_N6_PUB   = 5557   # N6가 infer/warn/emerg 발행
PORT_PC_PUB   = 5558   # PC(CARLA)가 imu/frame/veh 발행
PORT_PI_REP   = 5555   # Pi REQ-REP (이벤트 보고·에코)

ROAD_CLASSES = ("normal", "wet", "black_ice", "pothole")

_seq = itertools.count()

def _base() -> dict:
    return {"ts": time.time(), "seq": next(_seq)}

@dataclass
class Weights:
    alpha: float = 0.5   # 분류 확률 가중치
    beta: float = 0.3    # 반사도 가중치
    gamma: float = 0.2   # (1 - 차선 가시성) 가중치

@dataclass
class ContextMsg:
    risk_prior: float               # 0~1, 기상·위치 기반 사전 위험도
    threshold: float                # N6가 사용할 경고 임계값 (0.7 -> 0.4)
    weights: Weights = field(default_factory=Weights)
    weather: dict = field(default_factory=dict)   # temp, humidity, dew_point, precip ...
    location: dict = field(default_factory=dict)  # lat, lon, feature(bridge/tunnel/none)
    reason: str = ""

@dataclass
class InferMsg:
    p_cls: list            # len 4, ROAD_CLASSES 순서
    spec: float            # 반사도 0~1
    lane: float            # 차선 가시성 0~1
    risk: float            # N6 내부 융합 결과
    infer_ms: float        # NPU 추론 시간

@dataclass
class WarnMsg:
    level: int             # 0 none, 1 caution, 2 danger
    direction: str         # both / left / right
    distance_m: float
    risk: float

@dataclass
class EmergMsg:
    brake: float           # 0~1
    steer: float           # -1~1 (카운터 스티어)
    throttle: float        # 강제 0
    pulse_ms: int          # 브레이크 펄스 길이
    trigger: str           # "lat_acc" / "yaw_rate"

@dataclass
class ImuMsg:
    ax: float; ay: float; az: float
    gx: float; gy: float; gz: float
    speed_mps: float = 0.0
    steer: float = 0.0     # -1~1

# ---- 인코딩/디코딩 --------------------------------------------------------
def encode(topic: bytes, msg: Any, payload: bytes | None = None) -> list[bytes]:
    body = asdict(msg) if hasattr(msg, "__dataclass_fields__") else dict(msg)
    body.update(_base())
    parts = [topic, json.dumps(body, separators=(",", ":")).encode()]
    if payload is not None:
        parts.append(payload)
    return parts

def decode(parts: list[bytes]) -> tuple[bytes, dict, bytes | None]:
    topic, body = parts[0], json.loads(parts[1])
    payload = parts[2] if len(parts) > 2 else None
    return topic, body, payload

def age_ms(body: dict) -> float:
    """수신 시점 기준 메시지 나이(ms). 송수신 시계가 동기화돼 있어야 의미 있음."""
    return (time.time() - body["ts"]) * 1000.0
