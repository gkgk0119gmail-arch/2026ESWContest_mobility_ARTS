#!/usr/bin/env python3
"""slip_core.h(C) 와 imu_slip.py(파이썬 참조) 동치 검증 — 호스트 gcc 로 컴파일해 같은 합성 시나리오를 먹인다.
확정 시각·트리거·오탐 수가 같아야 한다. 사용: python3 sw/fw/npu_lib/test_slip_core.py"""
import subprocess, sys, tempfile, pathlib, numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.imu_slip import SlipDetector, synth_run

DRIVER = r'''
#include <stdio.h>
#include "slip_core.h"
int main(int argc, char **argv) {
  float dt = atof(argv[1]); int confirm = atoi(argv[2]); float minv = atof(argv[3]);
  slipdet_t d; slip_reset(&d, dt, confirm, minv);
  float ay, gz, sp, st; int i = 0;
  while (scanf("%f %f %f %f", &ay, &gz, &sp, &st) == 4) {
    uint8_t trig; float ayg, ye, brk, stc;
    if (slip_step(&d, ay, gz, sp, st, &trig, &ayg, &ye, &brk, &stc)) {
      printf("%d %u %.4f %.4f %.2f %.4f\n", i, trig, ayg, ye, brk, stc);
      d.emerg = 1;
      for (int k = 0; k < 40; k++) {           /* 제어기 40샘플: 조향 유계, 제동 펄스 */
        float b, s2; slip_control(&d, 10.0f - 0.2f * k, gz, 0.05f, 0.3f, 999.f, 999.f, 999.f, &b, &s2);
        if (!(b >= 0.15f && b <= 1.0f) || !(s2 >= -0.5f && s2 <= 0.5f)) { fprintf(stderr, "CTRL OUT OF RANGE b=%f s=%f\n", b, s2); return 2; }
      }
      if (d.mode != SLIP_MODE_LANEKEEP) { fprintf(stderr, "expected LANEKEEP got %u\n", d.mode); return 3; }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* 앞차 10m, 오른쪽 비어 있음 → 회피 R */
        slip_control(&e, 11.f, 0.f, 0.f, 0.f, 10.f, 5.f, 999.f, &b, &s2);
        if (e.mode != SLIP_MODE_EVADE_R || s2 <= 0.f) { fprintf(stderr, "expected EVADE_R got %u steer %f\n", e.mode, s2); return 4; } }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* 앞차 10m, 오른쪽 차로 없음, 왼쪽 비어 있음 → 회피 L */
        slip_control(&e, 11.f, 0.f, 0.f, 0.f, 10.f, 999.f, -1.f, &b, &s2);
        if (e.mode != SLIP_MODE_EVADE_L || s2 >= 0.f) { fprintf(stderr, "expected EVADE_L got %u\n", e.mode); return 5; } }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* 앞차 10m, 양쪽 막힘 → HARDSTOP (강한 제동) */
        slip_control(&e, 11.f, 0.f, 0.f, 0.f, 10.f, 5.f, 8.f, &b, &s2);
        if (e.mode != SLIP_MODE_HARDSTOP || b < 0.35f) { fprintf(stderr, "expected HARDSTOP got %u b %f\n", e.mode, b); return 6; } }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* v7: 빙판 차로가 오른쪽(+3.5) → 오른쪽 비어도 왼쪽으로 회피 */
        slip_control2(&e, 11.f, 0.f, 0.f, 0.f, 10.f, 999.f, 999.f, 0.f, 0.5f, +3.5f, 0.f, &b, &s2);
        if (e.mode != SLIP_MODE_EVADE_L) { fprintf(stderr, "v7 expected EVADE_L away from ice lane, got %u\n", e.mode); return 9; } }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* v7: 앞차가 멀어지면(+3 m/s) 회피 안 함 */
        slip_control2(&e, 11.f, 0.f, 0.f, 0.f, 10.f, 999.f, 999.f, 0.f, 0.5f, 0.f, +3.f, &b, &s2);
        if (e.mode != SLIP_MODE_LANEKEEP) { fprintf(stderr, "v7 expected LANEKEEP for receding car, got %u\n", e.mode); return 10; } }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* v7: 이미 겹침(gap<0) → 최대 제동 */
        slip_control2(&e, 11.f, 0.f, 0.f, 0.f, -2.f, 999.f, 999.f, 0.f, 0.5f, 0.f, 0.f, &b, &s2);
        if (e.mode != SLIP_MODE_HARDSTOP) { fprintf(stderr, "v7 expected HARDSTOP on overlap, got %u\n", e.mode); return 11; } }
      { slipdet_t e = d; e.mode = SLIP_MODE_NONE; float b, s2;                       /* v7: 접지 회복(감속 -6, 5샘플) 후엔 회피 없이 HARDSTOP, 제동 1.0 */
        for (int k = 0; k < 6; k++) slip_control2(&e, 11.f, 0.f, 0.f, 0.f, 999.f, 999.f, 999.f, -6.f, 0.8f, 0.f, 0.f, &b, &s2);
        slip_control2(&e, 11.f, 0.f, 0.f, 0.f, 10.f, 999.f, 999.f, -6.f, 0.8f, 0.f, 0.f, &b, &s2);
        if (!e.grip || e.mode != SLIP_MODE_HARDSTOP || b < 0.99f) { fprintf(stderr, "v7 expected grip HARDSTOP b=1, got grip=%d mode=%u b=%f\n", e.grip, e.mode, b); return 12; } }
      { slipdet_t e; slip_reset(&e, dt, 3, 2.f); uint8_t tg; float a1, a2; int fired = 0, at = -1;   /* 저마찰: 제동 0.5인데 감속 0.3 → 0.3s 지속 후에만 확정 */
        for (int k = 0; k < 60 && !fired; k++) { fired = slip_step2(&e, 0.f, 0.f, 11.f, 0.f, -0.3f, 0.5f, &tg, &a1, &a2); if (fired) at = k; }
        if (!fired || tg != SLIP_TRIG_LOWMU || at < SLIP_LOWMU_HOLD) { fprintf(stderr, "expected LOWMU after hold, got fired=%d at=%d\n", fired, at); return 7; } }
      { slipdet_t e; slip_reset(&e, dt, 3, 2.f); uint8_t tg; float a1, a2; int fired = 0;   /* 마른 노면 제동: 처음 10샘플 감속 0(필터 지연) 뒤 -6 m/s² → 발화 금지 */
        for (int k = 0; k < 60 && !fired; k++) fired = slip_step2(&e, 0.f, 0.f, 11.f, 0.f, k < 10 ? 0.f : -6.f, 1.0f, &tg, &a1, &a2);
        if (fired) { fprintf(stderr, "false LOWMU on dry braking\n"); return 8; } }
    }
    i++;
  }
  return 0;
}
'''
def run_c(exe, dt, confirm, minv, ay, gz, sp, st):
    data = "\n".join(f"{a:.6f} {g:.6f} {s:.6f} {t:.6f}" for a, g, s, t in zip(ay, gz, sp, st))
    out = subprocess.run([exe, str(dt), str(confirm), str(minv)], input=data, capture_output=True, text=True, check=True).stdout
    return [tuple(float(x) for x in l.split()) for l in out.strip().splitlines() if l.strip()]

