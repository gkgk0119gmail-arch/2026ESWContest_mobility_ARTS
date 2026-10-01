#!/usr/bin/env python3
"""Pi 측 ZeroMQ REP 에코 서버. PC/N6에서 보낸 메시지를 그대로 돌려주며 RTT 측정용."""
import zmq, time, sys

PORT = 5555
ctx = zmq.Context()
sock = ctx.socket(zmq.REP)
sock.bind(f"tcp://*:{PORT}")
print(f"[Pi] ZMQ echo server listening on tcp://0.0.0.0:{PORT}", flush=True)
n = 0
while True:
    msg = sock.recv()
    sock.send(msg)
    n += 1
    if n % 100 == 0:
        print(f"[Pi] {n} msgs echoed, last={msg[:40]!r}", flush=True)
