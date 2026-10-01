#!/usr/bin/env python3
"""스템을 FP32로 빼지 않고 int8로 올린다 — 절벽 텐서의 양자화 범위만 정밀하게(클리핑).

왜
  스템 4노드를 FP32로 남기면 정확도는 회복되지만(0.8885) 실보드에서 그 스템이 CM55 float로
  돌아 추론 233 ms 중 ~190 ms를 먹는다 (EpochBlock_2 152 ms, _5 38 ms). NPU 94 epoch는 ~20 ms.
  절벽의 원인은 스템 hardswish 출력의 per-tensor 스케일이 긴 꼬리(소수 큰 값)에 끌려 대다수
  값의 해상도를 잃는 것이므로, 그 텐서의 범위를 백분위로 잘라 주면 int8로도 살 수 있다.
  ORT TensorQuantOverrides 로 해당 텐서에만 scale/zero_point를 지정한다.

출력
  1) 스템 텐서 분포 (p50/p99/p99.9/p99.99/max, 채널별 max 편차)  — 꼬리 문제인지 채널 불균형인지
  2) 클리핑 백분위 스윕 정확도 (전 레이어 int8, 제외 없음)
"""
import argparse, json, os, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import numpy as np, onnx, onnxruntime as ort, cv2
from onnx import helper
from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantType, QuantFormat, CalibrationMethod
from onnxruntime.quantization.shape_inference import quant_pre_process
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import build_index, build_carla_index, split_items, subsample

MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
STEM = ["/features/features.0/features.0.0/Conv_output_0",
        "/features/features.0/features.0.2/Mul_output_0",
        "/features/features.1/block/block.0/block.0.0/Conv_output_0",
        "/features/features.1/block/block.0/block.0.2/Relu_output_0"]

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=os.path.expanduser("~/icepredict/models/roadnet_v2_qat_stem/roadnet_qat_plain.onnx"))
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v2"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v2_stemclip"))
ap.add_argument("--calib", type=int, default=200)
ap.add_argument("--eval-per-class", type=int, default=300)
ap.add_argument("--pcts", default="99.9,99.99,99.999,100")
ap.add_argument("--activation", default="uint8", choices=["uint8", "int8"])
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
random.seed(a.seed); np.random.seed(a.seed)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

def load_img(p):
    im = cv2.imread(str(p))
    if im is None: return None
    im = cv2.resize(im, (224, 224), interpolation=cv2.INTER_AREA)
    return ((im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - MEAN) / STD)[None].astype(np.float32)

