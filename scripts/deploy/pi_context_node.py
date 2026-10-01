#!/usr/bin/env python3
"""Pi 컨텍스트 노드: 기상 수신 → 사전 위험도·임계값 계산 → ZMQ PUB(ctx) 발행 → 로깅.
사용: python3 scripts/deploy/pi_context_node.py [--mock] [--period 600] [--lat 37.56 --lon 126.97] [--feature bridge] [--hour 5]
"""
import argparse, time, json, datetime as dt, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import zmq
from icepredict.common.protocol import encode, TOPIC_CTX, PORT_PI_PUB
from icepredict.pi.context import LocationCtx, build_context
from icepredict.pi import weather

ap = argparse.ArgumentParser()
ap.add_argument("--mock", action="store_true", help="기상 API 대신 고정 관측값")
ap.add_argument("--period", type=float, default=600.0)
ap.add_argument("--lat", type=float, default=37.5665); ap.add_argument("--lon", type=float, default=126.9780)
ap.add_argument("--feature", default="none"); ap.add_argument("--hour", type=int, default=-1)
ap.add_argument("--once", action="store_true")
a = ap.parse_args()

ctx = zmq.Context(); pub = ctx.socket(zmq.PUB); pub.bind(f"tcp://*:{PORT_PI_PUB}")
log = Path(__file__).resolve().parents[2] / "logs" / "context.jsonl"
log.parent.mkdir(exist_ok=True)
print(f"[ctx] PUB tcp://*:{PORT_PI_PUB}  period={a.period}s  mock={a.mock}", flush=True)
time.sleep(0.2)
while True:
    try:
        obs = weather.fetch(a.lat, a.lon, mock=a.mock)
        hour = a.hour if a.hour >= 0 else dt.datetime.now().hour
        msg = build_context(obs, LocationCtx(lat=a.lat, lon=a.lon, feature=a.feature, hour=hour))
        parts = encode(TOPIC_CTX, msg)
        pub.send_multipart(parts)
        body = json.loads(parts[1])
        with log.open("a") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
        print(f"[ctx] prior={msg.risk_prior:.2f} thr={msg.threshold:.2f} w=({msg.weights.alpha},{msg.weights.beta},{msg.weights.gamma}) "
              f"T={obs.temp_c}C RH={obs.humidity}% reason='{msg.reason}'", flush=True)
    except Exception as e:
        print(f"[ctx] ERROR {e}", flush=True)
    if a.once:
        break
    time.sleep(a.period)
