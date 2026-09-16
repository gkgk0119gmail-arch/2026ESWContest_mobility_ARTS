"""MobileNetV3-Small 백본 + 노면 4클래스 헤드 (반사도·차선 헤드는 CARLA 라벨 준비 후 추가).
STM32Cube.AI 배포를 위해 ONNX(opset 13, 고정 1×3×224×224)로 내보낸다."""
from __future__ import annotations
import torch, torch.nn as nn
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
from icepredict.common.protocol import ROAD_CLASSES

class RoadNet(nn.Module):
    def __init__(self, pretrained: bool = True, n_cls: int = len(ROAD_CLASSES), dropout: float = 0.2):
        super().__init__()
        w = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
        m = mobilenet_v3_small(weights=w)
        self.features = m.features
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.head_cls = nn.Sequential(nn.Flatten(), nn.Linear(576, 256), nn.Hardswish(), nn.Dropout(dropout), nn.Linear(256, n_cls))
    def forward(self, x):
        return self.head_cls(self.pool(self.features(x)))

def export_onnx(model: nn.Module, path: str, size: int = 224):
    model = model.eval().cpu()
    dummy = torch.zeros(1, 3, size, size)
    torch.onnx.export(model, dummy, path, opset_version=13, input_names=["image"], output_names=["logits"],
                      dynamo=False)
    return path
