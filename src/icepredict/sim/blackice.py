"""CARLA 블랙아이스 패치 생성과 감지 파이프라인.

- IcePatch : friction trigger box로 마찰계수를 낮추고, 경로상 위치를 계산해 준다.
- RoadNetDetector : 전방 카메라 프레임의 노면 ROI를 잘라 RoadNet ONNX로 4클래스 추론.
- 두 방어선의 판정은 icepredict.pi.fusion / icepredict.pi.imu_slip 를 그대로 재사용한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np

# ---- 1) 블랙아이스 패치 ---------------------------------------------------
@dataclass
class IcePatch:
    transform: "carla.Transform"
    extent: tuple[float, float, float]      # 반너비 (x 길이방향, y 폭, z 높이) in meters
    friction: float = 0.02
    actor: object = None

    @property
    def location(self):
        return self.transform.location

    def contains(self, loc, margin: float = 0.0) -> bool:
        """차량 위치가 패치 안인지 (yaw 회전 고려)."""
        t = self.transform
        dx, dy = loc.x - t.location.x, loc.y - t.location.y
        yaw = math.radians(t.rotation.yaw)
        lx = dx * math.cos(-yaw) - dy * math.sin(-yaw)
        ly = dx * math.sin(-yaw) + dy * math.cos(-yaw)
        return abs(lx) <= self.extent[0] + margin and abs(ly) <= self.extent[1] + margin

def spawn_ice_patch(world, waypoint, length_m=30.0, width_m=6.0, friction=0.02) -> IcePatch:
    """주어진 waypoint 중심으로 도로를 따라 마찰 트리거 박스를 만든다.
    CARLA의 friction trigger는 extent가 cm 단위(박스 절반 크기)."""
    import carla
    bp = world.get_blueprint_library().find("static.trigger.friction")
    ext = carla.Vector3D(length_m * 50.0, width_m * 50.0, 300.0)   # m→cm, 절반
    bp.set_attribute("friction", str(friction))
    bp.set_attribute("extent_x", str(ext.x)); bp.set_attribute("extent_y", str(ext.y)); bp.set_attribute("extent_z", str(ext.z))
    tf = carla.Transform(waypoint.transform.location + carla.Location(z=0.0), waypoint.transform.rotation)
    actor = world.spawn_actor(bp, tf)
    return IcePatch(transform=tf, extent=(length_m / 2, width_m / 2, 3.0), friction=friction, actor=actor)

def waypoint_ahead(carla_map, vehicle, distance_m: float):
    """차량 현재 차선에서 distance_m 앞의 waypoint (차선 유지)."""
    wp = carla_map.get_waypoint(vehicle.get_location(), project_to_road=True)
    step, moved = 2.0, 0.0
    while moved < distance_m:
        nxt = wp.next(step)
        if not nxt:
            break
        wp = nxt[0]; moved += step
    return wp

# ---- 2) 카메라 추론 -------------------------------------------------------
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

@dataclass
class RoadNetDetector:
    model_path: str
    size: int = 224
    roi_top: float = 0.62      # 프레임 하단 도로 영역만 사용 (RSCD는 노면 패치로 학습됨)
    roi_bottom: float = 0.97
    roi_left: float = 0.30
    roi_right: float = 0.70
    sess: object = field(init=False, default=None)
    _iname: str = field(init=False, default="")

    def __post_init__(self):
        import onnxruntime as ort
        so = ort.SessionOptions(); so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(self.model_path, so, providers=["CPUExecutionProvider"])
        self._iname = self.sess.get_inputs()[0].name

    def crop(self, rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        return rgb[int(h * self.roi_top):int(h * self.roi_bottom), int(w * self.roi_left):int(w * self.roi_right)]

    def infer(self, rgb: np.ndarray) -> np.ndarray:
        """returns softmax probs over ROAD_CLASSES"""
        import cv2
        patch = cv2.resize(self.crop(rgb), (self.size, self.size), interpolation=cv2.INTER_LINEAR)
        x = patch.astype(np.float32).transpose(2, 0, 1) / 255.0
        x = ((x - MEAN) / STD)[None]
        logits = self.sess.run(None, {self._iname: x})[0][0]
        e = np.exp(logits - logits.max())
        return e / e.sum()