def run_py(dt, confirm, minv, ay, gz, sp, st):
    det = SlipDetector(rate_hz=1.0 / dt, confirm_samples=confirm, min_speed_mps=minv)
    evs = []
    for i in range(len(ay)):
        ev = det.step(i * dt, float(ay[i]), float(gz[i]), float(sp[i]), float(st[i]))
        if ev: evs.append((i, 1 if ev.trigger == "lat_acc" else 2, ev.ay_g, ev.yaw_err))
    return evs

fails = 0
with tempfile.TemporaryDirectory() as td:
    src = pathlib.Path(td) / "drv.c"; src.write_text('#include <stdlib.h>\n' + DRIVER)
    exe = pathlib.Path(td) / "drv"
    subprocess.run(["gcc", "-O2", "-I", str(pathlib.Path(__file__).parent), str(src), "-o", str(exe), "-lm"], check=True)
    cases = [("1kHz 합성", 1000.0, 5, 2.0, dict()), ("50Hz 데모율", 50.0, 3, 6.94, dict()),
             ("50Hz 노이즈2배", 50.0, 3, 6.94, dict(noise_ay=0.8, noise_gz=0.04, seed=3)),
             ("50Hz 언더스티어(음의 yaw)", 50.0, 3, 6.94, dict(seed=5))]
    for name, hz, confirm, minv, kw in cases:
        t, ay, gz, v, steer = synth_run(rate_hz=hz, **kw)
        if "언더" in name: gz = 2 * SlipDetector(rate_hz=hz).expected_yaw(v, steer) - gz
        sp = np.full_like(ay, v); st = np.full_like(ay, steer)
        c = run_c(str(exe), 1.0 / hz, confirm, minv, ay, gz, sp, st); p = run_py(1.0 / hz, confirm, minv, ay, gz, sp, st)  # C 드라이버는 제어기 시나리오도 함께 검사한다 (실패 시 예외)
        same = len(c) == len(p) and all(int(a[0]) == b[0] and int(a[1]) == b[1] and abs(a[2] - b[2]) < 1e-3 and abs(a[3] - b[3]) < 1e-3 for a, b in zip(c, p))
        fails += not same
        print(f"[{'OK ' if same else 'FAIL'}] {name}: C {[(int(a[0]), int(a[1])) for a in c]}  py {[(b[0], b[1]) for b in p]}"
              + (f"  (C 첫 확정 {c[0][0]/hz:.3f}s ay_g {c[0][2]:.3f} brake {c[0][4]} steer {c[0][5]:+.3f})" if c else ""))
    # 정상 주행(미끄러짐 없음) 오탐 0 확인
    t, ay, gz, v, steer = synth_run(rate_hz=50.0, slip_at_s=99.0, noise_ay=0.8, noise_gz=0.04, seed=11)
    c = run_c(str(exe), 0.02, 3, 6.94, ay, gz, np.full_like(ay, v), np.full_like(ay, steer))
    print(f"[{'OK ' if not c else 'FAIL'}] 정상 주행 오탐: {len(c)}건"); fails += bool(c)