root = Path(a.rscd)
rscd_tr = build_index(root, "train"); rscd_te = subsample(build_index(root, "test_50k"), a.eval_per_class, a.seed)
carla_tr, carla_va, _ = split_items(build_carla_index(Path(a.carla)), val_frac=0.3, seed=a.seed)
def pick(items, n):
    by = {}
    for p, c in items: by.setdefault(c, []).append(p)
    per = max(1, n // len(by)); out_ = []
    for c, ps in sorted(by.items()): out_ += [(p, c) for p in random.sample(ps, min(per, len(ps)))]
    random.shuffle(out_); return out_[:n]
calib = pick(rscd_tr, a.calib // 2) + pick(carla_tr, a.calib - a.calib // 2); random.shuffle(calib)

prep = out / "prep.onnx"; quant_pre_process(a.model, str(prep), skip_symbolic_shape=False)
iname = ort.InferenceSession(str(prep), providers=["CPUExecutionProvider"]).get_inputs()[0].name

# ---- 1) 스템 텐서 분포 -------------------------------------------------------
m = onnx.load(str(prep)); have = {o for n in m.graph.node for o in n.output}
stem = [t for t in STEM if t in have]
for t in stem: m.graph.output.append(helper.make_empty_tensor_value_info(t))
dbg = out / "dbg.onnx"; onnx.save(m, str(dbg))
so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
s = ort.InferenceSession(str(dbg), so, providers=["CPUExecutionProvider"])
vals = {t: [] for t in stem}; chmax = {t: [] for t in stem}
for p, _ in calib[:60]:
    x = load_img(p)
    if x is None: continue
    outs = s.run(stem, {iname: x})
    for t, v in zip(stem, outs):
        v = v[0]; vals[t].append(np.abs(v).ravel()[::7]); chmax[t].append(np.abs(v).reshape(v.shape[0], -1).max(1))
print("\n=== 스템 텐서 |값| 분포 (calib 60장) ===")
print(f"{'tensor':<64s} {'p50':>7s} {'p99':>7s} {'p99.9':>7s} {'p99.99':>7s} {'max':>7s}  ch-max 중앙/최대")
stats = {}
for t in stem:
    v = np.concatenate(vals[t]); cm = np.stack(chmax[t]).max(0)
    q = np.percentile(v, [50, 99, 99.9, 99.99, 100]); stats[t] = q
    print(f"{t[-62:]:<64s} {q[0]:7.3f} {q[1]:7.3f} {q[2]:7.3f} {q[3]:7.3f} {q[4]:7.3f}  {np.median(cm):.3f}/{cm.max():.3f}")

# ---- 2) 클리핑 스윕 -----------------------------------------------------------
class Reader(CalibrationDataReader):
    def __init__(self, items): self.items = list(items); self.i = 0
    def get_next(self):
        while self.i < len(self.items):
            p, _ = self.items[self.i]; self.i += 1; x = load_img(p)
            if x is not None: return {iname: x}
        return None

def evaluate(path, items):
    so = ort.SessionOptions(); so.intra_op_num_threads = 8
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"]); nm = sess.get_inputs()[0].name
    C = len(ROAD_CLASSES); cm = np.zeros((C, C), np.int64)
    for p, c in items:
        x = load_img(p)
        if x is None: continue
        cm[c, int(np.argmax(sess.run(None, {nm: x})[0][0]))] += 1
    rec = cm.diagonal() / np.maximum(cm.sum(1), 1)
    return cm.trace() / max(cm.sum(), 1), {k: float(v) for k, v in zip(ROAD_CLASSES, rec)}

act = QuantType.QUInt8 if a.activation == "uint8" else QuantType.QInt8
def overrides_for(pct):
    if pct >= 100: return None
    ov = {}
    for t in stem:
        c = float(np.percentile(np.concatenate(vals[t]), pct)); c = max(c, 1e-3)
        # ORT는 numpy 스칼라(np.uint8 등)를 거부하고 numpy 배열만 받는다
        if a.activation == "uint8": ov[t] = [{"scale": np.array(2 * c / 255.0, dtype=np.float32), "zero_point": np.array(128, dtype=np.uint8)}]
        else:                       ov[t] = [{"scale": np.array(c / 127.0, dtype=np.float32), "zero_point": np.array(0, dtype=np.int8)}]
    return ov

fa, fr = evaluate(prep, rscd_te); ca, cr = evaluate(prep, carla_va)
print(f"\n[FP32] RSCD acc {fa:.4f} ice {fr['black_ice']:.3f} | CARLA acc {ca:.4f} ice {cr['black_ice']:.3f}")
print("\n=== 스템 클리핑 백분위 스윕 (전 레이어 int8, 제외 없음) ===")
print(f"{'clip pct':>9s} {'RSCD acc':>9s} {'RSCD ice':>9s} {'CARLA acc':>10s} {'CARLA ice':>10s}")
res = []
for pct in [float(x) for x in a.pcts.split(",")]:
    dst = out / f"int8_clip{pct:g}.onnx"
    extra = {"ActivationSymmetric": False, "WeightSymmetric": True}
    ov = overrides_for(pct)
    if ov: extra["TensorQuantOverrides"] = ov
    quantize_static(str(prep), str(dst), Reader(calib), quant_format=QuantFormat.QDQ, activation_type=act,
                    weight_type=QuantType.QInt8, per_channel=True, reduce_range=False,
                    calibrate_method=CalibrationMethod.MinMax, extra_options=extra)
    ra, rr = evaluate(dst, rscd_te); c2, cr2 = evaluate(dst, carla_va)
    print(f"{pct:9g} {ra:9.4f} {rr['black_ice']:9.3f} {c2:10.4f} {cr2['black_ice']:10.3f}   {dst.name}")
    res.append({"pct": pct, "rscd_acc": ra, "rscd_recall": rr, "carla_acc": c2, "carla_recall": cr2, "model": str(dst)})
(out / "sweep.json").write_text(json.dumps({"fp32": {"rscd_acc": fa, "carla_acc": ca}, "stem_stats": {t: list(map(float, q)) for t, q in stats.items()}, "sweep": res}, indent=1))
