"""MobileNetV3-Small 백본 + 노면 4클래스 헤드 + 반사도 헤드 (차선 헤드는 미구현).
STM32Cube.AI 배포를 위해 ONNX(opset 13, 고정 1×3×224×224)로 내보낸다."""
from __future__ import annotations
import torch, torch.nn as nn
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
from icepredict.common.protocol import ROAD_CLASSES

class RoadNet(nn.Module):
    def __init__(self, pretrained: bool = True, n_cls: int = len(ROAD_CLASSES), dropout: float = 0.2,
                 spec_head: bool = False):
        super().__init__()
        w = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
        m = mobilenet_v3_small(weights=w)
        self.features = m.features
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.head_cls = nn.Sequential(nn.Flatten(), nn.Linear(576, 256), nn.Hardswish(), nn.Dropout(dropout), nn.Linear(256, n_cls))
        # 반사도 헤드: 노면이 얼마나 거울처럼 반사하는가 (0~1). 융합 가중치의 45%(beta)를 쓰는 신호다.
        # 로짓만 내보내고 시그모이드는 밖에서 — 양자화 시 시그모이드가 별도 SW epoch가 되는 것을 피한다.
        self.head_spec = nn.Sequential(nn.Flatten(), nn.Linear(576, 64), nn.Hardswish(), nn.Linear(64, 1)) if spec_head else None
    def forward(self, x):
        f = self.pool(self.features(x))
        if self.head_spec is None:
            return self.head_cls(f)
        return torch.cat([self.head_cls(f), self.head_spec(f)], dim=1)   # [B, n_cls+1] 마지막이 반사도 로짓

def export_onnx(model: nn.Module, path: str, size: int = 224):
    model = model.eval().cpu()
    dummy = torch.zeros(1, 3, size, size)
    torch.onnx.export(model, dummy, path, opset_version=13, input_names=["image"], output_names=["logits"],
                      dynamo=False)
    return path