# ---- v7 보드 경로 동치: C(ctypes, sc_board) = 파이썬 slip_ref.SlipCoreRef ----------------------------------
# 미끄러짐 확정 → 비상 제어 전 구간을 샘플마다 비교한다. 접지 회복·겹침·앞차 멀어짐·빙판 차로 회피 금지가 다 지나가게
# 시나리오를 만든다. 이산값(slip·trig·emerg·mode·grip)은 정확히, 연속값은 1e-5 안에서 같아야 한다.
from icepredict.pi.slip_ref import SlipCoreRef, LocalEmergency7
from icepredict.pi.slip_ctypes import SlipCoreC
def _lut():
    from icepredict.pi.gain_lut import GainLUT
    return GainLUT.from_header(pathlib.Path(__file__).parent / "slip_gain_lut.h")

def board_scenario(kind, seed, n=600, dt=0.02):
    rng = np.random.default_rng(seed)
    t = np.arange(n) * dt
    v = np.clip(11.0 - np.maximum(t - 4.0, 0) * 1.2, 0.0, None)          # 4 s 뒤부터 감속
    steer = 0.02 * np.sin(t * 0.7)
    ay = rng.normal(0, 0.3, n); gz = rng.normal(0, 0.015, n)
    slip = t >= 2.0
    ay[slip] += -3.5 * np.clip((t[slip] - 2.0) / 0.2, 0, 1); gz[slip] += -0.4 * np.clip((t[slip] - 2.0) / 0.2, 0, 1)
    ax = np.where(t > 2.1, -0.6, -0.05) + rng.normal(0, 0.1, n)
    brake = np.where(t > 2.1, 0.6, 0.0)
    gf = np.full(n, 999.0); gl = np.full(n, 999.0); gr = np.full(n, 999.0); ice = np.zeros(n); frv = np.zeros(n)
    lane_err = 0.05 * np.sin(t); lat_off = 0.3 * np.cos(t * 0.5)
    if kind == "grip":     ax[t > 3.0] = -6.0 + rng.normal(0, 0.2, (t > 3.0).sum()); gf[t > 3.5] = 8.0
    if kind == "overlap":  gf[t > 2.3] = -1.5
    if kind == "receding": gf[:] = 12.0; frv[:] = 3.0
    if kind == "ice_lane": gf[:] = 10.0; ice[:] = 3.5
    if kind == "evade":    gf[:] = np.linspace(40, 5, n); gl[:] = 5.0          # 오른쪽만 비어 있다 → 우회피
    if kind == "lowmu":    ay[:] = rng.normal(0, 0.3, n); gz[:] = rng.normal(0, 0.015, n); brake[:] = np.where(t > 1.0, 0.5, 0.0); ax[:] = np.where(t > 1.0, -0.3, 0.0)
    return np.stack([ay, gz, v, steer, ax, brake, lane_err, lat_off, gf, gl, gr, ice, frv], 1)

