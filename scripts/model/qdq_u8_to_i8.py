#!/usr/bin/env python3
"""QDQ ONNX의 uint8 활성 양자화를 int8로 **정확히** 변환한다.

왜 필요한가
  ST Edge AI Core는 부호 없는 양자화 모델을 거부한다:
    "NOT IMPLEMENTED: Onnx exporting model with quantized unsigned integer format is not supported"
  그런데 PyTorch FX QAT는 quint8 활성일 때만 전 레이어를 제대로 양자화한다 (qint8을 강제하면
  dtype 제약을 못 맞춘 레이어가 조용히 생략된다). 그래서 quint8로 학습·내보낸 뒤 여기서 바꾼다.

왜 정확한가
  uint8 격자 {0..255}에 영점 z, 스케일 s 인 양자화는 int8 격자 {-128..127}에 영점 z-128,
  같은 스케일 s 인 양자화와 **완전히 같은 실수 집합**을 표현한다 (평행이동). 따라서 QuantizeLinear/
  DequantizeLinear의 zero_point를 -128 하고 dtype만 바꾸면 출력이 부동소수 반올림 수준에서 동일하다.
  ORT의 '비대칭 int8 보정'이 나빴던 것(0.56)과는 무관하다 — 그건 보정 선택의 문제였고 이건 형식 변환이다.

사용:
  python scripts/model/qdq_u8_to_i8.py in.onnx out.onnx [--check N]   # --check: N장으로 출력 일치 검증
"""
import argparse, sys
import numpy as np
import onnx
from onnx import numpy_helper, TensorProto

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("dst")
ap.add_argument("--check", type=int, default=0, help="이 개수의 난수 입력으로 변환 전후 출력 일치 검증")
a = ap.parse_args()

m = onnx.load(a.src)
inits = {t.name: t for t in m.graph.initializer}
zp_names = set()
for node in m.graph.node:
    if node.op_type in ("QuantizeLinear", "DequantizeLinear") and len(node.input) > 2:
        zp_names.add(node.input[2])

changed_zp, changed_tensor = 0, 0
for name in sorted(zp_names):
    t = inits.get(name)
    if t is None or t.data_type != TensorProto.UINT8:
        continue
    arr = numpy_helper.to_array(t).astype(np.int32) - 128
    t.CopyFrom(numpy_helper.from_array(arr.astype(np.int8), name))
    changed_zp += 1

# uint8로 선언된 값 정보(중간 텐서/출력)를 int8로. Q 출력 텐서 타입은 zero_point dtype에서
# 추론되므로 대개 불필요하지만, 명시 선언이 남아 있으면 검사기가 불일치를 잡는다.
for vi in list(m.graph.value_info) + list(m.graph.output) + list(m.graph.input):
    tt = vi.type.tensor_type
    if tt.elem_type == TensorProto.UINT8:
        tt.elem_type = TensorProto.INT8; changed_tensor += 1
# 가중치 등 uint8 초기값(있다면)도 같은 이동
for t in m.graph.initializer:
    if t.data_type == TensorProto.UINT8 and t.name not in zp_names:
        arr = numpy_helper.to_array(t).astype(np.int32) - 128
        t.CopyFrom(numpy_helper.from_array(arr.astype(np.int8), t.name)); changed_tensor += 1

onnx.checker.check_model(m)
onnx.save(m, a.dst)
print(f"변환: zero_point {changed_zp}개, 선언/초기값 {changed_tensor}개 uint8→int8 → {a.dst}")

if a.check:
    import onnxruntime as ort
    # 그래프 최적화를 끈다. 켜면 ORT가 QDQ+Mul을 QLinearMul로 융합하다 PyTorch export의
    # 비스칼라 영점에서 실패한다 ("Scale and Zero-point must be a scalar"). ST 툴체인과는 무관.
    so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    A = ort.InferenceSession(a.src, so, providers=["CPUExecutionProvider"])
    B = ort.InferenceSession(a.dst, so, providers=["CPUExecutionProvider"])
    inp = A.get_inputs()[0]
    shape = [d if isinstance(d, int) else 1 for d in inp.shape]
    rng = np.random.default_rng(0)
    worst, agree = 0.0, 0
    for _ in range(a.check):
        x = rng.standard_normal(shape).astype(np.float32)
        ya = A.run(None, {inp.name: x})[0]; yb = B.run(None, {inp.name: x})[0]
        worst = max(worst, float(np.abs(ya - yb).max()))
        agree += int(np.argmax(ya) == np.argmax(yb))
    print(f"검증 {a.check}장: 최대 절대오차 {worst:.2e}, argmax 일치 {agree}/{a.check}"
          + ("" if worst < 1e-3 else "   ← 불일치! 변환이 정확하지 않다"))
    sys.exit(0 if worst < 1e-3 else 1)
