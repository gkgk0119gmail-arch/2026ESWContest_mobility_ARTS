#!/usr/bin/env python3
"""게인 스케줄링 연산량 비교 (리눅스, Pi 5 A76) — bench_slip_linux.c 를 세 구성으로 빌드해 같은 입력으로 잰다.
  고정 게인(보드 지금) / SLIP_MU_EST=1 / SLIP_MU_EST=1 + SLIP_GAIN_SCHED=1
주의: 리눅스 값이다. 스케줄러 지터가 섞여 최댓값은 RTOS 최악 응답의 근거가 될 수 없다(문서 10).
보드(ThreadX) 실측은 보드 복귀 후 같은 펌웨어로 DWT 카운터로 잰다. 여기서 볼 것은 '추가 연산이 평균을 얼마나 늘리나' 다.
사용: python3 sw/fw/npu_lib/bench_gs.py [반복수] [--out json]"""
import json, pathlib, subprocess, sys, tempfile
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent
N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200000
OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
cfgs = [("고정 게인", []), ("SLIP_MU_EST", ["-DSLIP_MU_EST=1"]), ("SLIP_MU_EST + SLIP_GAIN_SCHED", ["-DSLIP_MU_EST=1", "-DSLIP_GAIN_SCHED=1"])]
res = {}
with tempfile.TemporaryDirectory() as td:
    for name, fl in cfgs:
        exe = pathlib.Path(td) / ("b" + str(len(res)))
        subprocess.run(["gcc", "-O2", "-I", str(HERE), *fl, str(HERE / "bench_slip_linux.c"), "-o", str(exe), "-lm"], check=True)
        reps = []
        for rep in range(3):                                   # 세 번 돌려 가장 조용한 회차의 분포를 쓴다 (평균 비교가 목적)
            o = pathlib.Path(td) / f"lat{rep}.txt"
            subprocess.run(["taskset", "-c", "3", str(exe), str(N), str(o)], check=True)
            reps.append(np.loadtxt(o))
        lat = min(reps, key=lambda x: np.percentile(x, 99))
        res[name] = {"mean_ns": float(lat.mean()), "p50_ns": float(np.median(lat)), "p99_ns": float(np.percentile(lat, 99)),
                     "max_ns": float(lat.max()), "n": int(len(lat))}
        print(f"{name:32s} 평균 {lat.mean():7.1f} ns  중앙 {np.median(lat):6.0f}  99분위 {np.percentile(lat, 99):6.0f}  최대 {lat.max():8.0f} ns (리눅스)")
base = res["고정 게인"]["mean_ns"]
for k, v in res.items(): v["mean_ratio"] = v["mean_ns"] / base
print("평균 비율:", {k: round(v["mean_ratio"], 2) for k, v in res.items()})
if OUT: pathlib.Path(OUT).write_text(json.dumps({"platform": "Raspberry Pi 5 (Cortex-A76, Linux) — RTOS 최악값 근거 아님", "n": N, "results": res}, indent=1, ensure_ascii=False))