def compare_board(kind, seed, variant=""):
    X = board_scenario(kind, seed)
    c = SlipCoreC(0.02, 3, 2.0, variant=variant)
    p = SlipCoreRef(0.02, 3, 2.0, mu_est=variant in ("mu", "gs"), lut=_lut() if variant == "gs" else None)
    worst, modes, fb, mun = 0.0, set(), None, 0
    for i, row in enumerate(X):
        row = row.copy()
        if fb is not None: row[5] = fb                                   # 폐루프: 지난 샘플 제동 명령이 지금 걸린 제동이다
        a = c.board(*row.tolist()); b = p.board(*row.tolist())
        fb = a["brake"] if a["emerg"] else None; mun = max(mun, a["mu_n"])
        for k in ("slip", "trig", "emerg", "mode", "grip"):
            if a[k] != b[k]: return False, f"샘플 {i} {k}: C {a[k]} py {b[k]}", modes
        for k in ("ay_g", "yaw_err", "brake", "steer", "a_stop", "mu_hat", "mu_n"):
            worst = max(worst, abs(a[k] - b[k]))
        modes.add(a["mode"])
    # 회피 이동 중에는 제동이 0.25 로 고정돼 '강' 펄스가 없다 → 마찰 추정 샘플 0 이 설계대로다
    return worst < 1e-5 and (variant == "" or kind == "evade" or mun > 0), f"최대 오차 {worst:.2e}, 마찰 추정 샘플 {mun}", modes

need = {"grip": {4}, "overlap": {4}, "receding": {1}, "ice_lane": {2}, "evade": {3}, "plain": {1}, "lowmu": {1}}
for kind in need:
    for seed in (1, 2, 3):
        ok, msg, modes = compare_board(kind, seed)
        ok = ok and need[kind] <= modes
        fails += not ok
        print(f"[{'OK ' if ok else 'FAIL'}] v7 보드 경로 {kind:9s} seed {seed}: {msg}  모드 {sorted(modes)}")
# SLIP_MU_EST=1 변형도 같은 시나리오에서 C = 파이썬 (마찰 추정값·샘플 수 포함)
for kind in ("plain", "grip", "evade", "lowmu"):
    ok, msg, modes = compare_board(kind, 1, "mu")
    fails += not ok
    print(f"[{'OK ' if ok else 'FAIL'}] SLIP_MU_EST 보드 경로 {kind:9s}: {msg}")
# LocalEmergency7(데모 폴백) 이 같은 제어를 내는지 — 확정 이후 구간만
X = board_scenario("evade", 4); c = SlipCoreC(0.02, 3, 2.0); le = None; worst = 0.0
for row in X:
    a = c.board(*row.tolist())
    if a["emerg"] and le is None:
        le = LocalEmergency7(0.02); le.core.kax = __import__("copy").deepcopy(SlipCoreRef(0.02).kax)
        ref = SlipCoreRef(0.02, 3, 2.0)
        for r2 in X: 
            o = ref.board(*r2.tolist())
            if o["emerg"]: break
        le.core.kax = ref.kax; le.core.n_emerg = ref.n_emerg - 1; le.core.mode = 0
        b_, s_, _ = le.core.control2(row[2], row[1], row[6], row[7], row[8], row[9], row[10], ref.kax.x0, row[5], row[11], row[12]), 0, 0
        worst = max(worst, abs(b_[0] - a["brake"]), abs(b_[1] - a["steer"]))
ok = le is not None and worst < 1e-5
fails += not ok
print(f"[{'OK ' if ok else 'FAIL'}] LocalEmergency7 첫 제어 샘플 = C: 오차 {worst:.2e}")
sys.exit(1 if fails else 0)
