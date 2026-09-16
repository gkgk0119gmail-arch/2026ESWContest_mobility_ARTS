#!/usr/bin/env python3
"""RoadNet ONNX CPU 추론 속도 측정 (Pi 5 기준선). NPU 목표(30ms)와 비교용."""
import sys, time, argparse
from pathlib import Path
import numpy as np, onnxruntime as ort

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=str(Path(__file__).resolve().parents[1] / "models/roadnet.onnx"))
ap.add_argument("--runs", type=int, default=200)
ap.add_argument("--threads", type=int, default=0, help="0=기본")
a = ap.parse_args()

so = ort.SessionOptions()
if a.threads: so.intra_op_num_threads = a.threads
sess = ort.InferenceSession(a.model, so, providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0]
x = np.random.randn(*[d if isinstance(d, int) else 1 for d in inp.shape]).astype(np.float32)
for _ in range(20): sess.run(None, {inp.name: x})
ts = []
for _ in range(a.runs):
    t0 = time.perf_counter(); sess.run(None, {inp.name: x}); ts.append((time.perf_counter()-t0)*1000)
ts.sort()
print(f"model={Path(a.model).name} shape={inp.shape} threads={a.threads or 'default'} runs={a.runs}")
print(f"latency ms  min={ts[0]:.1f}  median={ts[len(ts)//2]:.1f}  p95={ts[int(len(ts)*0.95)]:.1f}  max={ts[-1]:.1f}")
print(f"throughput  {1000/ (sum(ts)/len(ts)):.1f} fps")
