#!/usr/bin/env python3
"""PC 측 ZeroMQ REQ 클라이언트. 사용: python zmq_echo_client.py <Pi_IP> [횟수]
   PC에 먼저: pip install pyzmq"""
import zmq, time, sys, statistics

host = sys.argv[1] if len(sys.argv) > 1 else "172.24.119.56"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 500
ctx = zmq.Context()
sock = ctx.socket(zmq.REQ)
sock.setsockopt(zmq.RCVTIMEO, 3000)
sock.connect(f"tcp://{host}:5555")
rtts = []
for i in range(N):
    t0 = time.perf_counter()
    sock.send(f"ping {i}".encode())
    sock.recv()
    rtts.append((time.perf_counter() - t0) * 1000)
rtts.sort()
print(f"host={host} n={N}")
print(f"RTT ms  min={rtts[0]:.2f}  avg={statistics.mean(rtts):.2f}  p95={rtts[int(N*0.95)]:.2f}  max={rtts[-1]:.2f}")
