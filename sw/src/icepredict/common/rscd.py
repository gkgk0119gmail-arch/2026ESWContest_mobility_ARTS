"""RSCD(Road Surface Classification Dataset) 파일명 라벨 파서와 IcePredict 4클래스 매핑.

파일명: <id>-<friction>[-<material>[-<unevenness>]].jpg
  friction   : dry, wet, water, ice, fresh_snow, melted_snow
  material   : asphalt, concrete, gravel, mud  (ice/snow는 없음)
  unevenness : smooth, slight, severe          (asphalt/concrete만)
IcePredict 4클래스(ROAD_CLASSES): normal / wet / black_ice / pothole
  - severe 요철 → pothole (마찰과 무관하게 우선)
  - ice, melted_snow → black_ice   (녹은 눈은 재결빙 위험이 커 위험 클래스로 묶음)
  - wet, water, fresh_snow → wet
  - dry → normal
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from icepredict.common.protocol import ROAD_CLASSES

FRICTION = ("dry", "wet", "water", "ice", "fresh_snow", "melted_snow")
MATERIAL = ("asphalt", "concrete", "gravel", "mud")
UNEVEN = ("smooth", "slight", "severe")
_RE = re.compile(r"^\d+-(?P<f>dry|wet|water|ice|fresh_snow|melted_snow)(?:-(?P<m>asphalt|concrete|gravel|mud))?(?:-(?P<u>smooth|slight|severe))?\.(?:jpg|jpeg|png)$")

@dataclass(frozen=True)
class RscdLabel:
    friction: str
    material: str | None
    uneven: str | None

    @property
    def cls4(self) -> str:
        if self.uneven == "severe":
            return "pothole"
        if self.friction in ("ice", "melted_snow"):
            return "black_ice"
        if self.friction in ("wet", "water", "fresh_snow"):
            return "wet"
        return "normal"

    @property
    def cls4_idx(self) -> int:
        return ROAD_CLASSES.index(self.cls4)

def parse(filename: str) -> RscdLabel | None:
    """라벨을 못 읽으면 None (오타 파일 등은 학습에서 제외)."""
    m = _RE.match(filename.rsplit("/", 1)[-1])
    if not m:
        return None
    return RscdLabel(m["f"], m["m"], m["u"])
