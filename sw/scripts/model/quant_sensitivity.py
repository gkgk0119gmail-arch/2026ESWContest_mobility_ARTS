#!/usr/bin/env python3
"""int8 양자화에 민감한 레이어를 찾아 그것만 FP32로 제외한다 (혼합 정밀도).

배경 (2026-09-18 실측)
  RoadNet v2를 전 레이어 int8로 양자화하면 RSCD acc가 0.90 -> 0.29~0.48로 붕괴한다.
  uint8/int8, 대칭/비대칭, minmax/entropy/percentile, ORT PTQ / PyTorch QAT 전 조합에서
  같다. 그런데 PyTorch가 제약 때문에 일부 레이어 양자화를 건너뛴 경우(QDQ 54쌍)에는
  acc 0.9050이 나왔다 -> **대다수 레이어는 int8을 견디고 소수가 망가뜨린다.**

  한편 Neural-ART NPU 매핑은 프로파일을 지정하면 99 epoch 중 HW 90개로 잘 된다
  (프로파일 미지정 시 1개로 보여 오진했다). NPU는 미양자화 연산을 CPU로 폴백시키므로
  소수 레이어를 FP32로 남겨도 나머지는 NPU에서 돈다.

방법
  1) FP32 모델과 int8 모델의 **모든 중간 텐서**를 출력으로 노출해 같은 입력으로 실행
  2) 이름이 일치하는 텐서마다 SQNR(dB)을 계산. 낮을수록 양자화가 그 지점을 망친 것
  3) SQNR이 낮은 텐서를 만든 노드를 K개씩 제외하고 재양자화 -> 정확도 스윕
  4) 정확도를 되찾는 최소 K를 고른다 (FP32로 남기는 레이어가 적을수록 NPU 비중이 높다)

예)
  python sw/scripts/model/quant_sensitivity.py --calib 100 --eval-per-class 150 --sweep 0,1,2,4,8
"""
import argparse, json, os, random, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import numpy as np
import onnx, onnxruntime as ort
from onnx import helper
from onnxruntime.quantization import (quantize_static, CalibrationDataReader, QuantType,
                                      QuantFormat, CalibrationMethod)
from onnxruntime.quantization.shape_inference import quant_pre_process
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import build_index, build_carla_index, split_items, subsample
import cv2

MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=os.path.expanduser("~/icepredict/models/roadnet_v2/roadnet.onnx"))
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v2"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v2_mixed"))
ap.add_argument("--calib", type=int, default=100)
ap.add_argument("--probe", type=int, default=16, help="SQNR 측정에 쓸 이미지 수")
ap.add_argument("--eval-per-class", type=int, default=150)
ap.add_argument("--sweep", default="0,1,2,4,8,16", help="FP32로 제외할 노드 수 후보")
ap.add_argument("--size", type=int, default=224)
ap.add_argument("--quantized", default="", help="이미 양자화된 모델을 분석만 한다 (재양자화·스윕 생략)")
ap.add_argument("--cliff-db", type=float, default=3.0, help="이 SQNR(dB) 아래로 처음 떨어지는 지점을 절벽으로 본다")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
random.seed(a.seed); np.random.seed(a.seed)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

def load_img(path):
    im = cv2.imread(str(path))
    if im is None:
        return None
    im = cv2.resize(im, (a.size, a.size), interpolation=cv2.INTER_AREA)
    rgb = im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0
    return ((rgb - MEAN) / STD)[None].astype(np.float32)

# ---- 데이터 ---------------------------------------------------------------
rscd_root = Path(a.rscd)
rscd_tr = build_index(rscd_root, "train")
rscd_te = subsample(build_index(rscd_root, "test_50k"), a.eval_per_class, a.seed)
carla_tr, carla_va, _ = split_items(build_carla_index(Path(a.carla)), val_frac=0.3, seed=a.seed)

