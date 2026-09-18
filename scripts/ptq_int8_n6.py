#!/usr/bin/env python3
"""RoadNet v2를 ONNX Runtime PTQ로 int8화한다 — 활성을 **부호 있는 int8**로.

왜 이 스크립트인가
  핸드오프에는 "ONNX Runtime PTQ로 acc 0.910 -> 0.48 붕괴, per-channel·보정법·conv-only
  모두 무효"로 기록돼 있다. 그런데 **활성의 부호를 바꿔본 기록이 없다.**
  ORT `quantize_static`의 기본 activation_type은 QUInt8(부호 없음)이다.

  2026-09-18 실측으로 원인이 확인됐다:
    - 활성을 quint8로 두면 fake-quant 상태에서 이미 RSCD acc 0.3515로 붕괴한다
    - 활성을 qint8로 바꾸면 학습 0스텝에서 acc 0.9050 (FP32 v2는 0.8868)
  즉 붕괴는 양자화의 근본적 어려움이 아니라 **부호 없는 활성 양자화** 때문이었다.
  "features.1 첫 depthwise Conv 최대오차 15"는 증상이고 원인이 아니다.

  그리고 ST Edge AI Core는 부호 없는 양자화 모델을 아예 거부한다:
    "NOT IMPLEMENTED: Onnx exporting model with quantized unsigned integer format
     is not supported"
  즉 signed int8은 정확도 문제이기 전에 **배포 가능성의 전제 조건**이다.

  PyTorch FX QAT로 qint8 활성을 강제하면 backend config 제약을 만족하지 못한 레이어의
  양자화가 조용히 생략된다 (QDQ 쌍 164 -> 54). 정확도는 좋아 보이지만 양자화된 레이어가
  적어서이고, NPU 매핑은 1개에 머문다. 그래서 ORT PTQ로 전 레이어를 균일하게 양자화한다.

성공 판정 두 개
  (a) 정확도  : RSCD test acc가 FP32 v2(0.8868) 근처. PTQ 실패값 0.48과 대비
  (b) NPU 매핑: `stedgeai analyze --target stm32n6 --st-neural-art`의 HW epoch 수.
                FP32는 143 epoch 중 HW 1개였다. 이 수가 크게 늘어야 배포 의미가 있다

예)
  python scripts/ptq_int8_n6.py --calib 300 --eval-per-class 500
"""
import argparse, json, os, random, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import onnx, onnxruntime as ort
from onnxruntime.quantization import (quantize_static, CalibrationDataReader, QuantType,
                                      QuantFormat, CalibrationMethod)
from onnxruntime.quantization.shape_inference import quant_pre_process
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import build_index, build_carla_index, split_items, subsample
from icepredict.common.rscd import parse as rscd_parse

MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=os.path.expanduser("~/icepredict/models/roadnet_v2/roadnet.onnx"))
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v2"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v2_int8"))
ap.add_argument("--calib", type=int, default=300, help="보정 이미지 수 (RSCD+CARLA 혼합)")
ap.add_argument("--eval-per-class", type=int, default=500, help="RSCD test 클래스당 평가 장수 (0=전체)")
ap.add_argument("--size", type=int, default=224)
ap.add_argument("--activation", default="int8", choices=["int8", "uint8"],
                help="활성 양자화 부호. int8이 정답 — uint8은 ST가 거부하고 정확도도 붕괴한다")
