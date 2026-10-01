#!/usr/bin/env python3
"""`follow_lane` / `next_keeping_lane` 이 나들목에서 본선을 유지하는지 CARLA 없이 확인한다.

실제 버그: Town04 에서 `patch_wp.next(70)[0]` 이 램프(road 47 / lane -1)를 골라 정차 차량이
자차 차로에서 428 m 떨어진 곳에 놓였고, 그 결과 앞차 간격이 계속 999 로 나와 회피 판단이
아예 돌지 않았다. CARLA 서버가 배치에 묶여 있어도 검증할 수 있도록 웨이포인트를 흉내 낸다.

실행: python3 scripts/sim/test_follow_lane.py
"""
from __future__ import annotations

import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from icepredict.sim.blackice import follow_lane, next_keeping_lane  # noqa: E402

# 차로 귀속에 쓰는 선분 거리 — carla_demo 는 carla 모듈을 import 하므로 여기서 같은 구현을 옮겨 검증한다.
def _dist_to_polyline(pts, px, py):
    if not pts:
        return 1e9
    best = 1e9
    for i in range(len(pts) - 1):
        ax, ay = pts[i]; bx, by = pts[i + 1]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 <= 1e-9:
            d = math.hypot(px - ax, py - ay)
        else:
            t = ((px - ax) * vx + (py - ay) * vy) / L2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            d = math.hypot(px - (ax + t * vx), py - (ay + t * vy))
        best = min(best, d)
    if len(pts) == 1:
        best = math.hypot(px - pts[0][0], py - pts[0][1])
    return best



class Rot:
    def __init__(self, yaw):
        self.yaw = yaw


class Loc:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class Tf:
    def __init__(self, x, y, yaw):
        self.location = Loc(x, y)
        self.rotation = Rot(yaw)


class WP:
    """직선 본선(road 43 / lane -4)에 50 m 지점부터 램프가 갈라지는 가짜 지도.

    `next()` 는 분기에서 **램프를 먼저** 돌려준다. 실제 CARLA 도 순서를 보장하지 않으므로
    `[0]` 을 그대로 쓰면 램프로 새는 것을 재현한다.
    """

    def __init__(self, s, road_id=43, lane_id=-4, yaw=0.0, junction=False, ramp=False):
        self.s = s
        self.road_id = road_id
        self.lane_id = lane_id
        self.is_junction = junction
        self.ramp = ramp
        y = 0.0 if not ramp else (s - 50.0) * math.tan(math.radians(25.0))
        self.transform = Tf(s, y, yaw)

    def next(self, step):
        ns = self.s + step
        if self.ramp:
            return [WP(ns, road_id=47, lane_id=-1, yaw=25.0, ramp=True)]
        # 분기점 s=50 을 지나가면 램프 끝과 본선 끝을 **둘 다** 돌려준다 (실제 CARLA 와 같다).
        # 램프를 먼저 놓아, [0] 을 그대로 쓰면 새는 것을 재현한다.
        if self.s < 50.0 <= ns:
            return [WP(ns, road_id=47, lane_id=-1, yaw=25.0, junction=(ns <= 60.0), ramp=True),
                    WP(ns, road_id=43, lane_id=-4, yaw=0.0)]
        return [WP(ns, road_id=43, lane_id=-4, yaw=0.0)]

    def __repr__(self):
        return f"WP(s={self.s:.1f}, road={self.road_id}, lane={self.lane_id})"


def main():
    fails = 0

    start = WP(0.0)

    # 1) 순진한 next()[0] 은 램프로 샌다 — 버그 재현
    naive = start.next(70.0)[0]
    print(f"1) naive next(70)[0]        → {naive}")
    if naive.road_id == 43:
        print("   기대와 다름: 가짜 지도가 버그를 재현하지 못했다"); fails += 1
    else:
        print("   버그 재현 확인 (road 47 로 샘)")

    # 2) follow_lane 은 본선을 유지한다
    got = follow_lane(start, 70.0)
    print(f"2) follow_lane(start, 70)   → {got}")
    if got.road_id != 43 or got.lane_id != -4:
        print(f"   실패: 본선(43/-4)을 벗어났다"); fails += 1
    elif abs(got.s - 70.0) > 0.51:
        print(f"   실패: 거리 {got.s:.1f} m (기대 70 m)"); fails += 1
    else:
        print(f"   통과: 본선 유지, 거리 {got.s:.1f} m")

    # 3) 분기 직전·직후에서도 유지
    for d in (49.0, 50.0, 55.0, 61.0, 120.0):
        g = follow_lane(WP(0.0), d)
        ok = g.road_id == 43 and g.lane_id == -4 and abs(g.s - d) <= 0.51
        print(f"3) follow_lane(0, {d:5.1f}) → {g}  {'통과' if ok else '실패'}")
        if not ok:
            fails += 1

    # 4) 분기점에서 한 스텝만: 본선을 골라야 한다
    n = next_keeping_lane(WP(49.0), 2.0)
    ok = n is not None and n.road_id == 43
    print(f"4) next_keeping_lane(49, 2) → {n}  {'통과' if ok else '실패'}")
    if not ok:
        fails += 1

    # 5) 길이 끊기면 마지막 지점을 돌려준다 (예외 없이)
    class Dead(WP):
        def next(self, step):
            return []

    g = follow_lane(Dead(10.0), 50.0)
    ok = g.s == 10.0
    print(f"5) 길 끊김                  → {g}  {'통과' if ok else '실패'}")
    if not ok:
        fails += 1

    # 6) 선분 거리 — 8 m 간격 점열에서도 같은 차로 차량이 탈락하면 안 된다
    line = [(0.0, 0.0), (8.0, 0.0), (16.0, 0.0), (24.0, 0.0)]
    cases = [
        ("정확히 선 위, 점 사이(x=4)", 4.0, 0.0, 0.0),
        ("횡 1.5 m, 점 사이(x=12)", 12.0, 1.5, 1.5),
        ("횡 0 m, 점 위(x=16)", 16.0, 0.0, 0.0),
        ("횡 3.5 m (옆 차로)", 4.0, 3.5, 3.5),
    ]
    for name, px, py, exp in cases:
        got = _dist_to_polyline(line, px, py)
        ok = abs(got - exp) < 0.01
        print(f"6) {name:28s} → {got:.2f} (기대 {exp:.2f})  {'통과' if ok else '실패'}")
        if not ok:
            fails += 1
    # 점 최근접으로 재면 x=4 에서 4.0 이 나와 2 m 문턱에서 탈락한다 — 그게 고친 버그다
    near_pt = min(math.hypot(4.0 - x, 0.0 - y) for x, y in line)
    print(f"6) 참고: 점 최근접이면 {near_pt:.2f} m → 2 m 문턱에서 같은 차로 차량이 탈락했다")

    print()
    print("모두 통과" if fails == 0 else f"실패 {fails}건")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
