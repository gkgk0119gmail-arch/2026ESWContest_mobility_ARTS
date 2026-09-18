#!/usr/bin/env python3
"""Pi 브리지: 데스크탑 CARLA 데모 ↔ STM32N6 융합 서비스.

왜 방향을 뒤집는가
  학교망이 유선→무선을 차단해 데스크탑에서 Pi로 접속할 수 없다 (실측: 100% 손실,
  TCP 불가). Pi→데스크탑만 된다. 그래서 ZMQ 소켓의 bind/connect를 뒤집는다 —
  데스크탑이 bind하고 Pi가 connect한다. TCP는 한 번 연결되면 양방향이므로 데모는
  그 소켓으로 요청을 보내고 응답을 받는다.

    데스크탑 carla_demo.py  (REQ, bind tcp://*:5558)
          ↑ Pi가 접속
    Pi   n6_bridge.py       (REP, connect)  ──UDP 5557──▶  STM32N6

보드는 Pi의 사설망(192.168.50.0/24)에만 있고 데스크탑에서 직접 닿지 않는다. 이 브리지가
유일한 경로다.

사용:
  python3 scripts/n6_bridge.py                      # 기본값으로 실행
  python3 scripts/n6_bridge.py --board 192.168.50.158 --desktop 165.132.135.77
"""
import argparse, json, os, socket, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import zmq
from icepredict.common.protocol import PORT_PC_PUB

CTX_FMT, INFER_FMT, VD_FMT = "<I5f", "<I6f", "<IfBBHI"
VD_SZ = struct.calcsize(VD_FMT)
NAN = float("nan")

ap = argparse.ArgumentParser()
ap.add_argument("--board", default="192.168.50.158")
ap.add_argument("--board-port", type=int, default=5557)
ap.add_argument("--desktop", default="165.132.135.77")
ap.add_argument("--desktop-port", type=int, default=PORT_PC_PUB)
ap.add_argument("--timeout", type=float, default=0.5, help="보드 UDP 응답 대기(초)")
a = ap.parse_args()

board = (a.board, a.board_port)
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.settimeout(a.timeout)

ctx = zmq.Context()
rep = ctx.socket(zmq.REP)
endpoint = f"tcp://{a.desktop}:{a.desktop_port}"
rep.connect(endpoint)     # 데스크탑이 bind, 우리가 connect (차단 방향 회피)
print(f"[bridge] ZMQ REP → {endpoint}   보드 UDP → {a.board}:{a.board_port}", flush=True)

seq = 0
stats = {"infer": 0, "ctx": 0, "timeout": 0, "board_ns_sum": 0}
t0 = time.time()

def board_infer(p, spec, lane):
    """추론 확률을 보드에 보내 판정을 받는다. 실패 시 None."""
    global seq
    seq += 1
    pkt = struct.pack(INFER_FMT, seq,
                      float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                      NAN if spec is None else float(spec),
                      NAN if lane is None else float(lane))
    udp.sendto(pkt, board)
    try:
        data, _ = udp.recvfrom(64)
    except socket.timeout:
        return None
    if len(data) != VD_SZ:
        return None
    _s, risk, alarm, level, _pad, lat = struct.unpack(VD_FMT, data)
    return {"risk": risk, "alarm": bool(alarm), "level": level, "board_ns": lat}

def board_ctx(threshold, alpha, beta, gamma, hysteresis=0.1):
    global seq
    seq += 1
    udp.sendto(struct.pack(CTX_FMT, seq, float(threshold), float(alpha),
                           float(beta), float(gamma), float(hysteresis)), board)

try:
    while True:
        try:
            msg = rep.recv_json()
        except KeyboardInterrupt:
            break
        kind = msg.get("kind")

        if kind == "ctx":
            board_ctx(msg["threshold"], msg["alpha"], msg["beta"], msg["gamma"],
                      msg.get("hysteresis", 0.1))
            stats["ctx"] += 1
            print(f"[bridge] ctx → 보드: threshold={msg['threshold']:.3f} "
                  f"w=({msg['alpha']},{msg['beta']},{msg['gamma']})", flush=True)
            rep.send_json({"ok": True})

        elif kind == "infer":
            r = board_infer(msg["p"], msg.get("spec"), msg.get("lane"))
            if r is None:
                stats["timeout"] += 1
                rep.send_json({"ok": False, "error": "board timeout"})
            else:
                stats["infer"] += 1
                stats["board_ns_sum"] += r["board_ns"]
                r["ok"] = True
                rep.send_json(r)

        elif kind == "ping":
            rep.send_json({"ok": True, "board": a.board})

        elif kind == "bye":
            rep.send_json({"ok": True})
            break
        else:
            rep.send_json({"ok": False, "error": f"unknown kind {kind!r}"})
except KeyboardInterrupt:
    pass
finally:
    el = time.time() - t0
    n = max(stats["infer"], 1)
    print(f"\n[bridge] 종료 — infer {stats['infer']}건, ctx {stats['ctx']}건, "
          f"타임아웃 {stats['timeout']}건, 보드 평균 {stats['board_ns_sum']/n/1000:.2f}us, "
          f"{el:.1f}초", flush=True)
    rep.close(0); ctx.term()
