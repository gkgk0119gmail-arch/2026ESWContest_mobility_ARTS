"""시연용 '라즈베리파이 메인 프로그램' 대역.

실제 메인 프로그램(1차 방어 맥락·브리지)이 하는 일 중 안전 섬과 관련된 것만 한다:
  · 안전 섬에 50 Hz 심장박동(HB01)을 보낸다 — 끊기면 섬은 카메라·지도 입력을 믿지 않는 축소 모드로 간다
  · 시연 PC 에 0.5 s 마다 인사(HELO)를 보내고, 그쪽에서 KILL 이 오면 **스스로 SIGKILL** 한다 (진짜 프로세스 죽음)
데스크톱→파이 방향이 막힌 망에서도 인사 덕분에 KILL 이 들어온다.
사용: python3 -m icepredict.pi.pi_main --island 127.0.0.1:5600 --hello <데스크톱IP>:5701
"""
from __future__ import annotations

import argparse
import os
import select
import signal
import socket
import struct
import time

MAGIC_HB = 0x31304248


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--island", default="127.0.0.1:5600"); ap.add_argument("--hello", required=True)
    a = ap.parse_args()
    ih, ip = a.island.rsplit(":", 1); hh, hp = a.hello.rsplit(":", 1)
    isl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); ctl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 0; nxt = time.monotonic(); t_hello = 0.0
    print(f"[pi_main] pid {os.getpid()} — 심장박동 → {a.island}, 인사 → {a.hello}", flush=True)
    while True:
        now = time.monotonic()
        if now >= nxt:
            n += 1; isl.sendto(struct.pack("<II", MAGIC_HB, n), (ih, int(ip))); nxt += 0.02
        if now - t_hello > 0.5:
            ctl.sendto(b"HELO", (hh, int(hp))); t_hello = now
        r, _, _ = select.select([ctl], [], [], max(0.0, nxt - time.monotonic()))
        if r:
            b, _ = ctl.recvfrom(64)
            if b[:4] == b"KILL":
                print("[pi_main] KILL 받음 — 스스로 SIGKILL", flush=True)
                os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    main()