def pick_balanced(items, n):
    by = {}
    for p, c in items:
        by.setdefault(c, []).append(p)
    per = max(1, n // max(len(by), 1))
    picked = []
    for c, ps in sorted(by.items()):
        picked += [(p, c) for p in random.sample(ps, min(per, len(ps)))]
    random.shuffle(picked)
    return picked[:n]

calib = pick_balanced(rscd_tr, a.calib // 2) + pick_balanced(carla_tr, a.calib - a.calib // 2)
random.shuffle(calib)
probe = calib[: a.probe]
print(f"보정 {len(calib)}장 / 민감도 측정 {len(probe)}장 / 평가 RSCD {len(rscd_te)}장 + CARLA {len(carla_va)}장")

class Reader(CalibrationDataReader):
    def __init__(self, items, name):
        self.items = list(items); self.i = 0; self.name = name
    def get_next(self):
        while self.i < len(self.items):
            p, _ = self.items[self.i]; self.i += 1
            x = load_img(p)
            if x is not None:
                return {self.name: x}
        return None

prep = out / "roadnet_prep.onnx"
quant_pre_process(a.model, str(prep), skip_symbolic_shape=False)
iname = ort.InferenceSession(str(prep), providers=["CPUExecutionProvider"]).get_inputs()[0].name

def quantize(dst, exclude=()):
    quantize_static(
        str(prep), str(dst), Reader(calib, iname),
        quant_format=QuantFormat.QDQ, activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8, per_channel=True, reduce_range=False,
        calibrate_method=CalibrationMethod.MinMax,
        nodes_to_exclude=list(exclude),
        extra_options={"ActivationSymmetric": True, "WeightSymmetric": True},
    )

def expose_all_outputs(src, dst):
    """모든 중간 텐서를 graph output으로 추가한 사본을 만든다."""
    m = onnx.load(str(src))
    existing = {o.name for o in m.graph.output}
    for node in m.graph.node:
        for o in node.output:
            if o and o not in existing:
                m.graph.output.append(helper.make_empty_tensor_value_info(o))
                existing.add(o)
    onnx.save(m, str(dst))
    return m

def run_all(model_path, imgs):
    so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
    nm = sess.get_inputs()[0].name
    names = [o.name for o in sess.get_outputs()]
    acc = {n: [] for n in names}
    for p, _ in imgs:
        x = load_img(p)
        if x is None:
            continue
        for n, v in zip(names, sess.run(names, {nm: x})):
            acc[n].append(np.asarray(v, np.float32).ravel())
    return {n: np.concatenate(v) for n, v in acc.items() if v and v[0].size}

# ---- 1) 전량 양자화 후 SQNR 측정 -------------------------------------------
full_q = out / "int8_full.onnx"
if a.quantized:
    full_q = Path(a.quantized)
    print(f"\n[1] 주어진 양자화 모델 분석: {full_q}")
else:
    print("\n[1] 전량 int8 양자화 …")
    quantize(full_q)

fp32_dbg = out / "fp32_debug.onnx"; int8_dbg = out / "int8_debug.onnx"
expose_all_outputs(prep, fp32_dbg); expose_all_outputs(full_q, int8_dbg)
print("[2] 중간 텐서 추출 (FP32) …"); a_fp = run_all(fp32_dbg, probe)
print("[2] 중간 텐서 추출 (INT8) …"); a_q = run_all(int8_dbg, probe)

common = [n for n in a_fp if n in a_q and a_fp[n].shape == a_q[n].shape]
print(f"[3] 비교 가능한 텐서 {len(common)}개 / FP32 {len(a_fp)} · INT8 {len(a_q)}")

def sqnr_db(ref, test):
    noise = ((ref - test) ** 2).mean()
    sig = (ref ** 2).mean()
    if noise <= 0:
        return float("inf")
    return float(10.0 * np.log10(max(sig, 1e-20) / noise))

# 텐서 -> 그 텐서를 만든 노드 이름
m_prep = onnx.load(str(prep))
producer = {}
for node in m_prep.graph.node:
    for o in node.output:
        producer[o] = (node.name or o, node.op_type)

rows = []
for n in common:
    s = sqnr_db(a_fp[n], a_q[n])
    nm, op = producer.get(n, ("?", "?"))
    rows.append({"tensor": n, "node": nm, "op": op, "sqnr_db": s})
rows.sort(key=lambda r: r["sqnr_db"])

print("\n=== SQNR 최악 20개 (낮을수록 양자화가 망친 지점) ===")
print(f"{'SQNR(dB)':>9s}  {'op':<16s} {'node':<40s} tensor")
for r in rows[:20]:
    print(f"{r['sqnr_db']:9.2f}  {r['op']:<16s} {r['node'][:40]:<40s} {r['tensor'][:40]}")
(out / "sensitivity.json").write_text(json.dumps(rows, indent=1))

# 연쇄 오차에서는 '가장 나쁜 곳'이 아니라 '처음 무너지는 곳'이 원인이다. 하류는 상류 오차를
# 물려받아 더 나빠 보이므로 절대 SQNR 순위로는 원인이 묻힌다 (실제로 겪었다: 첫 depthwise가
# 7위로 밀려 한 번도 제외되지 않았다). 그래프 순서로 훑어 임계 아래로 처음 떨어지는 지점을 찍는다.
by_t = {r["tensor"]: r for r in rows}
order = [o for node in m_prep.graph.node for o in node.output if o in by_t]
print(f"\n=== 그래프 순서 SQNR (절벽 기준 {a.cliff_db} dB) ===")
first_cliff, shown = None, 0
for i, t in enumerate(order, 1):
    r = by_t[t]; low = r["sqnr_db"] < a.cliff_db
    if first_cliff is None and low:
        first_cliff = i
    if i <= 6 or low and shown < 14:
        print(f"{i:3d} {r['sqnr_db']:8.2f}  {r['op']:<16s} {t[:60]}{'  <<< 절벽' if low and first_cliff == i else ('  <' if low else '')}")
        if low: shown += 1
print(f"첫 절벽: #{first_cliff} {order[first_cliff-1] if first_cliff else '-'}"
      f"  | SQNR>{a.cliff_db}dB 텐서 {sum(1 for r in rows if r['sqnr_db'] >= a.cliff_db)}/{len(rows)}")

# 제외 후보: 가중치를 가진 연산만 (Conv/Gemm/MatMul) — 활성만 있는 노드는 제외해도 효과가 적다
WEIGHTED = {"Conv", "Gemm", "MatMul"}
cands, seen = [], set()
for r in rows:
    if r["op"] in WEIGHTED and r["node"] not in seen:
        cands.append(r["node"]); seen.add(r["node"])
print(f"\n제외 후보(가중 연산) {len(cands)}개 — 최악 순: {cands[:8]}")

# ---- 4) 정확도 스윕 --------------------------------------------------------
def evaluate(model_path, items):
    so = ort.SessionOptions(); so.intra_op_num_threads = 8
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL   # QLinearMul 융합 실패 회피
    sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
    nm = sess.get_inputs()[0].name
    C = len(ROAD_CLASSES); cm = np.zeros((C, C), np.int64)
    for p, c in items:
        x = load_img(p)
        if x is None:
            continue
        cm[c, int(np.argmax(sess.run(None, {nm: x})[0][0]))] += 1
    acc = cm.trace() / max(cm.sum(), 1)
    rec = cm.diagonal() / np.maximum(cm.sum(1), 1)
    return float(acc), {k: float(v) for k, v in zip(ROAD_CLASSES, rec)}

fp32_acc, fp32_rec = evaluate(prep, rscd_te)
fp32_cacc, fp32_crec = evaluate(prep, carla_va)
print(f"\n[기준] FP32  RSCD acc {fp32_acc:.4f} ice {fp32_rec['black_ice']:.3f} | "
      f"CARLA acc {fp32_cacc:.4f} ice {fp32_crec['black_ice']:.3f}")

if a.quantized:
    r_acc, r_rec = evaluate(full_q, rscd_te); c_acc, c_rec = evaluate(full_q, carla_va)
    print(f"[주어진 모델] RSCD acc {r_acc:.4f} ice {r_rec['black_ice']:.3f} | CARLA acc {c_acc:.4f} ice {c_rec['black_ice']:.3f}")
    sys.exit(0)
print("\n=== 제외 개수 스윕 ===")
print(f"{'K':>3s}  {'RSCD acc':>9s} {'RSCD ice':>9s}  {'CARLA acc':>10s} {'CARLA ice':>10s}  모델")
results = []
for K in [int(x) for x in a.sweep.split(",")]:
    ex = cands[:K]
    dst = out / f"int8_ex{K}.onnx"
    if K == 0:
        dst = full_q
    else:
        quantize(dst, ex)
    r_acc, r_rec = evaluate(dst, rscd_te)
    c_acc, c_rec = evaluate(dst, carla_va)
    mq = onnx.load(str(dst))
    nq = sum(1 for n in mq.graph.node if n.op_type == "QuantizeLinear")
    print(f"{K:3d}  {r_acc:9.4f} {r_rec['black_ice']:9.3f}  {c_acc:10.4f} {c_rec['black_ice']:10.3f}  "
          f"QDQ {nq} · {dst.name}")
    results.append({"K": K, "excluded": ex, "rscd_acc": r_acc, "rscd_recall": r_rec,
                    "carla_acc": c_acc, "carla_recall": c_rec, "quantize_nodes": nq,
                    "model": str(dst)})

(out / "sweep.json").write_text(json.dumps(
    {"fp32": {"rscd_acc": fp32_acc, "rscd_recall": fp32_rec,
              "carla_acc": fp32_cacc, "carla_recall": fp32_crec},
     "sweep": results, "sensitivity_top": rows[:40], "args": vars(a)}, indent=1))
print(f"\n저장: {out}/sweep.json, {out}/sensitivity.json")
print("다음: 정확도를 되찾은 최소 K의 모델로")
print('      stedgeai analyze --target stm32n6 --st-neural-art "n6-allmems-O3@<user_neuralart.json>"')
print("      를 돌려 HW epoch 수를 확인한다 (FP32로 남긴 레이어는 CPU 폴백된다)")
