#!/usr/bin/env python3
"""플래그 0 회귀 시험: 게인 스케줄링 작업 전 slip_core.h(커밋 785ccc1)와 지금 slip_core.h 를
SLIP_MU_EST=0 · SLIP_GAIN_SCHED=0(기본)으로 각각 컴파일해, 같은 입력에 대한 출력이 **비트 단위로 같은지** 본다.
공개 API(slip_reset/slip_step2/slip_control2)만 쓰는 드라이버로 펌웨어 IMU 분기와 같은 순서로 부른다.
사용: python3 sw/fw/npu_lib/test_flag0_regression.py [기준 커밋]"""
import pathlib, subprocess, sys, tempfile
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "785ccc1"
DRV = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "slip_core.h"
int main(void) {
  slipdet_t d; slip_reset(&d, 0.02f, 3, 2.0f);
  float in[13]; unsigned long n = 0;
  while (fread(in, sizeof(float), 13, stdin) == 13) {
    uint8_t trig = 0; float ayg = 0.f, ye = 0.f, b = 0.f, s = 0.f;
    int slip = slip_step2(&d, in[0], in[1], in[2], in[3], in[4], in[5], &trig, &ayg, &ye);
    if (slip) d.emerg = 1;
    int em = d.emerg;
    if (em) slip_control2(&d, in[2], in[1], in[6], in[7], in[8], in[9], in[10], d.kax.x0, in[5], in[11], in[12], &b, &s);
    float out[8] = {(float)slip, (float)trig, ayg, ye, (float)em, b, s, (float)d.mode};
    fwrite(out, sizeof(float), 8, stdout);
    if (++n % 900 == 0) slip_reset(&d, 0.02f, 3, 2.0f);           /* 주행 하나 = 18 s */
  }
  return 0;
}
'''

def build(hdr_text, td, name):
    d = pathlib.Path(td) / name; d.mkdir()
    (d / "slip_core.h").write_text(hdr_text); (d / "drv.c").write_text(DRV)
    if (HERE / "slip_gain_lut.h").exists(): (d / "slip_gain_lut.h").write_text((HERE / "slip_gain_lut.h").read_text())
    subprocess.run(["gcc", "-O2", "-ffp-contract=off", "-I", str(d), str(d / "drv.c"), "-o", str(d / "drv"), "-lm"], check=True)
    return d / "drv"

def scenarios(n_runs=60, seed=1):
    rng = np.random.default_rng(seed); X = []
    for r in range(n_runs):
        n = 900; t = np.arange(n) * 0.02
        v = np.clip(rng.uniform(8, 17) - np.maximum(t - rng.uniform(3, 6), 0) * rng.uniform(0.2, 3), 0, None)
        st = rng.uniform(-0.05, 0.05) * np.sin(t * rng.uniform(0.3, 1.5))
        t0 = rng.uniform(1.5, 5); sgn = rng.choice([-1, 1]); s = t >= t0
        ay = rng.normal(0, 0.3, n); gz = rng.normal(0, 0.02, n)
        ay[s] += sgn * rng.uniform(2, 5) * np.clip((t[s] - t0) / 0.2, 0, 1); gz[s] += sgn * rng.uniform(0.2, 0.6) * np.clip((t[s] - t0) / 0.2, 0, 1)
        ax = np.where(t > t0, rng.uniform(-6, -0.2), -0.05) + rng.normal(0, 0.1, n)
        br = np.where(t > t0 + 0.1, rng.choice([0.35, 0.7, 0.95]), 0.0) * (rng.random(n) > 0.1)
        le = rng.normal(0, 0.1, n); lo = rng.normal(0, 0.6, n)
        gf = np.where(rng.random() < 0.5, np.linspace(rng.uniform(5, 60), -3, n), 999.0)
        gl = np.full(n, rng.choice([-1.0, 5.0, 999.0])); gr = np.full(n, rng.choice([-1.0, 5.0, 999.0]))
        ice = np.full(n, rng.choice([0.0, 3.5, -3.5])); frv = np.full(n, rng.choice([0.0, 3.0, -2.0]))
        X.append(np.stack([ay, gz, v, st, ax, br, le, lo, gf, gl, gr, ice, frv], 1))
    return np.concatenate(X).astype(np.float32)

base_hdr = subprocess.run(["git", "show", f"{BASE}:sw/fw/npu_lib/slip_core.h"], capture_output=True, text=True, check=True, cwd=HERE).stdout
X = scenarios()
with tempfile.TemporaryDirectory() as td:
    a = build(base_hdr, td, "base"); b = build((HERE / "slip_core.h").read_text(), td, "now")
    oa = np.frombuffer(subprocess.run([str(a)], input=X.tobytes(), capture_output=True, check=True).stdout, dtype=np.float32)
    ob = np.frombuffer(subprocess.run([str(b)], input=X.tobytes(), capture_output=True, check=True).stdout, dtype=np.float32)
same = oa.shape == ob.shape and oa.tobytes() == ob.tobytes()
n_em = int((oa.reshape(-1, 8)[:, 4] > 0).sum()) if oa.size else 0
print(f"[{'OK ' if same else 'FAIL'}] 플래그 0 회귀: 기준 {BASE} vs 현재 — 샘플 {len(X)}개(비상 제어 {n_em}개), 출력 {'비트 단위 동일' if same else '다름'}")
if not same and oa.shape == ob.shape:
    d = np.flatnonzero(oa != ob); print("  첫 차이 위치", d[:5], oa[d[:5]], ob[d[:5]])
sys.exit(0 if same else 1)
