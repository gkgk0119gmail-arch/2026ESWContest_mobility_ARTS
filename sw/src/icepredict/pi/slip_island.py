"""파이 격리 코어 안전 섬(sw/fw/pi_island/safety_island.c)과 UDP 로 주고받는 클라이언트.

SlipCoreC(ctypes) 와 같은 board() 인터페이스를 낸다 — 데모는 `--slip-island HOST:PORT` 로 바꿔 끼우기만 한다.
CARLA(플랜트)가 IMU 를 보내는 쪽이다. 실차에서는 IMU 가 안전 섬에 직결된다.
파이 메인 프로그램의 심장박동은 별도 프로세스(heartbeat())가 보낸다 — 그 프로세스를 멈추면(SIGSTOP) 진짜 고장 주입이다.
섬이 timeout 안에 답하지 않으면 board() 는 None 을 돌려준다 (호출 쪽이 '2차 방어 응답 없음'으로 처리).
"""
from __future__ import annotations

import socket
import struct
import time

MAGIC_IMU, MAGIC_HB, MAGIC_OUT, MAGIC_RST = 0x314C5349, 0x31304248, 0x314F5349, 0x54455352
_IMU = struct.Struct("<II13f")
_OUT = struct.Struct("<II12ffI")


class SlipCoreIsland:
    def __init__(self, addr: str, timeout_s: float = 0.015, min_speed: float = 25 / 3.6):
        host, port = addr.rsplit(":", 1)
        self.dst = (host, int(port))
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.settimeout(timeout_s)
        self.seq = 0
        self.flags = 0x2 | 0x8 | 0x1         # 섬 빌드: 타원 + 마찰 추정 + 감시
        self._deg = False
        self.n_timeout = 0
        self.rtt_ms: list[float] = []
        self.compute_us: list[float] = []
        self.s.sendto(struct.pack("<IIf", MAGIC_RST, 0, min_speed), self.dst)   # 섬 상태 초기화 + 판정 최소 속도

    def set_host_fresh(self, fresh: bool):   # 섬은 실제 심장박동 패킷으로 판단한다 — 여기서는 아무것도 안 한다
        pass

    def degraded(self) -> bool:
        return self._deg

    def board(self, ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v):
        self.seq += 1
        t0 = time.perf_counter()
        self.s.sendto(_IMU.pack(MAGIC_IMU, self.seq, ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off,
                                gf, gl, gr, ice_off, front_rel_v), self.dst)
        while True:
            try:
                b, _ = self.s.recvfrom(256)
            except socket.timeout:
                self.n_timeout += 1
                return None
            if len(b) < _OUT.size: continue
            mg, seq, *rest = _OUT.unpack(b[:_OUT.size])
            if mg != MAGIC_OUT or seq != self.seq: continue      # 늦게 온 이전 답은 버린다
            o, cu, deg = rest[:12], rest[12], rest[13]
            self.rtt_ms.append((time.perf_counter() - t0) * 1000); self.compute_us.append(cu); self._deg = bool(deg)
            return dict(slip=int(o[0]), trig=int(o[1]), ay_g=o[2], yaw_err=o[3], emerg=int(o[4]), brake=o[5], steer=o[6],
                        mode=int(o[7]), grip=int(o[8]), mu_hat=o[9], mu_n=int(o[10]), a_stop=o[11])


def heartbeat(addr: str, hz: float = 50.0):
    """파이 메인 프로그램 대역: 주기적으로 심장박동을 보낸다. 이 프로세스를 SIGSTOP 하면 파이가 멈춘 것과 같다."""
    host, port = addr.rsplit(":", 1); dst = (host, int(port))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); n = 0; period = 1.0 / hz; nxt = time.monotonic()
    while True:
        n += 1; s.sendto(struct.pack("<II", MAGIC_HB, n), dst)
        nxt += period; time.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="파이 메인 프로그램 심장박동 대역")
    ap.add_argument("addr"); ap.add_argument("--hz", type=float, default=50.0)
    a = ap.parse_args(); heartbeat(a.addr, a.hz)
