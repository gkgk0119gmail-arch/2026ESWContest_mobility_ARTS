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
from icepredict.sim.camera import ROI_TOP, ROI_BOTTOM, ROI_LEFT, ROI_RIGHT
CAM_ROI = (ROI_TOP, ROI_BOTTOM, ROI_LEFT, ROI_RIGHT)

MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

@dataclass
class RoadNetDetector:
    model_path: str
    size: int = 224
    # 전방 8~30m 노면 구간. 값의 근거는 icepredict.sim.camera 참고.
    roi_top: float = CAM_ROI[0]
    roi_bottom: float = CAM_ROI[1]
    roi_left: float = CAM_ROI[2]
    roi_right: float = CAM_ROI[3]
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

# ---- 3) 빙판 시각화 (카메라 투영) ----------------------------------------
# CARLA friction trigger는 물리만 바꾸고 렌더링되지 않는다. 데모 영상에서 빙판이 어디인지
# 보이도록, 패치가 덮는 도로면 폴리곤을 카메라 화면에 투영해 반투명하게 덧그린다.
# 주의: 이 오버레이는 "표시"일 뿐이며, 1차 방어의 추론 입력에는 원본 프레임을 쓴다.

def camera_intrinsics(width: int, height: int, fov_deg: float) -> np.ndarray:
    f = width / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
    return np.array([[f, 0, width / 2.0], [0, f, height / 2.0], [0, 0, 1.0]], np.float64)

def world_to_image(points_world, camera, K: np.ndarray):
    """CARLA world 좌표 리스트 → 이미지 좌표. 카메라 뒤(z<=0)는 None."""
    w2c = np.array(camera.get_transform().get_inverse_matrix())
    out = []
    for p in points_world:
        v = np.array([p.x, p.y, p.z, 1.0])
        cam = w2c @ v
        # UE4(x앞, y오른, z위) → 표준 카메라(x오른, y아래, z앞)
        pc = np.array([cam[1], -cam[2], cam[0]])
        if pc[2] <= 0.1:
            out.append(None); continue
        img = K @ pc
        out.append((img[0] / img[2], img[1] / img[2]))
    return out

def patch_road_polygon(carla_map, patch: IcePatch, half_width_m: float | None = None, step_m: float = 2.0):
    """패치가 덮는 도로면을 waypoint를 따라 좌우 경계 점열로 만든다 (곡선 도로 대응)."""
    import carla
    center = carla_map.get_waypoint(patch.location, project_to_road=True)
    if half_width_m is None:
        half_width_m = max(2.0, center.lane_width / 2.0 - 0.1)   # 차선 폭에 맞춤
    half_len = patch.extent[0]
    # 중심에서 뒤로 half_len 이동한 지점부터 시작
    start = center
    moved = 0.0
    while moved < half_len:
        prv = start.previous(step_m)
        if not prv: break
        start = prv[0]; moved += step_m
    left, right, cur, travelled = [], [], start, 0.0
    while travelled <= 2 * half_len:
        t = cur.transform
        yaw = math.radians(t.rotation.yaw)
        nx, ny = -math.sin(yaw), math.cos(yaw)      # 좌우 법선
        z = t.location.z + 0.05
        left.append(carla.Location(t.location.x + nx * half_width_m, t.location.y + ny * half_width_m, z))
        right.append(carla.Location(t.location.x - nx * half_width_m, t.location.y - ny * half_width_m, z))
        nxt = cur.next(step_m)
        if not nxt: break
        cur = nxt[0]; travelled += step_m
    return left, right

def draw_patch_overlay(bgr: np.ndarray, poly_world, camera, K, color=(255, 220, 120), alpha=0.5,
                       label: str | None = None, post_h_m: float = 1.6):
    """도로면 폴리곤을 반투명하게 채우고, 진입 경계에 세로 기둥을 세워 원거리에서도 보이게 한다."""
    import cv2, carla
    left, right = poly_world
    pl = world_to_image(left, camera, K); pr = world_to_image(right, camera, K)
    pts = [p for p in pl if p] + [p for p in reversed(pr) if p]
    h, w = bgr.shape[:2]
    if len(pts) >= 3:
        arr = np.array(pts, np.int32)
        if not (arr[:, 0].max() < -w or arr[:, 0].min() > 2 * w):
            ov = bgr.copy()
            cv2.fillPoly(ov, [arr], color)
            bgr = cv2.addWeighted(ov, alpha, bgr, 1 - alpha, 0)
            cv2.polylines(bgr, [arr], True, (255, 255, 255), 2)
    # 진입 경계(패치 시작) 가로줄 + 양끝 세로 기둥
    if left and right:
        base = [left[0], right[0]]
        top = [carla.Location(p.x, p.y, p.z + post_h_m) for p in base]
        pb = world_to_image(base, camera, K); pt = world_to_image(top, camera, K)
        if all(pb) and all(pt):
            cv2.line(bgr, tuple(map(int, pb[0])), tuple(map(int, pb[1])), (255, 255, 255), 3)
            for b, t in zip(pb, pt):
                cv2.line(bgr, tuple(map(int, b)), tuple(map(int, t)), (80, 200, 255), 3)
            cv2.line(bgr, tuple(map(int, pt[0])), tuple(map(int, pt[1])), (80, 200, 255), 2)
            if label:
                lx = int((pt[0][0] + pt[1][0]) / 2); ly = int(min(pt[0][1], pt[1][1])) - 10
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
                if -tw < lx < w + tw and -50 < ly < h:
                    cv2.rectangle(bgr, (lx - tw // 2 - 6, ly - th - 8), (lx + tw // 2 + 6, ly + 6), (0, 0, 0), -1)
                    cv2.putText(bgr, label, (lx - tw // 2, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (80, 220, 255), 2)
    return bgr
