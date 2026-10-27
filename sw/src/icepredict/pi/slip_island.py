"""파이 격리 코어(sw/fw/pi_island/safety_island.c)와 UDP 로 주고받는 클라이언트.

SlipCoreC(ctypes) 와 같은 board() 인터페이스를 낸다 — 데모는 `--slip-island HOST:PORT` 로 바꿔 끼우기만 한다.
CARLA(플랜트)가 IMU 를 보내는 쪽이다. 실차에서는 IMU 가 안전 전용 RTOS에 직결된다.
파이 메인 프로그램의 심장박동은 별도 프로세스(heartbeat())가 보낸다 — 그 프로세스를 멈추면(SIGSTOP) 진짜 고장 주입이다.
섬이 timeout 안에 답하지 않으면 board() 는 None 을 돌려준다 (호출 쪽이 '2차 방어 응답 없음'으로 처리).
"""
from __future__ import annotations

import socket
import struct
import time

MAGIC_IMU, MAGIC_HB, MAGIC_OUT, MAGIC_RST = 0x314C5349, 0x31304248, 0x314F5349, 0x54455352
MAGIC_ENV, MAGIC_EOUT = 0x31564E45, 0x314F4E45
_ENV = struct.Struct("<II8fIII")
_EOUT = struct.Struct("<II9fIIf")
ENV_MODES = {0: "CRUISE", 1: "PROBE", 2: "CAUTION", 3: "PREBRAKE", 4: "BRAKE", 5: "HOLD"}
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


class EnvelopeIsland:
    """안전 전용 RTOS의 마찰 인식 제동 시점(SLIP_ENVELOPE)과 주고받는다. step() 이 None 이면 섬 무응답.
    addr = "HOST:PORT" 면 그쪽으로 보낸다. addr = "listen:PORT" 면 이 포트에서 섬의 인사 패킷(HELO)을 기다렸다가
    그 출발 주소로 보낸다 — 캠퍼스 망처럼 이쪽→파이 방향이 막힌 곳용 (섬은 --hello 이쪽IP:PORT)."""

    def __init__(self, addr: str, timeout_s: float = 0.03, wait_hello_s: float = 30.0):
        host, port = addr.rsplit(":", 1)
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if host == "listen":
            self.s.bind(("0.0.0.0", int(port))); self.s.settimeout(wait_hello_s)
            while True:
                b, src = self.s.recvfrom(64)
                if len(b) >= 4 and struct.unpack("<I", b[:4])[0] == 0x4F4C4548: self.dst = src; break
        else:
            self.dst = (host, int(port))
        self.s.settimeout(timeout_s)
        self.seq = 0; self._reset = 1; self.n_timeout = 0; self.rtt_ms: list[float] = []

    def reset(self):
        self._reset = 1

    def step(self, v, ax, ay, gap, v_lead=0.0, risk=-1.0, temp_c=-3.0, driver_brake=0.0, zone=False, dry_only=False):
        self.seq += 1; t0 = time.perf_counter()
        self.s.sendto(_ENV.pack(MAGIC_ENV, self.seq, v, ax, ay, gap, v_lead, risk, temp_c, driver_brake,
                                1 if zone else 0, 1 if dry_only else 0, self._reset), self.dst)
        self._reset = 0
        while True:
            try:
                b, _ = self.s.recvfrom(256)
            except socket.timeout:
                self.n_timeout += 1; return None
            if len(b) < _EOUT.size: continue
            mg, seq, *r = _EOUT.unpack(b[:_EOUT.size])
            if mg != MAGIC_EOUT or seq != self.seq: continue
            o = r[:9]; self.rtt_ms.append((time.perf_counter() - t0) * 1000)
            return dict(brake=o[0], thr_cap=o[1], mode=ENV_MODES[int(o[2])], mu=o[3], d_stop=o[4], d_need=o[5], v_max=o[6],
                        mu_probe=o[7], a_used=o[8], probe_by={0: None, 1: "camera", 2: "zone"}[r[9]], degraded=bool(r[10]), compute_us=r[11])


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
