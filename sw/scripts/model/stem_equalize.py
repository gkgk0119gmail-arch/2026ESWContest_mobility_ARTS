#!/usr/bin/env python3
"""스템 hardswish 출력의 채널 간 범위를 균등화해 스템을 int8로 올린다 (재학습 없음, 수학적 동치).

진단 (2026-09-19)
  features.0 hardswish 출력의 채널 max: 중앙 3.1, 최대 175(채널 11), 최소 0.3 — 500× 편차.
  채널 11은 대부분 0 근처(중앙 -0.05, 51.7% 음수)에 드문 스파이크(p99=38)가 있는 희소 채널이다.
  per-tensor 스케일이 이 채널에 맞춰지면 나머지 15개 채널은 2~3단계로 뭉개지고, depthwise는
  채널을 섞지 않으므로 그 손상이 그대로 출력 붕괴(SQNR -2dB)가 된다. 클리핑은 무효(몸통 간 차이).
  hardswish는 양의 동차함수가 아니라 그 **앞**에서의 CLE는 정확하지 않다(항등 구간 비율 15%).

방법 — hardswish **뒤**에 균등화 층을 넣는다
  h = hardswish(x)                              # 채널별 범위 제각각
  h' = DWScale(h)  : 1x1 depthwise Conv, W_c = 1/s_c   # 채널별 범위를 맞춘다 (선형, 정확)
  dw3x3'(h') with W'_c = W_c * s_c              # 역수를 뒤 3x3 depthwise에 접어 넣음 → 동치
  1x1 depthwise Conv를 쓰는 이유: Conv 가중치는 per-channel 양자화되어 채널당 값 하나가 정확히
  표현된다. 상수 Mul은 per-tensor로 양자화되어 작은 배율이 뭉개진다. 비용 112²×16 MAC.
  s_c = 채널 범위 / 목표(중앙값). 아주 작은 채널의 과증폭을 막기 위해 s_c >= s_min.
"""
import argparse, os, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import numpy as np, onnx, onnxruntime as ort, cv2
from onnx import helper, numpy_helper
from icepredict.train.data import build_index, build_carla_index

MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
HS_OUT = "/features/features.0/features.0.2/Mul_output_0"
DW = "/features/features.1/block/block.0/block.0.0/Conv"

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=os.path.expanduser("~/icepredict/models/roadnet_v2_qat_stem/roadnet_qat_plain.onnx"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v2_eq/roadnet_eq_fp32.onnx"))
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v2"))
ap.add_argument("--n", type=int, default=64, help="채널 범위 추정 이미지 수")
ap.add_argument("--pct", type=float, default=99.99, help="채널 범위로 쓸 백분위 (max 대신 — 스파이크 1~2개에 끌리지 않게)")
ap.add_argument("--s-min", type=float, default=0.05, help="s_c 하한 (작은 채널 최대 1/s_min 배 증폭)")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
random.seed(a.seed)
out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)

def li(p):
    im = cv2.imread(str(p))
    if im is None: return None
    im = cv2.resize(im, (224, 224), interpolation=cv2.INTER_AREA)
    return ((im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - MEAN) / STD)[None].astype(np.float32)

m = onnx.load(a.model)
g = m.graph
nodes = {n.name: n for n in g.node}
dw = nodes[DW]
assert dw.input[0] == HS_OUT, f"depthwise 입력이 {dw.input[0]} (기대 {HS_OUT})"
consumers = [n.name for n in g.node if HS_OUT in n.input]
assert consumers == [DW], f"hardswish 출력 소비자가 {consumers} — 단일 소비자여야 접어 넣기가 동치"
inits = {t.name: t for t in g.initializer}
W = numpy_helper.to_array(inits[dw.input[1]]); C = W.shape[0]
grp = next((at.i for at in dw.attribute if at.name == "group"), 1)
assert grp == C and W.shape[1] == 1, "depthwise가 아님"

