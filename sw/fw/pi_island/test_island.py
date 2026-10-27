#!/usr/bin/env python3
"""안전 전용 RTOS = ctypes C 코어 (같은 입력 → 같은 출력) + 심장박동이 끊기면 축소 모드로 들어가는지.

입력: 기록된 CARLA 주행의 코어 로그(core_*.csv, 곡선 빙판 s12 고정 게인) — 확정·비상 제어 경로를 모두 지난다.
사용: python3 sw/fw/pi_island/test_island.py   (섬을 직접 띄우고 끈다, 실시간 권한 없이)
"""
import csv
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sw/src"))
from icepredict.pi.slip_ctypes import SlipCoreC  # noqa: E402
from icepredict.pi.slip_island import SlipCoreIsland, MAGIC_HB  # noqa: E402

PORT = 5617
LOG = sorted((ROOT / "logs/gain_sched/runs/s12").glob("core_s12_f0.15_k60_fixed_r0.csv"))


def rows():
    with open(LOG[0]) as f:
        for r in csv.DictReader(f):
            yield [float(r[k]) for k in ("ay", "gz", "speed", "steer_eq", "ax", "brake_cmd", "lane_err", "lat_off", "gap_f")] + [999.0, 999.0, 0.0, 0.0]


def main():
    if not LOG: print("[SKIP] 코어 로그 없음"); return 0
    subprocess.run(["make", "-s", "-C", str(ROOT / "sw/fw/pi_island")], check=True)
    p = subprocess.Popen([str(ROOT / "sw/fw/pi_island/safety_island"), "--udp", str(PORT), "--prio", "0", "--cpu", "-1"],
                         stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    try:
        import socket, struct
        hb = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        isl = SlipCoreIsland(f"127.0.0.1:{PORT}", timeout_s=0.5); time.sleep(0.05)
        ref = SlipCoreC(0.02, 3, 25 / 3.6, variant="mw")
        n = bad = emerg = 0; worst = 0.0
        for x in rows():
            hb.sendto(struct.pack("<II", MAGIC_HB, n), isl.dst)      # 메인 프로그램 살아 있음
            ref.set_host_fresh(True)
            a = isl.board(*x); b = ref.board(*x)
            if a is None: bad += 1; continue
            d = max(abs(a[k] - b[k]) for k in ("slip", "emerg", "brake", "steer", "mode", "mu_hat"))
            worst = max(worst, d); n += 1; emerg += b["emerg"]
            if d > 1e-6: bad += 1
        ok1 = bad == 0 and n > 100 and emerg > 0
        print(f"[{'OK ' if ok1 else 'FAIL'}] 섬 = ctypes: 샘플 {n}개(비상 제어 {emerg}개), 최대 차이 {worst:.2e}, 불일치·무응답 {bad}")
        # 심장박동 끊기 → 5샘플 뒤 축소 모드
        k_deg = None
        for k in range(20):
            a = isl.board(*x)
            if a is not None and isl.degraded(): k_deg = k + 1; break
        ok2 = k_deg is not None and k_deg <= 6
        print(f"[{'OK ' if ok2 else 'FAIL'}] 심장박동 끊김 → 축소 모드: {k_deg} 샘플 뒤")
        rtt = sorted(isl.rtt_ms); print(f"[INFO] 왕복 지연(같은 파이) 중앙 {rtt[len(rtt)//2]:.3f} ms, 99 % {rtt[int(len(rtt)*0.99)]:.3f} ms")
        # 마찰 인식 제동 시점: 섬 = ctypes (결빙 취약 구간 탭 → 빙판 → 예비 제동까지)
        from icepredict.pi.slip_island import EnvelopeIsland
        ei = EnvelopeIsland(f"127.0.0.1:{PORT}", timeout_s=0.5); er = SlipCoreC(0.02, 3, 25 / 3.6, variant="env"); er.env_reset(0.6725)
        worst = 0.0; n_env = 0; modes = set(); mode_r = "CRUISE"
        for i in range(900):
            v = 16.7 - 0.01 * max(0, i - 400); gap = 300.0 - 0.33 * i; zone = i >= 50
            ax = (-2.8 if i < 300 else -1.5) if mode_r == "PROBE" else 0.0
            hb.sendto(struct.pack("<II", MAGIC_HB, i), isl.dst)
            a = ei.step(v, ax, 0.0, gap, 0.0, 0.05, -3.0, 0.0, zone)
            why, _ = er.env_should_probe(0.02, 0.05, -3.0, v, ax, 0.0, 0.0, gap, zone); b = er.env_step(0.02, v, ax, gap, 0.0); mode_r = b["mode"]
            if a is None: worst = 9e9; break
            d = max(abs(a[k] - b[k]) for k in ("brake", "thr_cap", "mu", "d_need")); worst = max(worst, d); modes.add(b["mode"]); n_env += 1
            if a["mode"] != b["mode"]: worst = 9e9; break
        ok3 = worst < 1e-5 and {"PROBE", "PREBRAKE"} <= modes
        print(f"[{'OK ' if ok3 else 'FAIL'}] 섬 제동 시점 = ctypes: 샘플 {n_env}개, 모드 {sorted(modes)}, 최대 차이 {worst:.2e}")
        return 0 if ok1 and ok2 and ok3 else 1
    finally:
        p.terminate(); p.wait()


if __name__ == "__main__":
    sys.exit(main())
