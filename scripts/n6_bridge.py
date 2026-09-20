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
# NPU 프레임 경로 (보드 UDP 5559): 청크 <IHHHH + payload, 응답 <I4ffBBHII. 펌웨어 app_netxduo.c와 일치.
FR_HDR, FR_VD, FR_CHUNK, FR_BYTES = "<IHHHH", "<I4fffBBHII", 1400, 150528   # 응답에 spec 추가
# 2차 방어(IMU 미끄러짐 감지) 경로 (보드 UDP 5557, 32B): 펌웨어 patch_fw_slip.py 의 ip_imu_t / ip_slip_t 와 일치
# v3: 60B 요청(차선 오차·주변 차 간격·종가속·제동 명령 포함) / 28B 응답(emerg, mode 포함) — 펌웨어 patch_fw_slip.py 와 일치
IMU_FMT, SLIP_FMT = "<I15fI", "<IBBBBffIff"     # v7: 68B (ice_lane_off, front_rel_v 추가)
IMU_MAGIC, IMU_RESET = 0x31554D49, 0x30554D49        # 'IMU1' / 'IMU0'
SLIP_SZ = struct.calcsize(SLIP_FMT)
NAN = float("nan")

ap = argparse.ArgumentParser()
ap.add_argument("--board", default="192.168.50.158")
ap.add_argument("--board-port", type=int, default=5557)
ap.add_argument("--desktop", default="165.132.135.77")
ap.add_argument("--desktop-port", type=int, default=PORT_PC_PUB)
ap.add_argument("--timeout", type=float, default=0.5, help="보드 UDP 응답 대기(초)")
ap.add_argument("--frame-port", type=int, default=5559)
ap.add_argument("--gap-us", type=int, default=150, help="프레임 청크 간격 — 버스트면 보드 NetX 풀이 넘친다")
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
stats = {"infer": 0, "ctx": 0, "timeout": 0, "board_ns_sum": 0, "frame": 0, "frame_timeout": 0, "npu_us_sum": 0,
         "imu": 0, "imu_timeout": 0, "imu_ns_sum": 0, "slip": 0}
fr_id = 0
udp_f = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); udp_f.settimeout(2.0)

def board_frame(q_bytes):
    """int8 NCHW 150528B를 청크로 보내 보드 NPU 추론+융합 판정을 받는다. 실패 시 None."""
    global fr_id
    fr_id = (fr_id + 1) & 0xFFFFFFFF
    n = (len(q_bytes) + FR_CHUNK - 1) // FR_CHUNK
    for i in range(n):
        pl = q_bytes[i * FR_CHUNK:(i + 1) * FR_CHUNK]
        udp_f.sendto(struct.pack(FR_HDR, fr_id, i, n, len(pl), 0) + pl, (a.board, a.frame_port))
        if a.gap_us:
            t_next = time.perf_counter() + a.gap_us / 1e6
            while time.perf_counter() < t_next: pass
    try:
        d, _ = udp_f.recvfrom(128)
    except socket.timeout:
        return None
    fid, l0, l1, l2, l3, spec, risk, alarm, level, _pad, infer_us, total_us = struct.unpack(FR_VD, d)
    if fid != fr_id: return None
    return {"logits": [l0, l1, l2, l3], "spec": spec, "risk": risk, "alarm": bool(alarm), "level": level, "infer_us": infer_us, "total_us": total_us}
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

