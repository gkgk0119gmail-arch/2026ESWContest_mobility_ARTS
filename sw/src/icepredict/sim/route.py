"""고정 경로 주행 (Traffic Manager 미사용).

왜 직접 모나
  Traffic Manager로 자율주행을 시키면 경로를 예측할 수 없어 빙판 패치를 경로 위에 보장할 수
  없고, 액터 파괴 시점에 TM과 충돌해 클라이언트가 죽는다(std::runtime_error: destroyed actor).
  경로를 미리 뽑아 두고 pure pursuit으로 따라가면 결정론적이고 TM 의존이 사라진다.
"""
from __future__ import annotations
import math
import numpy as np

def build_route(carla_map, start_wp, length_m: float = 500.0, step_m: float = 2.0,
                rng: np.random.Generator | None = None):
    """start_wp에서 시작해 차선을 따라가는 waypoint 열. 분기에서는 가장 직진에 가까운 쪽 선택."""
    rng = rng or np.random.default_rng()
    route = [start_wp]
    cur = start_wp
    travelled = 0.0
    while travelled < length_m:
        nxt = cur.next(step_m)
        if not nxt:
            break
        if len(nxt) == 1:
            cur = nxt[0]
        else:
            cy = cur.transform.rotation.yaw
            # 진행 방향 변화가 가장 작은 분기 = 직진
            cur = min(nxt, key=lambda w: abs(((w.transform.rotation.yaw - cy + 180) % 360) - 180))
        route.append(cur)
        travelled += step_m
    return route

def _yaw_err(a: float, b: float) -> float:
    return ((a - b + 180.0) % 360.0) - 180.0

class PurePursuit:
    """전방 주시점을 향해 조향, 목표 속도는 P 제어."""
    def __init__(self, route, target_kph: float = 50.0, lookahead_m: float = 8.0,
                 wheelbase_m: float = 2.7, step_m: float = 2.0):
        self.route = route
        self.target = target_kph / 3.6
        self.la = lookahead_m
        self.L = wheelbase_m
        self.step = step_m
        self.i = 0

    def done(self) -> bool:
        return self.i >= len(self.route) - int(self.la / self.step) - 2

    def update(self, vehicle):
        """VehicleControl과 진행률(0~1)을 돌려준다."""
        import carla
        loc = vehicle.get_location()
        # 가장 가까운 경로점 갱신 (앞으로만 진행)
        best, bd = self.i, 1e18
        for j in range(self.i, min(self.i + 40, len(self.route))):
            d = loc.distance(self.route[j].transform.location)
            if d < bd:
                bd, best = d, j
        self.i = best
        tgt_idx = min(self.i + max(1, int(self.la / self.step)), len(self.route) - 1)
        tgt = self.route[tgt_idx].transform.location

        tf = vehicle.get_transform()
        yaw = math.radians(tf.rotation.yaw)
        dx, dy = tgt.x - loc.x, tgt.y - loc.y
        # 차량 좌표계로 변환
        lx = dx * math.cos(-yaw) - dy * math.sin(-yaw)
        ly = dx * math.sin(-yaw) + dy * math.cos(-yaw)
        ld = max(1e-3, math.hypot(lx, ly))
        curvature = 2.0 * ly / (ld * ld)
        steer = float(np.clip(math.atan(curvature * self.L) / math.radians(35.0), -1.0, 1.0))

        v = vehicle.get_velocity()
        spd = math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
        err = self.target - spd
        throttle = float(np.clip(0.35 * err, 0.0, 0.7))
        brake = float(np.clip(-0.25 * err, 0.0, 0.5))
        return carla.VehicleControl(throttle=throttle, steer=steer, brake=brake), self.i / max(1, len(self.route))

def route_waypoint_at(route, index: int):
    return route[min(max(0, index), len(route) - 1)]
