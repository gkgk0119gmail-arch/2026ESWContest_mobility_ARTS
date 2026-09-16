#!/usr/bin/env python3
"""INT8 PTQ(사후 양자화) 정확도 손실 측정.

계획서 주장: "STM32Cube.AI PTQ로 FP32→INT8, 보정셋 500장, 정확도 손실 5% 이내"를
ONNX Runtime 정적 양자화(per-tensor, MinMax)로 동등 조건 검증한다.
벤더 툴체인(STM32Cube.AI / Hailo DFC)은 계정 로그인이 필요해 여기서는 대리 검증.
"""
import argparse, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, onnxruntime as ort
from PIL import Image
from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantType, QuantFormat, CalibrationMethod
from icepredict.common.rscd import parse
from icepredict.common.protocol import ROAD_CLASSES

MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

def load(p, size=224):
    img = Image.open(p).convert("RGB").resize((size, size), Image.BILINEAR)
    x = np.asarray(img, np.float32).transpose(2, 0, 1) / 255.0
    return ((x - MEAN) / STD)[None]

def index(root, split, limit=None, seed=0):
    items = []
    for dp, _, fs in os.walk(Path(root) / split):
        for f in fs:
            l = parse(f)
            if l: items.append((os.path.join(dp, f), l.cls4_idx))
    items.sort()
    if limit:
        rng = np.random.default_rng(seed); idx = rng.permutation(len(items))[:limit]
        items = [items[i] for i in idx]
    return items

class Reader(CalibrationDataReader):
    def __init__(self, items, name): self.it = iter(items); self.name = name
    def get_next(self):
        n = next(self.it, None)
        return None if n is None else {self.name: load(n[0])}

def evaluate(sess, items, name, bs=32):
    C = len(ROAD_CLASSES); cm = np.zeros((C, C), np.int64); lat = []
    for i in range(0, len(items), bs):
        chunk = items[i:i+bs]
        for p, y in chunk:
            x = load(p); t0 = time.perf_counter()
            pr = int(np.argmax(sess.run(None, {name: x})[0]))
            lat.append((time.perf_counter()-t0)*1000); cm[y, pr] += 1
    acc = np.trace(cm) / cm.sum()
    rec = (np.diag(cm) / np.maximum(cm.sum(1), 1))
    return {"acc": float(acc), "macro_recall": float(rec.mean()),
            "recall": {k: float(v) for k, v in zip(ROAD_CLASSES, rec)},
            "median_ms": float(np.median(lat)), "cm": cm.tolist()}

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--fp32", default=os.path.expanduser("~/icepredict/models/roadnet_v1/roadnet.onnx"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v1/roadnet_int8.onnx"))
ap.add_argument("--calib", type=int, default=500)
ap.add_argument("--eval", type=int, default=4000)
ap.add_argument("--per-channel", action="store_true", help="채널별 가중치 스케일 (MobileNet 계열 필수)")
ap.add_argument("--calib-method", default="minmax", choices=["minmax", "percentile", "entropy"])
ap.add_argument("--conv-only", action="store_true", help="Conv/Gemm만 양자화 (HardSigmoid/Mul은 FP32 유지)")
ap.add_argument("--uint8-act", action="store_true", help="활성화를 UInt8로 (비대칭)")
a = ap.parse_args()

calib = index(a.root, "vali_20k", a.calib, seed=1)
ev = index(a.root, "test_50k", a.eval, seed=2)
print(f"calib={len(calib)} eval={len(ev)}")

s32 = ort.InferenceSession(a.fp32, providers=["CPUExecutionProvider"]); nm = s32.get_inputs()[0].name
prep = a.fp32.replace(".onnx", "_prep.onnx")
from onnxruntime.quantization.shape_inference import quant_pre_process
quant_pre_process(a.fp32, prep, skip_symbolic_shape=True)
t0 = time.time()
CM = {"minmax": CalibrationMethod.MinMax, "percentile": CalibrationMethod.Percentile, "entropy": CalibrationMethod.Entropy}
kw = {}
if a.conv_only: kw["op_types_to_quantize"] = ["Conv", "Gemm"]
quantize_static(prep, a.out, Reader(calib, nm), quant_format=QuantFormat.QDQ,
                activation_type=QuantType.QUInt8 if a.uint8_act else QuantType.QInt8,
                weight_type=QuantType.QInt8,
                per_channel=a.per_channel, calibrate_method=CM[a.calib_method], **kw)
print(f"config: per_channel={a.per_channel} calib={a.calib_method} conv_only={a.conv_only} uint8_act={a.uint8_act}")
print(f"quantized in {time.time()-t0:.0f}s  size {os.path.getsize(a.fp32)/1e6:.2f}MB → {os.path.getsize(a.out)/1e6:.2f}MB")

r32 = evaluate(s32, ev, nm)
s8 = ort.InferenceSession(a.out, providers=["CPUExecutionProvider"])
r8 = evaluate(s8, ev, s8.get_inputs()[0].name)
print("\n            FP32     INT8     차이")
print(f"acc        {r32['acc']:.4f}  {r8['acc']:.4f}  {r8['acc']-r32['acc']:+.4f}")
print(f"macro_rec  {r32['macro_recall']:.4f}  {r8['macro_recall']:.4f}  {r8['macro_recall']-r32['macro_recall']:+.4f}")
for k in ROAD_CLASSES:
    print(f"  {k:10s} {r32['recall'][k]:.4f}  {r8['recall'][k]:.4f}  {r8['recall'][k]-r32['recall'][k]:+.4f}")
print(f"latency ms {r32['median_ms']:.1f}    {r8['median_ms']:.1f}")
rel = (r32['acc'] - r8['acc']) / r32['acc'] * 100
print(f"\n상대 정확도 손실 {rel:.2f}%  (계획서 목표 5% 이내: {'충족' if rel <= 5 else '미달'})")
json.dump({"fp32": r32, "int8": r8, "rel_acc_loss_pct": rel}, open(Path(a.out).with_name("quant_report.json"), "w"), indent=1)
