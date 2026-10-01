#!/usr/bin/env python3
"""Pi ↔ STM32N6 위험도 융합 UDP 클라이언트.

N6 펌웨어(icepredict_n6)의 IcePredict 융합 서비스와 통신한다. 보드에 올린 C 구현이
src/icepredict/pi/fusion.py 의 RiskFuser와 같은 값을 내는지 검증하는 것이 주 목적이다.
두 구현이 어긋나면 Pi 시뮬 결과와 실제 보드 동작이 달라진다.

패킷 (little-endian, packed) — 펌웨어와 반드시 일치해야 한다:
  ctx     Pi  -> N6 : <I 5f      seq, threshold, alpha, beta, gamma, hysteresis  = 24 bytes
  infer   호스트-> N6 : <I 6f      seq, p[4], spec, lane                           = 28 bytes
  verdict N6  -> 송신자: <I f B B H I seq, risk, alarm, level, pad, latency_ns      = 16 bytes

spec/lane에 NaN을 넣으면 그 신호가 없는 것으로 보고 가중치를 재정규화한다
(미학습 헤드를 0으로 넣으면 위험도 상한이 잘린다 — fusion.py의 spec=None과 같은 의미).

사용:
  python3 scripts/deploy/n6_fusion_client.py --ip 192.168.50.158 --selftest
  python3 scripts/deploy/n6_fusion_client.py --ip 192.168.50.158 --p-ice 0.92
"""
import argparse, math, socket, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

CTX_FMT, INFER_FMT, VD_FMT = "<I5f", "<I6f", "<IfBBHI"
CTX_SZ, INFER_SZ, VD_SZ = struct.calcsize(CTX_FMT), struct.calcsize(INFER_FMT), struct.calcsize(VD_FMT)
NAN = float("nan")

def send_ctx(sock, addr, seq, threshold, alpha, beta, gamma, hyst=0.1):
    sock.sendto(struct.pack(CTX_FMT, seq, threshold, alpha, beta, gamma, hyst), addr)

def send_infer(sock, addr, seq, p, spec=NAN, lane=NAN, timeout=2.0):
    sock.settimeout(timeout)
    t0 = time.perf_counter()
    sock.sendto(struct.pack(INFER_FMT, seq, *p, spec, lane), addr)
    data, _ = sock.recvfrom(64)
    rtt_us = (time.perf_counter() - t0) * 1e6
    seq_r, risk, alarm, level, _pad, lat_ns = struct.unpack(VD_FMT, data)
    return dict(seq=seq_r, risk=risk, alarm=bool(alarm), level=level,
                board_ns=lat_ns, rtt_us=rtt_us)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True)
    ap.add_argument("--port", type=int, default=5557)
    ap.add_argument("--p-ice", type=float, default=0.8)
    ap.add_argument("--threshold", type=float, default=0.441, help="기상 컨텍스트가 내린 값")
    ap.add_argument("--weights", default="0.35,0.45,0.20", help="alpha,beta,gamma")
    ap.add_argument("--selftest", action="store_true", help="Pi 참조 구현과 값 일치 검증")
    a = ap.parse_args()

    alpha, beta, gamma = (float(x) for x in a.weights.split(","))
    addr = (a.ip, a.port)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    send_ctx(s, addr, 1, a.threshold, alpha, beta, gamma)
    time.sleep(0.2)   # 보드가 ctx를 반영할 시간

    if not a.selftest:
        p = [1.0 - a.p_ice, 0.0, a.p_ice, 0.0]
        r = send_infer(s, addr, 1, p)
        print(f"p_ice={a.p_ice:.2f} → risk={r['risk']:.4f} alarm={r['alarm']} level={r['level']} "
              f"보드={r['board_ns']/1000:.2f}us RTT={r['rtt_us']:.0f}us")
        return

    # ---- 보드 C 구현 vs Pi 파이썬 참조 구현 ----
    from icepredict.pi.fusion import RiskFuser
    from icepredict.common.protocol import Weights
    ref = RiskFuser(Weights(alpha, beta, gamma), threshold=a.threshold)

    cases = [
        ("클래스만 (반사도·차선 미학습)", 0.10, None, None),
        ("클래스만",                      0.44, None, None),
        ("클래스만",                      0.50, None, None),
        ("클래스만",                      0.92, None, None),
        ("세 신호 모두",                   0.60, 0.80, 0.30),
        ("세 신호 모두",                   0.20, 0.10, 0.95),
    ]
    print(f"{'케이스':32s} {'p_ice':>6s} {'보드risk':>9s} {'Pi risk':>9s} {'차':>8s} "
          f"{'보드alarm':>9s} {'Pi alarm':>9s} {'보드us':>7s}")
    bad = 0
    for i, (name, p_ice, spec, lane) in enumerate(cases):
        p = [1.0 - p_ice, 0.0, p_ice, 0.0]
        r = send_infer(s, addr, i, p,
                       NAN if spec is None else spec,
                       NAN if lane is None else lane)
        e = ref.fuse(p, spec=spec, lane=lane)
        d = abs(r["risk"] - e.risk)
        ok = d < 1e-4 and r["alarm"] == e.alarm
        if not ok: bad += 1
        print(f"{name:32s} {p_ice:6.2f} {r['risk']:9.4f} {e.risk:9.4f} {d:8.1e} "
              f"{str(r['alarm']):>9s} {str(e.alarm):>9s} {r['board_ns']/1000:7.2f}"
              + ("" if ok else "   ← 불일치"))
    print()
    if bad:
        print(f"불일치 {bad}건 — 보드 C 구현과 fusion.py가 어긋난다. 고쳐야 한다.")
        sys.exit(1)
    print(f"전 케이스 일치 ({len(cases)}건). 보드 C 구현과 fusion.py가 같은 값을 낸다.")

if __name__ == "__main__":
    main()
