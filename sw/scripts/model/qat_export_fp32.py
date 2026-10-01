#!/usr/bin/env python3
"""QAT로 단련된 가중치를 **FP32 ONNX**로 뽑는다 (fake-quant 제거).

왜
  PyTorch가 내보낸 QDQ 그래프는 정확도는 좋지만(RSCD 0.8975) atonn이 잘 흡수하지 못한다
  (293 epoch 중 HW 54). 반면 ORT PTQ가 만든 QDQ 그래프는 HW 88 / SW 14로 매핑된다.
  QAT의 가치는 *가중치*(양자화에 강건해진)에 있고 ORT의 가치는 *그래프 형태*에 있으므로,
  QAT 가중치를 FP32로 뽑아 ORT PTQ(스템 제외)로 다시 양자화한다.

방법
  qat_n6.py와 같은 qconfig/제외 모듈로 prepare_qat_fx를 재구성해 best.pt를 싣고,
  fake-quant와 관측자를 끈 채 export한다. FakeQuantize.forward는 fake_quant_enabled가 0이면
  입력을 그대로 돌려주므로 TorchScript 추적 export에서 그 연산이 사라져 순수 FP32 그래프가 된다.
"""
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import torch, torch.ao.quantization as tq
from torch.ao.quantization.quantize_fx import prepare_qat_fx
from icepredict.train.model import RoadNet

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", default=os.path.expanduser("~/icepredict/models/roadnet_v2_qat_stem/best.pt"))
ap.add_argument("--out", default="")
ap.add_argument("--size", type=int, default=224)
a = ap.parse_args()
ck = torch.load(a.ckpt, map_location="cpu")
args = ck.get("args", {})
fp32_mods = [m.strip() for m in args.get("fp32_modules", "features.0,features.1.block.0").split(",") if m.strip()]
act_dtype = args.get("act_dtype", "quint8")

if act_dtype == "quint8":
    act_fq = tq.FakeQuantize.with_args(observer=tq.MovingAverageMinMaxObserver, quant_min=0, quant_max=255,
                                       dtype=torch.quint8, qscheme=torch.per_tensor_affine, reduce_range=False)
else:
    act_fq = tq.FakeQuantize.with_args(observer=tq.MovingAverageMinMaxObserver, quant_min=-128, quant_max=127,
                                       dtype=torch.qint8, qscheme=torch.per_tensor_affine, reduce_range=False)
wt_fq = tq.FakeQuantize.with_args(observer=tq.MovingAveragePerChannelMinMaxObserver, quant_min=-128, quant_max=127,
                                  dtype=torch.qint8, qscheme=torch.per_channel_symmetric, ch_axis=0)
qm = tq.QConfigMapping().set_global(tq.QConfig(activation=act_fq, weight=wt_fq))
for name in fp32_mods:
    qm = qm.set_module_name(name, None)

example = torch.randn(1, 3, a.size, a.size)
model = prepare_qat_fx(RoadNet(pretrained=False).train(), qm, (example,))
missing, unexpected = model.load_state_dict(ck["model"], strict=False)
print(f"ckpt epoch {ck.get('epoch')}  fp32_modules={fp32_mods}  act={act_dtype}  "
      f"missing={len(missing)} unexpected={len(unexpected)}")
model.eval()
model.apply(tq.disable_observer); model.apply(tq.disable_fake_quant)

out = Path(a.out) if a.out else Path(a.ckpt).with_name("roadnet_qat_fp32.onnx")
torch.onnx.export(model, (example,), str(out), opset_version=13,
                  input_names=["image"], output_names=["logits"], dynamo=False)
import onnx
m = onnx.load(str(out)); ops = {}
for n in m.graph.node: ops[n.op_type] = ops.get(n.op_type, 0) + 1
print(f"FP32 ONNX: {out}  노드 {len(m.graph.node)}  QuantizeLinear {ops.get('QuantizeLinear', 0)} (0이어야 함)")
print("Conv 노드 이름 예:", [n.name for n in m.graph.node if n.op_type == "Conv"][:3])