MODE_NAME = {0: "none", 1: "lane_keep", 2: "evade_left", 3: "evade_right", 4: "hard_stop", 5: "stopped"}
def board_imu(ay, gz, speed, steer, dt, min_speed, reset=False, lane_err=0.0, lat_off=0.0,
              gap_front=999.0, gap_left=999.0, gap_right=999.0, ax=0.0, brake_cmd=0.0, ice_lane_off=0.0, front_rel_v=0.0):
    """IMU 한 샘플(+차선 오차·주변 차 간격)을 보드에 보내 미끄러짐 판정과 비상 제어 명령을 받는다. 실패 시 None."""
    global seq
    seq += 1
    udp.sendto(struct.pack(IMU_FMT, seq, float(ay), float(gz), float(speed), float(steer), float(dt), float(min_speed),
                           float(lane_err), float(lat_off), float(gap_front), float(gap_left), float(gap_right),
                           float(ax), float(brake_cmd), float(ice_lane_off), float(front_rel_v), IMU_RESET if reset else IMU_MAGIC), board)
    try:
        data, _ = udp.recvfrom(64)
    except socket.timeout:
        return None
    if len(data) != SLIP_SZ:
        return None
    s_, slip, trig, emerg, mode, ay_g, yaw_err, lat, brake, steer_cmd = struct.unpack(SLIP_FMT, data)
    if s_ != seq:
        return None
    return {"slip": bool(slip), "trigger": {1: "lat_acc", 2: "yaw_rate", 3: "low_mu"}.get(trig, "none"), "ay_g": ay_g, "yaw_err": yaw_err,
            "latency_ns": lat, "brake": brake, "steer": steer_cmd, "emerg": bool(emerg), "mode": MODE_NAME.get(mode, str(mode))}

def board_ctx(threshold, alpha, beta, gamma, hysteresis=0.1):
    global seq
    seq += 1
    udp.sendto(struct.pack(CTX_FMT, seq, float(threshold), float(alpha),
                           float(beta), float(gamma), float(hysteresis)), board)

try:
    while True:
        try:
            parts = rep.recv_multipart()
        except KeyboardInterrupt:
            break
        msg = json.loads(parts[0]); kind = msg.get("kind")

        if kind == "frame":                                   # [json, int8 bytes]
            q = parts[1] if len(parts) > 1 else b""
            r = board_frame(q) if len(q) == FR_BYTES else None
            if r is None:
                stats["frame_timeout"] += 1; rep.send_json({"ok": False, "error": "board frame timeout/len"})
            else:
                stats["frame"] += 1; stats["npu_us_sum"] += r["infer_us"]; r["ok"] = True; rep.send_json(r)
            continue

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

        elif kind in ("imu", "imu_reset"):
            r = board_imu(msg.get("ay", 0.0), msg.get("gz", 0.0), msg.get("speed", 0.0), msg.get("steer", 0.0),
                          msg.get("dt", 0.02), msg.get("min_speed", 0.0), reset=(kind == "imu_reset"),
                          lane_err=msg.get("lane_err", 0.0), lat_off=msg.get("lat_off", 0.0),
                          gap_front=msg.get("gap_front", 999.0), gap_left=msg.get("gap_left", 999.0), gap_right=msg.get("gap_right", 999.0),
                          ax=msg.get("ax", 0.0), brake_cmd=msg.get("brake_cmd", 0.0),
                          ice_lane_off=msg.get("ice_lane_off", 0.0), front_rel_v=msg.get("front_rel_v", 0.0))
            if r is None:
                stats["imu_timeout"] += 1
                rep.send_json({"ok": False, "error": "board imu timeout/len (펌웨어에 slip 경로가 없을 수 있다)"})
            else:
                stats["imu"] += 1; stats["imu_ns_sum"] += r["latency_ns"]; stats["slip"] += int(r["slip"])
                if r["slip"]:
                    print(f"[bridge] 보드 SLIP ({r['trigger']}) ay_g={r['ay_g']:.2f} yaw_err={r['yaw_err']:.2f} "
                          f"→ mode {r['mode']} brake {r['brake']:.2f} steer {r['steer']:+.3f} ({r['latency_ns']/1000:.1f}us)", flush=True)
                r["ok"] = True; rep.send_json(r)

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
    n = max(stats["infer"], 1); nf = max(stats["frame"], 1)
    print(f"\n[bridge] 종료 — infer {stats['infer']}건, ctx {stats['ctx']}건, 타임아웃 {stats['timeout']}건, 보드 평균 {stats['board_ns_sum']/n/1000:.2f}us | "
          f"NPU 프레임 {stats['frame']}건, 실패 {stats['frame_timeout']}건, NPU 평균 {stats['npu_us_sum']/nf/1000:.1f}ms | "
          f"IMU {stats['imu']}건(미끄러짐 {stats['slip']}건), 실패 {stats['imu_timeout']}건, 보드 평균 {stats['imu_ns_sum']/max(stats['imu'],1)/1000:.1f}us | {el:.1f}초", flush=True)
    rep.close(0); ctx.term()