ap.add_argument("--no-per-channel", action="store_true")
ap.add_argument("--calib-method", default="minmax", choices=["minmax", "entropy", "percentile"])
# 활성 대칭/비대칭. ReLU·hardswish 출력은 대부분 음수가 아니라, 대칭으로 잡으면 범위의
# 절반을 버려 실질 1비트를 잃는다. ST가 거부한 것은 '부호 없음'이지 '비대칭'이 아니므로
# 영점을 허용하는 비대칭 int8을 쓸 수 있다. 대칭을 켜면 보정법이 무력화되는 부작용도 있다
# (minmax와 entropy가 동일한 결과를 낸 이유).
ap.add_argument("--act-symmetric", action="store_true", help="활성을 대칭으로 (기본은 비대칭)")
# 양자화할 연산 종류. MobileNetV3의 SE 블록(GlobalAveragePool → fc → HardSigmoid → Mul)이
# 민감도 최악 목록 상위를 차지한다. Conv/Gemm만 양자화하고 그 계열을 FP32로 남기면
# NPU는 미양자화 연산을 CPU로 폴백시키므로 Conv 대부분은 여전히 NPU에서 돈다.
ap.add_argument("--op-types", default="", help="양자화할 연산 종류 (쉼표). 예: Conv,Gemm. 빈 값=전체")
# 혼합 정밀도. 민감도 분석에서 SQNR이 **처음 무너지는 곳**은 features.1의 첫 depthwise 3x3
# (31dB → -2dB). 그 뒤는 전부 하류다. 절대 SQNR 순위로는 하류가 더 나빠 보여 원인이 묻힌다.
# depthwise는 출력 채널이 입력 채널 하나에만 의존해 per-tensor 활성 양자화에 취약하다
# (MobileNet 계열의 알려진 실패 모드). NPU는 미양자화 노드를 CPU로 폴백시키므로
# 소수 레이어를 FP32로 남겨도 MACC 대부분(1x1 pointwise)은 NPU에서 돈다.
ap.add_argument("--exclude-nodes", default="", help="FP32로 남길 노드 이름 (쉼표)")
ap.add_argument("--exclude-depthwise", action="store_true", help="depthwise Conv(group==C_in) 전부 FP32로")
ap.add_argument("--exclude-prefix", default="", help="이 접두사로 시작하는 노드 전부 FP32로 (쉼표)")
# 부분 문자열 매칭. FX GraphModule에서 export한 그래프는 노드 이름 규칙이 다르다
# (/features/features.0/... 대신 /features_0_.../). 두 규칙을 한 번에 다루기 위한 것.
ap.add_argument("--exclude-match", default="", help="이 부분 문자열을 포함하는 노드 전부 FP32로 (쉼표)")
# 그래프 순서 기준. 이름 규칙에 의존하지 않는다 — FX가 계층을 평탄화하면 features.1.block.0이
# /block.0.0/block.0.0.0/Conv 가 되는 식으로 이름이 바뀌어 접두사/부분 매칭이 빗나간다
# (실제로 0개 제외되어 스템이 양자화되고 0.383으로 붕괴했다). 스템 절벽은 "첫 depthwise Conv
# 까지"라는 위치로 정의되므로 그 위치까지의 노드를 전부 제외한다.
ap.add_argument("--exclude-until", default="", help="그래프 순서로 이 부분 문자열을 처음 포함하는 노드까지 전부 FP32로")
ap.add_argument("--exclude-until-depthwise", action="store_true", help="그래프 순서로 첫 depthwise Conv까지 전부 FP32로")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
random.seed(a.seed); np.random.seed(a.seed)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

import cv2

def load_img(path):
    im = cv2.imread(str(path))
    if im is None:
        return None
    im = cv2.resize(im, (a.size, a.size), interpolation=cv2.INTER_AREA)
    rgb = im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0
    return ((rgb - MEAN) / STD)[None].astype(np.float32)

# ---- 데이터: 보정·평가 목록 ------------------------------------------------
t0 = time.time()
rscd_root = Path(a.rscd)
rscd_tr = build_index(rscd_root, "train")
rscd_te = build_index(rscd_root, "test_50k")
if a.eval_per_class:
    rscd_te = subsample(rscd_te, a.eval_per_class, a.seed)
carla_tr, carla_va, _keys = split_items(build_carla_index(Path(a.carla)), val_frac=0.3, seed=a.seed)
print(f"index: rscd_train={len(rscd_tr)} rscd_test={len(rscd_te)} carla_val={len(carla_va)} ({time.time()-t0:.0f}s)")

# 보정 집합은 배포 도메인을 대표해야 한다 → RSCD와 CARLA를 절반씩, 클래스 균등하게
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
print(f"보정 이미지 {len(calib)}장 (RSCD {a.calib//2} + CARLA {a.calib - a.calib//2})")

class Reader(CalibrationDataReader):
    def __init__(self, items, input_name):
        self.items = list(items); self.i = 0; self.name = input_name
    def get_next(self):
        while self.i < len(self.items):
            p, _c = self.items[self.i]; self.i += 1
            x = load_img(p)
            if x is not None:
                return {self.name: x}
        return None

sess0 = ort.InferenceSession(a.model, providers=["CPUExecutionProvider"])
iname = sess0.get_inputs()[0].name

# ---- 전처리 후 양자화 ------------------------------------------------------
prep = out / "roadnet_prep.onnx"
quant_pre_process(a.model, str(prep), skip_symbolic_shape=False)
print(f"전처리: {prep}")

# ---- 제외 노드 목록 구성 --------------------------------------------------
mp = onnx.load(str(prep))
inits = {t.name: t for t in mp.graph.initializer}
exclude = [n.strip() for n in a.exclude_nodes.split(",") if n.strip()]
prefixes = [n.strip() for n in a.exclude_prefix.split(",") if n.strip()]
matches = [n.strip() for n in a.exclude_match.split(",") if n.strip()]
dw = []
for node in mp.graph.node:
    if node.op_type == "Conv":
        g = next((att.i for att in node.attribute if att.name == "group"), 1)
        w = inits.get(node.input[1])
        cin = (w.dims[1] * g) if w is not None else None
        if g > 1 and cin is not None and g == cin:
            dw.append(node.name)
    if prefixes and any(node.name.startswith(px) for px in prefixes):
        exclude.append(node.name)
    if matches and any(mt in node.name for mt in matches):
        exclude.append(node.name)
