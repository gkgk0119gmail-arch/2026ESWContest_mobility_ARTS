#!/usr/bin/env python3
"""FX QAT 체크포인트의 가중치를 **평범한 RoadNet**에 되돌려 싣고 v2와 같은 경로로 FP32 ONNX를 만든다.

왜
  FX GraphModule에서 바로 export한 그래프는 부풀어 있다 (양자화 후 736노드, atonn HW 134/SW 120).
  같은 모델을 model.export_onnx 경로로 내보내면 529노드, HW 88/SW 14다. QAT의 가치는 가중치에
  있으므로 가중치만 옮겨 깨끗한 그래프를 얻는다.

키 매핑
  prepare_qat_fx는 torchvision Conv2dNormActivation(Sequential: Conv, BN, act)의 Conv+BN을
  인덱스 0의 융합 모듈로 바꾼다 → 상태 키가 X.0.weight, X.0.bn.* 가 된다.
  평범한 모델은 X.0.weight(conv), X.1.*(bn) 이다. 즉 '.bn.' 이 붙은 키는 인덱스를 +1 하면 된다.
  qconfig=None으로 뺀 스템은 융합되지 않아 키가 그대로다. fake-quant/observer 버퍼는 버린다.
  strict 로드가 실패하면 매핑이 틀린 것이므로 멈춘다.
"""
import argparse, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import torch
from icepredict.train.model import RoadNet, export_onnx

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", default=os.path.expanduser("~/icepredict/models/roadnet_v2_qat_stem/best.pt"))
ap.add_argument("--out", default="")
a = ap.parse_args()
sd = torch.load(a.ckpt, map_location="cpu")["model"]
# qconfig=None 으로 뺀 스템은 융합되지 않는 대신 FX가 (Conv, BN)을 한 단계 더 감싼다:
#   features.0.0.0.weight (conv) / features.0.0.1.* (bn)  →  features.0.0.weight / features.0.1.*
# 실측 키로 확인한 규칙이다 (처음 가정과 반대였다 — strict 로드 실패로 발견).
STEM = ("features.0", "features.1.block.0")
def remap(k):
    for p in STEM:
        if k.startswith(p + ".0.0."): return p + ".0." + k[len(p) + 5:]
        if k.startswith(p + ".0.1."): return p + ".1." + k[len(p) + 5:]
    m = re.match(r"^(.*)\.(\d+)\.bn\.(.+)$", k)          # 융합 ConvBn: X.i.bn.* → X.(i+1).*
    return f"{m.group(1)}.{int(m.group(2))+1}.{m.group(3)}" if m else k
new, dropped = {}, 0
for k, v in sd.items():
    if "activation_post_process" in k or "weight_fake_quant" in k:
        dropped += 1; continue
    new[remap(k)] = v
plain = RoadNet(pretrained=False)
res = plain.load_state_dict(new, strict=False)
print(f"버린 양자화 버퍼 {dropped}개 / missing {len(res.missing_keys)} / unexpected {len(res.unexpected_keys)}")
if res.missing_keys or res.unexpected_keys:
    print("  missing:", res.missing_keys[:5]); print("  unexpected:", res.unexpected_keys[:5])
    sys.exit(1)
out = Path(a.out) if a.out else Path(a.ckpt).with_name("roadnet_qat_plain.onnx")
export_onnx(plain, str(out)); print(f"FP32 ONNX (v2 형태): {out}")