# ---- 채널 범위 추정 ----
dbg = onnx.load(a.model); dbg.graph.output.append(helper.make_empty_tensor_value_info(HS_OUT))
tmp = str(out.parent / "_dbg.onnx"); onnx.save(dbg, tmp)
so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
s = ort.InferenceSession(tmp, so, providers=["CPUExecutionProvider"]); nm = s.get_inputs()[0].name
items = random.sample(build_index(Path(a.rscd), "train"), a.n // 2) + random.sample(build_carla_index(Path(a.carla)), a.n - a.n // 2)
per = []
for p, _ in items:
    x = li(p)
    if x is None: continue
    v = s.run([HS_OUT], {nm: x})[0][0].reshape(C, -1)
    per.append(np.percentile(np.abs(v), a.pct, axis=1))
rng_c = np.stack(per).max(0)                               # 채널별 범위 (p99.99의 이미지 간 max)
target = float(np.median(rng_c))
s_c = np.maximum(rng_c / target, a.s_min).astype(np.float32)
print(f"채널 범위(p{a.pct}) 중앙 {target:.3f}, 최대 {rng_c.max():.2f}(ch{rng_c.argmax()}), 최소 {rng_c.min():.3f}")
print("s_c:", np.round(s_c, 3).tolist())
print(f"균등화 후 채널 범위: {(rng_c / s_c).min():.2f} ~ {(rng_c / s_c).max():.2f} (편차 {(rng_c/s_c).max()/(rng_c/s_c).min():.1f}×, 이전 {rng_c.max()/rng_c.min():.0f}×)")

# ---- 그래프 편집 (v2) --------------------------------------------------------
# v1(hardswish 뒤 1x1 균등화 Conv)은 실패했다(0.36): 그 Conv도 양자화 대상이라 ORT가 그 입력
# (불균형한 hardswish 출력)에 per-tensor Q를 먼저 박는다 — 문제를 한 노드 뒤로 옮겼을 뿐.
# 불균형은 Conv0 출력 x에서 시작되므로 균등화는 Conv0 가중치에 접어 넣는다:
#   Conv0'(w,b 채널 c를 1/s_c 배)  → x' = x/s_c            [양자화, 균형]
#   Mul_s(x', s_c)                 → x   (게이트 계산용)     [FLOAT — 제외. 여기서 Q하면 절벽 재현]
#   HardSigmoid(x)                 → h ∈ [0,1]              [FLOAT — 제외 (유계라 양자화해도 되지만 정확성 위해 제외)]
#   Mul_hs(x', h)                  → y' = hardswish(x)/s_c  [양자화, |y'| <= |x'| 라 균형]
#   dw3x3'(w 채널 c를 s_c 배)(y')  = dw3x3(hardswish(x))    [정확히 동치]
# float으로 남는 것은 112²×16 원소의 Mul과 HardSigmoid 두 개뿐이다 (수 ms).
CONV0 = "/features/features.0/features.0.0/Conv"; HSIG = "/features/features.0/features.0.2/HardSigmoid"; HSMUL = "/features/features.0/features.0.2/Mul"
c0, hsig, hsmul = nodes[CONV0], nodes[HSIG], nodes[HSMUL]
X = c0.output[0]
assert hsig.input[0] == X and set(hsmul.input) == {X, hsig.output[0]}, "hardswish 구조가 기대와 다름"
assert [n.name for n in g.node if X in n.input] == [HSIG, HSMUL] or sorted(n.name for n in g.node if X in n.input) == sorted([HSIG, HSMUL]), "Conv0 출력 소비자가 hardswish 두 노드여야 함"
# Conv0 가중치/편향 스케일
W0 = numpy_helper.to_array(inits[c0.input[1]]); inits[c0.input[1]].CopyFrom(numpy_helper.from_array((W0 / s_c.reshape(-1, 1, 1, 1)).astype(np.float32), inits[c0.input[1]].name))
if len(c0.input) > 2:
    B0 = numpy_helper.to_array(inits[c0.input[2]]); inits[c0.input[2]].CopyFrom(numpy_helper.from_array((B0 / s_c).astype(np.float32), inits[c0.input[2]].name))
# Mul_s 삽입: x = x' * s_c  (HardSigmoid 입력만 이걸 쓴다)
g.initializer.append(numpy_helper.from_array(s_c.reshape(1, C, 1, 1).astype(np.float32), "stem_eq_s"))
X_UNSCALED = X + "_unscaled"
MUL_S = "/features/features.0/stem_eq/Mul_s"
g.node.insert([i for i, n in enumerate(g.node) if n.name == HSIG][0], helper.make_node("Mul", [X, "stem_eq_s"], [X_UNSCALED], name=MUL_S))
hsig.input[0] = X_UNSCALED
# Mul_hs(x', h) 는 그대로 (x'가 이미 스케일된 값) → 출력 = hardswish(x)/s_c
# dw3x3 가중치 보정
W2 = (W * s_c.reshape(C, 1, 1, 1)).astype(np.float32)
inits[dw.input[1]].CopyFrom(numpy_helper.from_array(W2, inits[dw.input[1]].name))
onnx.checker.check_model(m)
m = onnx.shape_inference.infer_shapes(m)
onnx.save(m, str(out))
print(f"저장: {out}")
print(f"float으로 제외할 노드: {MUL_S}, {HSIG}")
print(f"dw 가중치 |w|max 채널 편차: {np.abs(W2).reshape(C,-1).max(1).max()/np.abs(W2).reshape(C,-1).max(1).min():.1f}× (이전 {np.abs(W).reshape(C,-1).max(1).max()/np.abs(W).reshape(C,-1).max(1).min():.1f}×)")
(out.parent / "exclude_nodes.txt").write_text(f"{MUL_S},{HSIG}")

# ---- 동치 검증: 원본 vs 균등화 모델 로짓 ----
A = ort.InferenceSession(a.model, so, providers=["CPUExecutionProvider"]); B = ort.InferenceSession(str(out), so, providers=["CPUExecutionProvider"])
worst = 0.0
for p, _ in items[:8]:
    x = li(p)
    if x is None: continue
    worst = max(worst, float(np.abs(A.run(None, {nm: x})[0] - B.run(None, {nm: x})[0]).max()))
print(f"동치 검증 (8장, FP32 로짓 최대 차): {worst:.2e}  {'OK' if worst < 1e-3 else '!! 불일치'}")
