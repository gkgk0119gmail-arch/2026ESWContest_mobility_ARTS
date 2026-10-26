#!/usr/bin/env python3
"""SLIP_ENVELOPE (slip_envelope.h) 단위 시험 — ctypes 로 보드와 같은 C 를 부른다.

1. 노면 확인 탭: 표의 각 μ 에서 '제동 0.3 실측 감속'을 넣으면 그 μ 를 되돌린다 (±2 %), 마른 노면 감속이면 μ 를 바꾸지 않는다
2. 탭 측정 실패(표 최저보다 덜 감속)면 한 번 다시 잰다
3. 정지 거리·필요 거리 = 지연 0.3 s + v²/(2·0.9·a(μ)) (+ 여유 4 m), 필요 거리 안이면 전력 제동
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sw/src"))
from icepredict.pi.slip_ctypes import SlipCoreC  # noqa: E402

mt = json.loads((ROOT / "sw/docs/data/gain_sched/mu_table.json").read_text())["table"]
rows = sorted((v["mu_eff"], v["decel_by_brake"][0], v["decel_by_brake"][3]) for v in mt.values())
ok_all = True


def check(name, ok, msg):
    global ok_all
    ok_all &= ok
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: {msg}")


def tap(dec, n=35):
    c = SlipCoreC(variant="env"); c.env_reset(0.6725); c.env_probe()
    for i in range(n):
        o = c.env_step(0.02, 17.0, -dec if i >= 10 else 0.0, 500.0, 0.0)
    return c, o


errs = []
for mu, d_probe, _ in rows[:-1]:
    if d_probe >= 0.92 * rows[-1][1]: continue          # 마른 노면과 구분이 안 되는 칸 (μ 0.41)
    _, o = tap(d_probe); errs.append(abs(o["mu"] / mu - 1))
check("탭 → μ", max(errs) < 0.02, f"{len(errs)}칸, 최대 오차 {max(errs)*100:.2f} %")
_, o = tap(rows[-1][1]); check("마른 노면 탭", abs(o["mu"] - 0.6725) < 1e-6 and o["mu_probe"] > 0.6, f"μ {o['mu']:.4f} 유지")
c, o = tap(0.05, 32); retry = o["mode"] == "PROBE"
for i in range(35): o = c.env_step(0.02, 17.0, -rows[3][1], 500.0, 0.0)
check("탭 실패 → 재측정", retry and abs(o["mu"] / rows[3][0] - 1) < 0.02, f"재측정 μ {o['mu']:.3f} (참 {rows[3][0]:.3f})")
c = SlipCoreC(variant="env"); c.env_reset(rows[5][0]); v = 16.67
o = c.env_step(0.02, v, 0.0, 500.0, 0.0)
ds = v * 0.3 + v * v / (2 * 0.9 * rows[5][2])
check("정지 거리 식", abs(o["d_stop"] - ds) < 0.05 and abs(o["d_need"] - ds - 4.0) < 0.05, f"{o['d_stop']:.2f} m (식 {ds:.2f} m)")
o = c.env_step(0.02, v, 0.0, ds + 3.0, 0.0); check("필요 거리 안 → 전력 제동", o["mode"] == "BRAKE" and o["brake"] == 1.0, f"모드 {o['mode']}")

# 4. 탭 판단 (env_should_probe)
def run_probe_logic(risk_fn, zone_fn, temp=-3.0, n=600, dec_fn=lambda i: 2.8):
    c = SlipCoreC(variant="env"); c.env_reset(0.6725); taps = []; mode = "CRUISE"
    for i in range(n):
        why, blk = c.env_should_probe(0.02, risk_fn(i), temp, 16.7, 0.0, 0.0, 0.0, 500.0, zone_fn(i))
        if why: taps.append((i, why))
        o = c.env_step(0.02, 16.7, -dec_fn(i) if mode == "PROBE" else 0.0, 500.0, 0.0); mode = o["mode"]
    return taps, o
taps, _ = run_probe_logic(lambda i: 0.45 if i >= 10 else 0.1, lambda i: False)
want = 10 + 10 + int(round(19.0 / 16.7 / 0.02))          # 회색 0.2 s + 카메라가 본 지점까지 19 m
check("카메라 계기", bool(taps) and taps[0][1] == "camera" and abs(taps[0][0] - want) <= 2, f"첫 탭 {taps[:1]} (기대 약 {want})")
taps, _ = run_probe_logic(lambda i: 0.45, lambda i: False, temp=8.0)
check("따뜻하면 탭 없음", not taps, f"탭 {len(taps)}회")
taps, o = run_probe_logic(lambda i: 0.05, lambda i: i >= 50, dec_fn=lambda i: 2.8 if i < 300 else 1.57)
check("결빙 취약 구간 계기", [w for _, w in taps] == ["zone"] * 3 and abs(o["mu"] / 0.164 - 1) < 0.03,
      f"탭 {taps}, 찾은 μ {o['mu']:.3f}")
taps, o = run_probe_logic(lambda i: 0.05, lambda i: i >= 50)
c = SlipCoreC(variant="env"); c.env_reset(0.6725); c.env_probe(); n_probe = 0
for i in range(40):
    o = c.env_step(0.02, 16.7, -2.8 if i >= 10 else -1.9, 500.0, 0.0); n_probe += o["mode"] == "PROBE"
check("마른 노면 탭 일찍 끝내기", n_probe <= 14 and abs(o["mu"] - 0.6725) < 1e-6, f"탭 {n_probe * 20} ms (전체 600 ms)")
check("구간이어도 마른 노면이면 μ 유지·최대 3번", len(taps) == 3 and abs(o["mu"] - 0.6725) < 1e-6, f"탭 {len(taps)}회, μ {o['mu']:.3f}")
c = SlipCoreC(variant="env"); c.env_reset(0.6725)
for i in range(5): c.env_should_probe(0.02, 0.7, -3.0, 16.7, 0.0, 0.0, 0.0, 500.0, False)
check("카메라 확신 → 빙판 기본값", abs(c.env_step(0.02, 16.7, 0.0, 500.0, 0.0)["mu"] - 0.10) < 1e-6, "μ 0.10")
sys.exit(0 if ok_all else 1)