if a.exclude_depthwise:
    exclude += dw
if a.exclude_until or a.exclude_until_depthwise:
    for node in mp.graph.node:
        exclude.append(node.name)
        hit = (a.exclude_until and a.exclude_until in node.name) or \
              (a.exclude_until_depthwise and node.name in dw)
        if hit:
            break
exclude = list(dict.fromkeys(exclude))
print(f"depthwise Conv {len(dw)}개 감지; FP32로 제외하는 노드 {len(exclude)}개"
      + (f": {exclude[:6]}{' ...' if len(exclude) > 6 else ''}" if exclude else ""))

q_path = out / f"roadnet_int8_{a.activation}.onnx"
act = QuantType.QInt8 if a.activation == "int8" else QuantType.QUInt8
method = {"minmax": CalibrationMethod.MinMax, "entropy": CalibrationMethod.Entropy,
          "percentile": CalibrationMethod.Percentile}[a.calib_method]
print(f"양자화: activation={a.activation}({'대칭' if a.act_symmetric else '비대칭'}) weight=int8 "
      f"per_channel={not a.no_per_channel} calib={a.calib_method} format=QDQ")
quantize_static(
    str(prep), str(q_path), Reader(calib, iname),
    quant_format=QuantFormat.QDQ,
    activation_type=act,
    weight_type=QuantType.QInt8,
    per_channel=not a.no_per_channel,
    reduce_range=False,
    calibrate_method=method,
    op_types_to_quantize=[t.strip() for t in a.op_types.split(",") if t.strip()] or None,
    nodes_to_exclude=exclude or None,
    extra_options={"ActivationSymmetric": a.act_symmetric, "WeightSymmetric": True},
)
m = onnx.load(str(q_path))
kinds = {}
for node in m.graph.node:
    kinds[node.op_type] = kinds.get(node.op_type, 0) + 1
print(f"양자화 완료: {q_path}")
print(f"  노드 {len(m.graph.node)}개 — QuantizeLinear {kinds.get('QuantizeLinear',0)} / "
      f"DequantizeLinear {kinds.get('DequantizeLinear',0)} / Conv {kinds.get('Conv',0)} / "
      f"QLinearConv {kinds.get('QLinearConv',0)}")

# ---- 평가 ------------------------------------------------------------------
def evaluate(model_path, items, label):
    so = ort.SessionOptions(); so.intra_op_num_threads = 8
    sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
    nm = sess.get_inputs()[0].name
    C = len(ROAD_CLASSES); cm = np.zeros((C, C), np.int64)
    for p, c in items:
        x = load_img(p)
        if x is None:
            continue
        pred = int(np.argmax(sess.run(None, {nm: x})[0][0]))
        cm[c, pred] += 1
    acc = cm.trace() / max(cm.sum(), 1)
    rec = (cm.diagonal() / np.maximum(cm.sum(1), 1))
    print(f"  {label:18s} acc {acc:.4f}  recall " +
          " ".join(f"{k}={v:.3f}" for k, v in zip(ROAD_CLASSES, rec)))
    return {"acc": float(acc), "recall": {k: float(v) for k, v in zip(ROAD_CLASSES, rec)},
            "cm": cm.tolist()}

print("\n=== 평가 (RSCD test) ===")
fp32_r = evaluate(a.model, rscd_te, "FP32 v2")
int8_r = evaluate(q_path, rscd_te, f"INT8 ({a.activation})")
print("=== 평가 (CARLA val) ===")
fp32_c = evaluate(a.model, carla_va, "FP32 v2")
int8_c = evaluate(q_path, carla_va, f"INT8 ({a.activation})")

res = {"args": vars(a), "onnx_nodes": kinds, "excluded_nodes": exclude, "depthwise_nodes": dw,
       "rscd": {"fp32": fp32_r, "int8": int8_r},
       "carla": {"fp32": fp32_c, "int8": int8_c}}
(out / f"metrics_{a.activation}.json").write_text(json.dumps(res, indent=1))

print(f"\n=== 결과 ({a.activation} 활성) ===")
print(f"RSCD  acc              : {fp32_r['acc']:.4f} (FP32) → {int8_r['acc']:.4f} (INT8)   [PTQ 실패 기록 = 0.48]")
print(f"RSCD  black_ice recall : {fp32_r['recall']['black_ice']:.4f} → {int8_r['recall']['black_ice']:.4f}")
print(f"CARLA black_ice recall : {fp32_c['recall']['black_ice']:.4f} → {int8_c['recall']['black_ice']:.4f}")
print(f"\n다음: stedgeai analyze --model {q_path} --type onnx --target stm32n6 --st-neural-art")
print("      HW epoch 수가 늘어야 NPU 배포에 의미가 있다 (FP32는 143 중 1개였다)")
