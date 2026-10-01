"""전방 노면 카메라의 표준 설정. 수집기와 데모가 반드시 같은 값을 써야 한다.

설계 근거
  계획서는 "차량 전방 30m 범위 노면 상태"를 1차 감지 대상으로 잡는다. 카메라 높이 h, 아래로
  기울인 각 p, 초점거리 f일 때 전방 거리 d인 노면은 이미지 y = cy - f*tan(p - atan(h/d)) 에 맺힌다.

  FOV 90°(f=320), pitch -12°로 두면 8~30m 구간이 y 188~232 로 단 44px에 압축된다. 여기서
  224×224로 늘려도 노면 질감이 남지 않는다. 화각을 60°(f=554)로 좁히면 같은 구간이 151~227,
  76px로 넓어져 질감과 차선이 살아난다.

  따라서 넓은 화각으로 발밑을 보는 대신, 적당히 좁은 화각으로 8~30m 앞 노면을 본다.
  이것이 "밟기 전에 본다"는 1차 방어의 전제와도 맞는다.
"""
from __future__ import annotations
import math

WIDTH, HEIGHT, FOV = 640, 480, 60.0
CAM_X, CAM_Z, CAM_PITCH = 1.4, 1.6, -12.0
EXPOSURE_COMP = -1.0        # 노면 하이라이트가 날아가지 않도록 약간 어둡게

# 전방 8~30m 노면이 맺히는 세로 구간 (아래 road_band_y 로 계산한 값)
ROI_TOP, ROI_BOTTOM, ROI_LEFT, ROI_RIGHT = 0.30, 0.49, 0.28, 0.72

def focal_px(width: int = WIDTH, fov_deg: float = FOV) -> float:
    return width / (2.0 * math.tan(math.radians(fov_deg) / 2.0))

def image_y_for_distance(d_m: float, h: float = CAM_Z, pitch_deg: float = CAM_PITCH,
                         height: int = HEIGHT, width: int = WIDTH, fov_deg: float = FOV) -> float:
    """전방 d_m 지점의 노면이 맺히는 이미지 y 좌표."""
    f = focal_px(width, fov_deg)
    depression = math.atan(h / max(d_m, 0.1))              # 수평선 기준 내려본 각
    rel = math.radians(-pitch_deg) - depression            # 카메라 광축 기준 (양수 = 광축 위)
    return height / 2.0 - f * math.tan(rel)

def horizon_y(pitch_deg: float = CAM_PITCH, height: int = HEIGHT,
              width: int = WIDTH, fov_deg: float = FOV) -> float:
    """무한 원거리 노면이 맺히는 y. 노면 정반사(평면 거울 근사)의 대칭축."""
    return height / 2.0 - focal_px(width, fov_deg) * math.tan(math.radians(-pitch_deg))

def road_band_y(near_m: float = 8.0, far_m: float = 30.0, **kw) -> tuple[float, float]:
    """near~far 구간이 차지하는 (위, 아래) y. 정규화 전 픽셀 값."""
    return image_y_for_distance(far_m, **kw), image_y_for_distance(near_m, **kw)

def apply_camera_bp(bp, width: int = WIDTH, height: int = HEIGHT, fov: float = FOV,
                    exposure: float | None = EXPOSURE_COMP):
    bp.set_attribute("image_size_x", str(width))
    bp.set_attribute("image_size_y", str(height))
    bp.set_attribute("fov", str(fov))
    if exposure is not None and bp.has_attribute("exposure_compensation"):
        bp.set_attribute("exposure_compensation", str(exposure))
    return bp

def camera_transform(carla_mod):
    return carla_mod.Transform(carla_mod.Location(x=CAM_X, z=CAM_Z), carla_mod.Rotation(pitch=CAM_PITCH))

def roi_slice(height: int = HEIGHT, width: int = WIDTH):
    return (int(height * ROI_TOP), int(height * ROI_BOTTOM),
            int(width * ROI_LEFT), int(width * ROI_RIGHT))

def crop_roi(img):
    ys, ye, xs, xe = roi_slice(img.shape[0], img.shape[1])
    return img[ys:ye, xs:xe]
