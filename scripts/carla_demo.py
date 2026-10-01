#!/usr/bin/env python3
"""IcePredict CARLA 데모: 자율주행 중 블랙아이스 이중 방어.

1차 (예방): 전방 카메라 → RoadNet 추론 → 베이지안 융합 위험도 > 임계값 → 경고 후 제동
2차 (사후): 1차가 놓쳐 타이어가 빙판에 닿아 미끄러지면 IMU 칼만 필터가 감지 → 비상 안정화

사용:
  python scripts/carla_demo.py                     # 1차+2차 모두 켬
  python scripts/carla_demo.py --disable-primary   # 1차 끄고 2차만 (미끄러짐 시연)
  python scripts/carla_demo.py --gt-detect         # 카메라 대신 정답 거리로 1차 판정 (도메인갭 우회)
  python scripts/carla_demo.py --no-ice-render     # 얼음 합성 끔 (엔진 화면 그대로, 1차 감지 불가)

얼음 합성에 대하여
  CARLA friction trigger는 마찰계수만 바꾸고 노면을 렌더링하지 않는다. 따라서 합성 없이는
  카메라가 볼 것이 없어 1차 방어가 원리적으로 동작할 수 없다(RoadNet의 black_ice 확률이
  0.016에 머문 진짜 이유). 여기서는 수집기와 **같은** composite_ice로 카메라 프레임에 얼음을
  합성한 뒤 그 프레임을 RoadNet에 넣고, 같은 프레임을 영상으로 저장한다. 학습 데이터와
  데모 화면의 외형이 같아야 미세조정이 데모에서 동작한다.
  한계: 얼음 외관은 우리가 정의한 합성물이므로 이 데모는 파이프라인 동작 증명이며
  실차 성능 근거가 아니다. 실차 근거는 RSCD 실사진 학습 결과가 제공한다.
"""
import argparse, sys, json, math, os, sys, time
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, cv2, carla

from icepredict.common.protocol import ROAD_CLASSES
from icepredict.pi.fusion import RiskFuser
from icepredict.pi.context import WeatherObs, LocationCtx, build_context
from icepredict.pi.imu_slip import SlipDetector, SlipEvent, emergency_command, G
from icepredict.sim.blackice import (spawn_ice_patch, waypoint_ahead, follow_lane, next_keeping_lane, RoadNetDetector,
                                     camera_intrinsics, patch_road_polygon, draw_patch_overlay,
                                     patch_image_mask)
from icepredict.sim.ice_render import composite_ice, random_ice_params
from icepredict.sim import camera as camcfg

ICE = ROAD_CLASSES.index("black_ice")

def spawn_native_ice(world, bl, wp, patch, tile=7.0, rough=0.05):
    """엔진 안에 실제로 광택 나는 얇은 평면 타일을 깐다 (static.prop.mesh + 런타임 재질).
    거칠기 0.05 → 엔진이 하늘/조명을 실제로 반사한다. 조감·라이다 시점에서도 '진짜 도로 위 빙판'처럼 보이고,
    합성과 달리 날씨·시간에 따라 외관이 저절로 바뀐다. 1인칭 모델 입력은 여전히 학습 분포인 합성 얼음을 쓴다."""
    tf = wp.transform; f = tf.get_forward_vector(); c = patch.location
    n = max(1, int(math.ceil(2 * patch.extent[0] / tile)))
    before = set(world.get_names_of_all_objects()); tiles = []
    for i in range(n):
        d = (i - (n - 1) / 2) * tile
        bp = bl.find("static.prop.mesh")
        bp.set_attribute("mesh_path", "/Engine/BasicShapes/Plane.Plane")
        bp.set_attribute("scale", str(tile)); bp.set_attribute("mass", "0")
        act = world.try_spawn_actor(bp, carla.Transform(carla.Location(c.x + f.x * d, c.y + f.y * d, tf.location.z + 0.01),
                                                        carla.Rotation(yaw=tf.rotation.yaw)))
        if act is not None:
            act.set_simulate_physics(False); tiles.append(act)
    for _ in range(3): world.tick()
    new = set(world.get_names_of_all_objects()) - before
    S = 4      # 텍셀이 크면 노이즈가 격자무늬로 보인다 → 균일한 색 (젖은 아스팔트: 어둡고 약간 푸른 회색)
    diff = carla.TextureColor(S, S); arm = carla.TextureFloatColor(S, S)
    for y in range(S):
        for x in range(S):
            diff.set(x, y, carla.Color(44, 45, 48, 255))
            arm.set(x, y, carla.FloatColor(1.0, rough, 0.0, 0.0))
    ok = 0
    for name in new:
        try:
            world.apply_color_texture_to_object(name, carla.MaterialParameter.Diffuse, diff)
            world.apply_float_color_texture_to_object(name, carla.MaterialParameter.AO_Roughness_Metallic_Emissive, arm)
            ok += 1
        except Exception as e:
            print(f"[ice] 텍스처 적용 실패 {name}: {e}", flush=True)
    # get_names_of_all_objects() 가 런타임 스폰 액터를 포함하지 않으면 new 가 비고, 타일은 UE4 기본
    # 재질(밝은 회색 체커)로 남는다. 렌더 프레임에서 보이던 회색 격자가 바로 그 징후다.
    print(f"[ice] 타일 {len(tiles)}개 스폰, 텍스처 대상 {len(new)}개, 적용 성공 {ok}개", flush=True)
    return tiles, sorted(new)

def _same_dir_lane(l, ref):
    return l is not None and l.lane_type == carla.LaneType.Driving and (l.lane_id * ref.lane_id) > 0

def spawn_traffic(world, bl, cmap, tm, ego, n, patch_wp, lead_stop, rng):
    """옆 차로(같은 방향)에 자율주행 차량 n대, 필요하면 자차 차로 앞에 정차 차량 1대(비상등)."""
    ego_wp = cmap.get_waypoint(ego.get_location())
    cars = [b for b in bl.filter("vehicle.*") if b.get_attribute("number_of_wheels").as_int() == 4
            and not any(k in b.id for k in ("carlacola", "firetruck", "ambulance", "sprinter", "t2", "cybertruck", "fusorosa", "european_hgv",
                                            "microlino", "isetta", "carlamotors"))]
    lanes = [l for l in (ego_wp.get_left_lane(), ego_wp.get_right_lane()) if _same_dir_lane(l, ego_wp)]
    spawned = []
    dists = [-45.0, -25.0, 35.0, 55.0, 80.0, 105.0, 130.0, 160.0, 190.0, 220.0, 250.0, 280.0]
    rng.shuffle(dists)
    for d in dists[:max(0, n)]:
        if not lanes: break
        base = lanes[int(rng.integers(len(lanes)))]
        wps = base.next(d) if d > 0 else base.previous(-d)
        if not wps: continue
        bp = cars[int(rng.integers(len(cars)))]
        if bp.has_attribute("color"):
            bp.set_attribute("color", rng.choice(bp.get_attribute("color").recommended_values))
        tf = wps[0].transform; tf.location.z += 0.3
        v = world.try_spawn_actor(bp, tf)
        if v is None: continue
        v.set_autopilot(True, 8000); autopiloted.append(v)
        if TIRE_MU > 0:
            try:
                pc = v.get_physics_control()
                for wh in pc.wheels: wh.tire_friction = float(TIRE_MU)
                v.apply_physics_control(pc)
            except Exception: pass
        tm.auto_lane_change(v, False); tm.ignore_lights_percentage(v, 100)
        tm.vehicle_percentage_speed_difference(v, float(rng.uniform(-5, 35)))
        spawned.append(v)
    lead = None
    if lead_stop > 0:
        # 빙판 중심 웨이포인트(자차 차로 위)에서 lead_stop 만큼 앞.
        # 주의: 예전 주석의 "428 m 밖에 놓였다"는 **오진**이었다. 동기 모드에서 스폰 직후의
        # get_location() 은 아직 갱신되지 않아 (0,0,0) 을 주고, 그 값으로 재면 원점까지 거리 428 m 가
        # 나오며 원점 근처 도로(47/-1)로 스냅된다. 틱을 돌린 뒤 재면 실제로는 제대로 놓여 있다.
        # follow_lane 은 그와 별개로 나들목에서 램프로 새지 않도록 2 m 씩 본선을 따라 걷는다.
        # 어디서 차로가 갈리는지 보려고 10 m 마다 road/lane 을 찍는다 (한 주행에 7줄).
        _dbg = patch_wp; _tr = [f"0m:{_dbg.road_id}/{_dbg.lane_id}"]
        for _k in range(int(lead_stop // 10)):
            _nx = follow_lane(_dbg, 10.0)
            if _nx is None: break
            _dbg = _nx; _tr.append(f"{(_k+1)*10}m:{_dbg.road_id}/{_dbg.lane_id}")
        print("[traffic] 차로 추적 " + " → ".join(_tr), flush=True)
        lwp = follow_lane(patch_wp, lead_stop)
        if lwp is not None:
            _pl = patch_wp.transform.location; _ll = lwp.transform.location
            print(f"[traffic] follow_lane 결과 {lwp.road_id}/{lwp.lane_id} "
                  f"빙판에서 {_pl.distance(_ll):.0f} m, 좌표 ({_ll.x:.0f},{_ll.y:.0f})", flush=True)
            bp = [b for b in cars if "tesla" not in b.id][0]
            tf = lwp.transform; tf.location.z += 0.3
            lead = world.try_spawn_actor(bp, tf)
            if lead is None:
                print("[traffic] 그 자리에 스폰 실패 — 뒤로 물려 재시도", flush=True)
                for _back in (5.0, 10.0, 15.0, 20.0):
                    _alt = follow_lane(patch_wp, max(lead_stop - _back, 5.0))
                    _t2 = _alt.transform; _t2.location.z += 0.3
                    lead = world.try_spawn_actor(bp, _t2)
                    if lead is not None:
                        print(f"[traffic] {lead_stop - _back:.0f} m 지점에 스폰 성공", flush=True)
                        break
            if lead is not None:
                # 스폰 직후에는 위치를 읽지 않는다 — 동기 모드에서 아직 (0,0,0) 이라
                # "428 m 밖에 놓였다"는 가짜 경고가 찍히고, 실제로 그 오진이 네 세션을 헛돌게 했다.
                # 진짜 확인은 월드 틱을 돌린 뒤 본문에서 한다 ("[traffic] (틱 후 확인) ...").
                pass
                try:
                    lead.set_light_state(carla.VehicleLightState(carla.VehicleLightState.LeftBlinker | carla.VehicleLightState.RightBlinker | carla.VehicleLightState.Brake))
                except Exception: pass
                lead.apply_control(carla.VehicleControl(hand_brake=True, brake=1.0))
    return spawned, lead

def wrap_pi(a):
    return (a + math.pi) % (2 * math.pi) - math.pi

def lane_state(cmap, ego):
    """차선 유지 제어 입력: (차선 방향 − 차량 방향) rad, 차선 중심의 횡위치 m(오른쪽 양수), 현재 웨이포인트."""
    tf = ego.get_transform(); loc = tf.location; yaw = math.radians(tf.rotation.yaw)
    wp = cmap.get_waypoint(loc, project_to_road=True, lane_type=carla.LaneType.Driving)
    nxt = wp.next(6.0); tgt = (nxt[0] if nxt else wp).transform.location
    lane_err = wrap_pi(math.atan2(tgt.y - loc.y, tgt.x - loc.x) - yaw)
    c = wp.transform.location; dx, dy = c.x - loc.x, c.y - loc.y
    lat_off = -dx * math.sin(yaw) + dy * math.cos(yaw)
    return lane_err, lat_off, wp

def lane_center_offset(cmap, ego, wp, target_lane_id):
    """같은 도로에서 lane_id 가 target 인 차로 중심의 자차 기준 횡위치(m, 오른쪽 양수). 못 찾으면 0."""
    tf = ego.get_transform(); loc = tf.location; yaw = math.radians(tf.rotation.yaw)
    cand = wp
    for _ in range(4):
        if cand is None or cand.lane_id == target_lane_id: break
        step = cand.get_left_lane() if (target_lane_id - cand.lane_id) * cand.lane_id > 0 else cand.get_right_lane()
        cand = step if (step is not None and step.road_id == wp.road_id) else None
    if cand is None or cand.lane_id != target_lane_id: return 0.0
    c = cand.transform.location; dx, dy = c.x - loc.x, c.y - loc.y
    return -dx * math.sin(yaw) + dy * math.cos(yaw)

def _lane_polyline(base, ahead=170.0, step=8.0):
    """차로 중심선을 세계 좌표 점열로 만든다. `next_keeping_lane` 으로 걸으므로 나들목에서 램프로 새지 않는다."""
    pts = []
    if base is None:
        return pts
    cur = base
    l0 = cur.transform.location
    pts.append((l0.x, l0.y))
    d = 0.0
    while d < ahead:
        nxt = next_keeping_lane(cur, step)
        if nxt is None:
            break
        cur = nxt
        d += step
        l = cur.transform.location
        pts.append((l.x, l.y))
    return pts


def _dist_to_polyline(pts, px, py):
    """점 (px,py) 에서 점열이 이루는 꺾은선까지의 최단거리.

    가장 가까운 **점**까지 재면 점 간격의 절반만큼 오차가 생긴다(8 m 간격이면 최대 4 m).
    그 오차가 2 m 판정 문턱을 넘어 같은 차로 차량을 떨어뜨리므로 반드시 **선분**으로 재야 한다.
    """
    if not pts:
        return 1e9
    best = 1e9
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 <= 1e-9:
            d = math.hypot(px - ax, py - ay)
        else:
            t = ((px - ax) * vx + (py - ay) * vy) / L2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            d = math.hypot(px - (ax + t * vx), py - (ay + t * vy))
        if d < best:
            best = d
    if len(pts) == 1:
        best = math.hypot(px - pts[0][0], py - pts[0][1])
    return best


def neighbor_gaps(vehicles, ego, wp):
    """레이더/라이다가 줄 값을 시뮬에서 계산: 같은 차로 앞차 간격, 왼쪽·오른쪽 차로 가장 가까운 차 (없으면 999, 차로 없음 -1).

    차로 판정은 자차 좌표계 횡거리로 하면 안 된다 — 굽은 고속도로에서는 100 m 앞의 같은 차로 차량도
    횡거리가 1.75 m 를 넘어 '옆 차로'로 오판된다. 그렇다고 `base.next(fx)[0]` 로 중심점을 한 번에
    잡아도 안 된다 — 나들목에서 `[0]` 이 램프를 고르면 같은 차로 앞차가 통째로 탈락한다.
    그래서 차로마다 **중심선 점열**을 만들어(램프로 새지 않는 `next_keeping_lane` 으로) 그 꺾은선까지의
    **선분 거리**로 판정한다. 점 최근접으로 재면 점 간격의 절반(8 m 간격이면 4 m)이 오차로 들어가
    2 m 문턱에서 같은 차로 차량이 또 탈락한다. 점열은 10틱마다만 다시 만든다.
    """
    tf = ego.get_transform(); loc = tf.location; yaw = math.radians(tf.rotation.yaw); c, s_ = math.cos(yaw), math.sin(yaw)
    gf = gl = gr = 999.0; neighbor_gaps.front_rel_v = 0.0; neighbor_gaps.n = getattr(neighbor_gaps, "n", 0) + 1
    ev_ = ego.get_velocity(); ego_v = math.hypot(ev_.x, ev_.y)
    lw = wp.get_left_lane() if _same_dir_lane(wp.get_left_lane(), wp) else None
    rw = wp.get_right_lane() if _same_dir_lane(wp.get_right_lane(), wp) else None
    # 중심선 점열은 매 틱 다시 만들 필요가 없다. 차로가 바뀌거나 10틱이 지나면 다시 만든다.
    # (자차는 틱당 0.2 m 남짓 움직이므로 조금 뒤처진 점열도 같은 차선을 그대로 기술한다.)
    key = (wp.road_id, wp.lane_id)
    cache = getattr(neighbor_gaps, "_poly", None)
    if cache is None or cache[0] != key or neighbor_gaps.n - cache[1] > 10:
        neighbor_gaps._poly = (key, neighbor_gaps.n,
                               {"f": _lane_polyline(wp), "l": _lane_polyline(lw), "r": _lane_polyline(rw)})
    polys = neighbor_gaps._poly[2]
    for v in vehicles:
        try: p = v.get_location()
        except Exception: continue
        dx, dy = p.x - loc.x, p.y - loc.y
        fx = dx * c + dy * s_
        if fx < -40 or fx > 150: continue
        # 차로 귀속: 각 차로 중심선 점열에서 그 차량에 가장 가까운 점까지의 거리로 판단한다.
        # 예전 구현은 `base.next(fx)[0]` 한 방으로 중심점을 잡았는데, 나들목에서 [0] 이 램프를 고르면
        # 같은 차로 앞차도 멀리 떨어진 것으로 계산돼 앞차 간격이 999 로 남았다 (회피 시나리오가 안 돌던 원인).
        best, best_d = None, 1e9
        if fx >= 0:
            for name in ("f", "l", "r"):
                pts = polys.get(name) or []
                if not pts:
                    continue
                d_ = _dist_to_polyline(pts, p.x, p.y)
                if d_ < best_d:
                    best, best_d = name, d_
        else:
            # 뒤차는 옆 차로 간격(40 m 이내)에만 쓰이므로 자차 기준 횡거리로 충분하다.
            fy = -dx * s_ + dy * c
            lane_w = 3.5
            if abs(fy) < lane_w / 2:
                best, best_d = "f", abs(fy)
            elif fy < 0:
                best, best_d = "l", abs(abs(fy) - lane_w)
            else:
                best, best_d = "r", abs(abs(fy) - lane_w)
        if os.environ.get("ICE_GAP_DEBUG") and (getattr(neighbor_gaps, "n", 0) % 50 == 0):
            print(f"[gapdbg] {v.type_id[-14:]:14s} fx={fx:7.1f} fy={(-dx * s_ + dy * c):6.1f} best={best} d={best_d:6.1f} ego_lane={wp.road_id}/{wp.lane_id}", flush=True)
        if best is None or best_d > 2.0: continue
        if best == "f" and fx > 0:
            if fx - 4.6 < gf:
                gf = fx - 4.6
                try: vv = v.get_velocity(); neighbor_gaps.front_rel_v = math.hypot(vv.x, vv.y) - ego_v
                except Exception: neighbor_gaps.front_rel_v = 0.0
        elif best == "l" and abs(fx) < 40: gl = min(gl, abs(fx))
        elif best == "r" and abs(fx) < 40: gr = min(gr, abs(fx))
    if lw is None: gl = -1.0
    if rw is None: gr = -1.0
    return gf, gl, gr

class LocalEmergency:
    """보드 slip_control() 의 파이썬 참조 구현 (보드 무응답 시 폴백). 상수·논리 동일."""
    KP, KL, KD, SMAX, A_ICE, GAP_FREE, LANE_W = 1.4, 0.15, 0.30, 0.5, 1.0, 20.0, 3.5
    MODES = {0: "none", 1: "lane_keep", 2: "evade_left", 3: "evade_right", 4: "hard_stop", 5: "stopped"}
    def __init__(self): self.n = 0; self.mode = 0; self.shift = 0.0
    def step(self, speed, gz, lane_err, lat_off, gf, gl, gr):
        self.n += 1
        d_stop = speed * speed / (2 * self.A_ICE) + 4.0
        if self.mode in (0, 1, 4):
            if gf < d_stop:
                if gr >= self.GAP_FREE: self.mode, self.shift = 3, +self.LANE_W
                elif gl >= self.GAP_FREE: self.mode, self.shift = 2, -self.LANE_W
                else: self.mode, self.shift = 4, 0.0
            else: self.mode, self.shift = 1, 0.0
        st = max(-self.SMAX, min(self.SMAX, (self.KP * lane_err + self.KL * (lat_off + self.shift) - self.KD * gz) / 0.61086524))
        pulse = (self.n >> 3) & 1
        if self.mode in (2, 3): b = 0.25 if abs(lat_off + self.shift) > 0.8 else (0.7 if pulse else 0.35)
        elif self.mode == 4: b = 0.95 if pulse else 0.5
        else: b = 0.7 if pulse else 0.35
        if speed < 2.0: b = 1.0
        if speed < 0.5: self.mode, b, st = 5, 1.0, 0.0
        return b, st, self.MODES[self.mode]

def build_mosaic(files, size=1024, tile=4, rng=None):
    """실제 노면 사진 여러 장을 격자로 이어 붙이고 경계를 부드럽게 섞어 타일링용 텍스처(BGR)를 만든다."""
    rng = rng or np.random.default_rng(0)
    # 후보 사진에서 (1) 가까운 노면인 아래쪽 중앙을 정사각형으로 잘라 원근을 줄이고, (2) 경계 에너지가 낮은(차체·연석·차선이
    # 안 섞인) 것만 고른다. 그대로 이어 붙이면 잡동사니와 기울기가 격자마다 보인다 (실제로 그랬다).
    cand = []
    for f in [files[int(i)] for i in rng.permutation(len(files))[:min(len(files), 160)]]:
        im = cv2.imread(str(f))
        if im is None: continue
        h, w = im.shape[:2]; sq = min(w, int(h * 0.55)); x0 = (w - sq) // 2; y0 = h - sq
        crop = im[y0:y0 + sq, x0:x0 + sq]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY); e = float(cv2.Laplacian(cv2.GaussianBlur(g, (5, 5), 0), cv2.CV_32F).var())
        if float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].mean()) > 45: continue   # 푸른 하늘·차체 반사가 든 사진 제외
        cand.append((e, crop))
    cand.sort(key=lambda t: t[0])
    keep = [c for _, c in cand[:max(tile * tile, len(cand) // 3)]]
    pick = [keep[int(i)] for i in rng.choice(len(keep), tile * tile, replace=len(keep) < tile * tile)]
    # 셀 간 색조(LAB a,b) 를 중앙값으로 맞춘다 — 사진마다 화이트밸런스가 달라 격자가 색으로 드러난다
    labs = [cv2.cvtColor(c, cv2.COLOR_BGR2LAB).astype(np.float32) for c in pick]
    med_a = float(np.median([l[:, :, 1].mean() for l in labs])); med_b = float(np.median([l[:, :, 2].mean() for l in labs]))
    for i, l in enumerate(labs):
        l[:, :, 1] += med_a - l[:, :, 1].mean(); l[:, :, 2] += med_b - l[:, :, 2].mean()
        pick[i] = cv2.cvtColor(np.clip(l, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    cell = size // tile; canvas = np.zeros((size, size, 3), np.float32); wsum = np.zeros((size, size, 1), np.float32)
    k3 = cell // 3
    ramp = np.linspace(0, 1, k3, dtype=np.float32)
    w1 = np.ones(cell, np.float32); w1[:k3] = ramp; w1[cell - k3:] = ramp[::-1]
    wcell = (w1[:, None] * w1[None, :])[:, :, None]
    ov = cell // 4
    k = 0
    for r in range(tile):
        for c in range(tile):
            im = np.rot90(pick[k], int(rng.integers(4))); k += 1
            im = cv2.resize(np.ascontiguousarray(im), (cell + 2 * ov, cell + 2 * ov)).astype(np.float32)
            im *= (float(np.mean([c.mean() for c in pick])) / max(im.mean(), 1.0))      # 셀 간 노출 맞춤
            y0, x0 = r * cell - ov, c * cell - ov
            ys, xs = slice(max(y0, 0), min(y0 + cell + 2 * ov, size)), slice(max(x0, 0), min(x0 + cell + 2 * ov, size))
            wc = cv2.resize(wcell, (cell + 2 * ov, cell + 2 * ov))[:, :, None]
            iy, ix = slice(ys.start - y0, ys.stop - y0), slice(xs.start - x0, xs.stop - x0)
            canvas[ys, xs] += im[iy, ix] * wc[iy, ix]; wsum[ys, xs] += wc[iy, ix]
    out = canvas / np.maximum(wsum, 1e-3)
    # 전체 밝기·색 평균을 아스팔트답게 (사진마다 노출이 달라 격자가 보이는 것을 막는다)
    lab = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 0] = cv2.GaussianBlur(lab[:, :, 0], (0, 0), 3) * 0 + (lab[:, :, 0] - cv2.GaussianBlur(lab[:, :, 0], (0, 0), 60) + lab[:, :, 0].mean())
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

def texture_from_bgr(img):
    h, w = img.shape[:2]; t = carla.TextureColor(w, h)
    for y in range(h):
        row = img[y]
        for x in range(w):
            b, g, r = row[x]; t.set(x, y, carla.Color(int(r), int(g), int(b), 255))
    return t

def apply_real_road_textures(world, folder, ice_class, tile_names, rng):
    """RSCD 사진으로 도로(마른 아스팔트)·빙판 타일(ice 등) 재질을 바꾼다. 반환: (도로 객체 수, 타일 수)"""
    import pathlib
    root = pathlib.Path(folder)
    dry = sorted(root.rglob("*-dry-asphalt-smooth.jpg"))[:400]
    ice = sorted(root.rglob(f"*-{ice_class}.jpg"))[:400]
    if not dry or not ice: raise RuntimeError(f"RSCD 사진 없음 (dry {len(dry)}, {ice_class} {len(ice)})")
    road_tex = texture_from_bgr(build_mosaic(dry, size=512, tile=4, rng=rng))
    ice_tex = texture_from_bgr(build_mosaic(ice, size=512, tile=2, rng=rng))
    roads = [n for n in world.get_names_of_all_objects() if n.startswith("Road_Road_")]
    world.apply_color_texture_to_objects(roads, carla.MaterialParameter.Diffuse, road_tex)
    if tile_names:
        world.apply_color_texture_to_objects(list(tile_names), carla.MaterialParameter.Diffuse, ice_tex)
    return len(roads), len(tile_names)

SNOW_PREFIXES = ("Town04_TerrainNode", "Road_Grass_", "Road_Sidewalk_", "Road_Curb_")
def snow_apply(world):
    """쌓인 눈: 지형·잔디·인도·연석 물체의 재질을 런타임 API로 흰색(거칠기 0.85, 무광)으로 바꾼다.
    CARLA에 눈 프리셋이 없어 직접 만든다. 도로는 그대로 두어(제설된 도로) 빙판 구간이 살아 있게 한다.
    엔진 물체를 바꾸므로 주행이 끝나면 reload_world 로 되돌려야 한다."""
    names = [n for n in world.get_names_of_all_objects() if n.startswith(SNOW_PREFIXES)]
    S = 16; rng = np.random.default_rng(3)
    diff = carla.TextureColor(S, S); arm = carla.TextureFloatColor(S, S)
    for y in range(S):
        for x in range(S):
            v = int(np.clip(232 + rng.normal(0, 5), 215, 250))
            diff.set(x, y, carla.Color(v - 4, v - 2, v, 255))
            arm.set(x, y, carla.FloatColor(1.0, 0.85, 0.0, 0.0))
    world.apply_color_texture_to_objects(names, carla.MaterialParameter.Diffuse, diff)
    world.apply_float_color_texture_to_objects(names, carla.MaterialParameter.AO_Roughness_Metallic_Emissive, arm)
    return len(names)

def snow_cover(bgr, seg, rng):
    """쌓인 눈(2D): 지형·인도·연석은 거의 흰색으로, 초목은 서리 낀 듯 절반만. 노면(1)·차선·차량은 그대로 (제설 도로)."""
    out = bgr.astype(np.float32)
    white = np.array([238, 242, 246], np.float32)
    strong = np.isin(seg, (10, 2, 22)); weak = seg == 9
    noise = rng.normal(0, 6, bgr.shape[:2]).astype(np.float32)[:, :, None]
    out[strong] = 0.15 * out[strong] + 0.85 * (white + noise[strong])
    out[weak] = 0.55 * out[weak] + 0.45 * white
    return np.clip(out, 0, 255).astype(np.uint8)

class SnowField:
    """내리는 눈 (2D 합성). 입자가 프레임 간에 이어져 떨어지고 바람에 흔들린다. 카메라 앞 렌즈에 가까운 큰 입자 소수 + 먼 작은 입자 다수."""
    def __init__(self, w, h, n=700, seed=5):
        self.w, self.h = w, h; self.rng = np.random.default_rng(seed)
        self.x = self.rng.uniform(0, w, n); self.y = self.rng.uniform(0, h, n)
        self.depth = self.rng.uniform(0.15, 1.0, n) ** 2          # 1 = 가까움
        self.phase = self.rng.uniform(0, 6.28, n)
    def step(self, img, t, wind=0.6, haze=0.10):
        v = 2.2 + 9.0 * self.depth
        self.y = (self.y + v) % self.h
        self.x = (self.x + wind * (1 + 2 * self.depth) + 0.8 * np.sin(t * 2.0 + self.phase)) % self.w
        out = cv2.addWeighted(img, 1 - haze, np.full_like(img, 235), haze, 0)   # 눈 오는 날의 뿌연 공기
        layer = np.zeros_like(img); alpha = np.zeros(img.shape[:2], np.float32)
        for xi, yi, d, vv in zip(self.x.astype(int), self.y.astype(int), self.depth, v):
            r = 1 if d < 0.6 else 2
            a_ = 0.35 + 0.5 * d
            cv2.circle(layer, (xi, yi), r, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(alpha, (xi, yi), r, float(a_), -1, cv2.LINE_AA)
            if d > 0.7:                                                        # 가까운 입자만 짧은 낙하 궤적
                cv2.line(layer, (xi, yi), (int(xi - wind), int(yi - vv * 0.5)), (255, 255, 255), 1, cv2.LINE_AA)
                cv2.line(alpha, (xi, yi), (int(xi - wind), int(yi - vv * 0.5)), float(a_ * 0.6), 1, cv2.LINE_AA)
        alpha = cv2.GaussianBlur(alpha, (3, 3), 0)[:, :, None]
        return (out * (1 - alpha) + layer * alpha).astype(np.uint8)

def _height_colors(z, inten, sensor_z):
    """높이(노면 0 → 4m)로 TURBO 색, 노면 근처는 차분한 청회색, 반사강도로 밝기."""
    hgt = z + sensor_z
    hn = np.clip(hgt / 4.0, 0, 1)
    # applyColorMap 은 1차원 입력을 (1×N)으로 볼 때가 있어 결과 형태가 흔들린다 → (N×1)로 명시하고 (N×3)으로 편다
    cm = cv2.applyColorMap((hn * 255).astype(np.uint8).reshape(-1, 1), cv2.COLORMAP_TURBO).reshape(-1, 3).astype(np.float32)
    ground = hgt < 0.18
    cm[ground] = np.array([150, 110, 70], np.float32)            # 노면: 청회색(BGR)
    it = np.clip(0.55 + 0.6 * inten, 0.55, 1.15)[:, None]
    return np.clip(cm * it, 0, 255).astype(np.uint8)

# CARLA 시맨틱 태그 → 색 (WADS 그림처럼 클래스마다 뚜렷한 색, BGR)
SEM_COLORS = {1: (200, 60, 200), 24: (255, 255, 255), 2: (180, 200, 230), 3: (0, 200, 240), 4: (0, 160, 200), 5: (0, 120, 180),
              6: (200, 200, 200), 7: (0, 220, 255), 8: (0, 200, 255), 9: (60, 200, 60), 10: (90, 150, 110), 11: (0, 0, 0),
              12: (0, 0, 255), 13: (0, 0, 255), 14: (255, 120, 40), 15: (255, 120, 40), 16: (255, 120, 40), 18: (255, 120, 40), 19: (255, 120, 40),
              20: (60, 60, 60), 22: (170, 170, 170), 0: (120, 120, 120)}
SEM_ICE = (255, 255, 80)      # 빙판 구간 점 (청록)
SEM_SNOW = (245, 245, 245)    # 내리는 눈 입자
LIDAR_DT = np.dtype([("x", np.float32), ("y", np.float32), ("z", np.float32), ("cos", np.float32), ("idx", np.uint32), ("tag", np.uint32)])

def sem_colors(tags):
    out = np.full((len(tags), 3), 120, np.uint8)
    for t, c in SEM_COLORS.items(): out[tags == t] = c
    return out

def world_to_lidar(locs, ego_tf, sensor_z=2.4):
    """월드 좌표 리스트 → 라이다 프레임 (x앞, y오른, z위; 노면 ≈ -sensor_z)."""
    yaw = math.radians(ego_tf.rotation.yaw); c_, s_ = math.cos(yaw), math.sin(yaw); e = ego_tf.location
    return np.array([[(p.x - e.x) * c_ + (p.y - e.y) * s_, -(p.x - e.x) * s_ + (p.y - e.y) * c_, p.z - e.z - sensor_z] for p in locs], np.float32)

def vehicle_boxes(vehicles, ego_tf, sensor_z=2.4, max_range=90.0):
    """주변 차량의 3D 바운딩 박스 꼭짓점(8×3, 라이다 프레임)."""
    out = []
    for v in vehicles:
        try:
            tf = v.get_transform()
            if tf.location.distance(ego_tf.location) > max_range: continue
            verts = v.bounding_box.get_world_vertices(tf)
            out.append(world_to_lidar(verts, ego_tf, sensor_z))
        except Exception: continue
    return out

def render_lidar3d(pts, ego_tf, poly, col, w=640, h=480, sensor_z=2.4, hfov_deg=72.0, colors=None, boxes=None, title=None):
    """라이다 점군을 차 뒤 위쪽 가상 카메라에서 본 3D 원근 그림으로. 좌표는 라이다 프레임(x앞, y오른, z위, 노면 z=-sensor_z)."""
    img = np.full((h, w, 3), 14, np.uint8)
    C = np.array([-15.0, 0.0, 5.5], np.float32)                   # 차 15m 뒤, 노면 위 7.9m
    tgt = np.array([14.0, 0.0, -sensor_z], np.float32)
    f = tgt - C; f /= np.linalg.norm(f)
    up = np.array([0, 0, 1], np.float32); r = np.cross(up, f); r /= np.linalg.norm(r); u = np.cross(f, r)
    fx = (w / 2) / math.tan(math.radians(hfov_deg) / 2); cx, cy = w / 2, h * 0.5
    def proj(P):                                                   # P: (N,3) → (N,2) 픽셀, depth
        Q = P - C; d = Q @ f; ok = d > 0.8
        x = cx + fx * (Q @ r) / np.maximum(d, 1e-3); y = cy - fx * (Q @ u) / np.maximum(d, 1e-3)
        return x, y, d, ok
    # 지면 격자 (10m 간격) — 깊이 단서
    for X in range(0, 81, 10):
        xs, ys, d, ok = proj(np.array([[X, -14, -sensor_z], [X, 14, -sensor_z]], np.float32))
        if ok.all(): cv2.line(img, (int(xs[0]), int(ys[0])), (int(xs[1]), int(ys[1])), (34, 34, 34), 1, cv2.LINE_AA)
        if ok.all() and X: cv2.putText(img, f"{X}m", (int(xs[1]) + 4, int(ys[1])), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (80, 80, 80), 1)
    for Y in range(-14, 15, 7):
        xs, ys, d, ok = proj(np.array([[-6, Y, -sensor_z], [80, Y, -sensor_z]], np.float32))
        if ok.all(): cv2.line(img, (int(xs[0]), int(ys[0])), (int(xs[1]), int(ys[1])), (34, 34, 34), 1, cv2.LINE_AA)
    # 빙판 구간 (지면 폴리곤을 차량→라이다 프레임으로)
    yaw = math.radians(ego_tf.rotation.yaw); c_, s_ = math.cos(yaw), math.sin(yaw)
    left, right = poly
    P = []
    for p in list(left) + list(reversed(right)):
        dx, dy = p.x - ego_tf.location.x, p.y - ego_tf.location.y
        P.append([dx * c_ + dy * s_, -dx * s_ + dy * c_, -sensor_z + 0.02])
    if len(P) >= 3:
        xs, ys, d, ok = proj(np.array(P, np.float32))
        if ok.sum() >= 3:
            arr = np.stack([xs[ok], ys[ok]], 1).astype(np.int32)
            ov = img.copy(); cv2.fillPoly(ov, [arr], col); img = cv2.addWeighted(ov, 0.35, img, 0.65, 0)
            cv2.polylines(img, [arr], True, col, 2, cv2.LINE_AA)
    # 점군: 먼 점부터 그려 가까운 점이 위에 오게, 가까울수록 크게
    if pts is not None and len(pts):
        xs, ys, d, ok = proj(pts[:, :3])
        ok &= (xs >= 1) & (xs < w - 1) & (ys >= 1) & (ys < h - 1)
        xs, ys, d = xs[ok].astype(np.int32), ys[ok].astype(np.int32), d[ok]
        colr = colors[ok] if colors is not None else _height_colors(pts[ok, 2], pts[ok, 3], sensor_z)
        order = np.argsort(-d); xs, ys, d, colr = xs[order], ys[order], d[order], colr[order]
        img[ys, xs] = colr                                          # 1px (모두)
        near = d < 45
        for ox, oy in ((1, 0), (0, 1), (1, 1)):                     # 2x2 (중간 거리)
            img[ys[near] + oy, xs[near] + ox] = colr[near]
        vnear = d < 22
        for ox, oy in ((-1, 0), (0, -1), (-1, -1), (1, -1), (-1, 1)):  # 3x3 (가까운 점)
            img[ys[vnear] + oy, xs[vnear] + ox] = colr[vnear]
    # 주변 차량 3D 박스 (AI Hub 라이다 3D 박스 형식)
    EDGES = ((0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4), (0, 4), (1, 5), (2, 6), (3, 7))
    for bxv in (boxes or []):
        bx, by, bd, bok = proj(bxv)
        if bok.sum() < 8: continue
        pt = [(int(x), int(y)) for x, y in zip(bx, by)]
        for i, j in EDGES: cv2.line(img, pt[i], pt[j], (0, 255, 120), 1, cv2.LINE_AA)
        cv2.putText(img, f"{float(np.hypot(bxv[:, 0].mean(), bxv[:, 1].mean())):.0f}m", (min(p[0] for p in pt), min(p[1] for p in pt) - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 120), 1)
    # 자차 박스 (와이어프레임)
    L0, L1, W_, Z0, Z1 = -2.4, 2.3, 0.95, -sensor_z, -sensor_z + 1.5
    box = np.array([[L0, -W_, Z0], [L1, -W_, Z0], [L1, W_, Z0], [L0, W_, Z0], [L0, -W_, Z1], [L1, -W_, Z1], [L1, W_, Z1], [L0, W_, Z1]], np.float32)
    bx, by, bd, bok = proj(box)
    if bok.all():
        pt = [(int(x), int(y)) for x, y in zip(bx, by)]
        for i, j in ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)):
            cv2.line(img, pt[i], pt[j], (255, 255, 255), 1, cv2.LINE_AA)
        cv2.line(img, pt[1], pt[2], (0, 255, 255), 2, cv2.LINE_AA)   # 앞면 강조
    # 높이 색 범례 + 작은 평면도
    if colors is None:
        bar = cv2.applyColorMap(np.linspace(255, 0, 120).astype(np.uint8)[:, None], cv2.COLORMAP_TURBO)
        img[100:220, w - 26:w - 14] = bar
        cv2.putText(img, "4m", (w - 40, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
        cv2.putText(img, "0m", (w - 40, 232), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
    else:
        for i, (name, c) in enumerate((("road", SEM_COLORS[1]), ("lane", SEM_COLORS[24]), ("ICE PATCH", SEM_ICE), ("vehicle", SEM_COLORS[14]),
                                        ("vegetation", SEM_COLORS[9]), ("building/wall", SEM_COLORS[3]), ("pole", SEM_COLORS[6]), ("snow", SEM_SNOW))):
            y = 96 + 16 * i; cv2.rectangle(img, (w - 120, y - 9), (w - 108, y + 3), c, -1)
            cv2.putText(img, name, (w - 104, y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (210, 210, 210), 1)
    mini = render_lidar(pts, ego_tf, poly, col, w=200, h=150, ppm=1.7, sensor_z=sensor_z)
    img[h - 150 - 40:h - 40, w - 200:w] = mini
    cv2.rectangle(img, (w - 200, h - 190), (w - 1, h - 41), (90, 90, 90), 1)
    return img

def render_lidar(pts, ego_tf, poly, col, w=640, h=480, ppm=5.0, sensor_z=2.4):
    """라이다 점군을 위에서 본 그림으로. 높이로 색을 입히고, 빙판 폴리곤을 차량 좌표계로 옮겨 겹친다."""
    img = np.full((h, w, 3), 18, np.uint8)
    cx, cy = w // 2, int(h * 0.78)
    for r in (10, 20, 30, 40, 50, 60, 70):
        cv2.circle(img, (cx, cy), int(r * ppm), (42, 42, 42), 1)
        cv2.putText(img, f"{r}m", (cx + 4, cy - int(r * ppm) + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (90, 90, 90), 1)
    if pts is not None and len(pts):
        u = (cx + pts[:, 1] * ppm).astype(np.int32); v = (cy - pts[:, 0] * ppm).astype(np.int32)
        ok = (u >= 0) & (u < w) & (v >= 0) & (v < h)
        img[v[ok], u[ok]] = _height_colors(pts[ok, 2], pts[ok, 3], sensor_z)
    yaw = math.radians(ego_tf.rotation.yaw); c_, s_ = math.cos(yaw), math.sin(yaw)
    def to_img(p):
        dx, dy = p.x - ego_tf.location.x, p.y - ego_tf.location.y
        fx = dx * c_ + dy * s_; fy = -dx * s_ + dy * c_          # UE: x앞, y오른
        return (int(round(cx + fy * ppm)), int(round(cy - fx * ppm)))
    left, right = poly
    pts2 = [to_img(p) for p in left] + [to_img(p) for p in reversed(right)]
    if len(pts2) >= 3:
        arr = np.array(pts2, np.int32)
        ov = img.copy(); cv2.fillPoly(ov, [arr], col); img = cv2.addWeighted(ov, 0.25, img, 0.75, 0)
        cv2.polylines(img, [arr], True, col, 1)
    cv2.rectangle(img, (cx - 5, cy - 11), (cx + 5, cy + 11), (255, 255, 255), -1)   # 자차
    return img

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2000)
ap.add_argument("--town", default="Town04")
ap.add_argument("--model", default=os.path.expanduser("~/icepredict/models/roadnet_v1/roadnet.onnx"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/logs/carla_demo"))
ap.add_argument("--patch-ahead", type=float, default=90.0, help="패치를 차량 앞 몇 m에 둘지")
ap.add_argument("--patch-len", type=float, default=40.0)
ap.add_argument("--friction", type=float, default=0.02)
ap.add_argument("--target-kph", type=float, default=60.0)
ap.add_argument("--max-steps", type=int, default=1200)
ap.add_argument("--fps", type=int, default=50, help="시뮬 고정 스텝 (1/fps 초)")
ap.add_argument("--warmup", type=float, default=3.0, help="스폰 충격·가속 대기 (감지 비활성)")
ap.add_argument("--min-detect-kph", type=float, default=25.0, help="이 속도 이상일 때만 2차 감지 활성")
ap.add_argument("--disable-primary", action="store_true", help="1차 방어 끔 (2차 시연)")
ap.add_argument("--gt-detect", action="store_true", help="카메라 대신 패치까지 거리로 1차 판정")
ap.add_argument("--detect-range", type=float, default=30.0, help="--gt-detect 시 감지 거리(m)")
ap.add_argument("--night", action="store_true")
ap.add_argument("--no-ice-render", action="store_true", help="카메라 프레임에 얼음 합성 안 함")
ap.add_argument("--ctx-temp", type=float, default=-3.0,
                help="맥락 계층에 넣을 기온(°C). 기본 -3. 영상만으로는 젖은 노면과 얼음을 못 가르므로 "
                     "이 값이 판단을 가른다. +5 로 주면 같은 화면에서도 1차가 침묵해야 맞다")
ap.add_argument("--fixed-weather-ctx", action="store_true",
                help="기상 컨텍스트를 옛 방식대로 고정값(영하 3도·습도 88·강수 0)으로 넘긴다. "
                     "2026-09-21 이전 주행과 비교할 때만 쓴다. 기본은 CARLA 날씨를 실제로 반영하는 것 — "
                     "고정으로 넘기면 폭우 주행에서도 보드가 '비 안 옴'을 받아 "
                     "강수 기반 1차 신뢰 판단이 동작하지 않는다.")
ap.add_argument("--alarm-confirm", type=int, default=8,
                help="1차 경보를 확정하는 데 필요한 연속 프레임 수. 0.1초짜리 깜빡임 오경보를 거른다. "
                     "1 이면 예전 동작(보드 경보를 그대로 씀). 실측 근거는 fusion.ALARM_CONFIRM_FRAMES 주석 참조.")
ap.add_argument("--control-no-ice", action="store_true",
                help="음성 대조군: 빙판을 아예 만들지 않고 같은 경로를 주행한다. "
                     "이때 나오는 1차 경보는 전부 오경보이므로 시뮬 오경보율을 처음으로 잴 수 있다.")
ap.add_argument("--ice-seed", type=int, default=7, help="얼음 외관 파라미터 시드 (프레임마다 고정)")
# 융합을 STM32N6에서 돌린다. 학교망이 유선→무선을 차단하므로 데스크탑이 bind하고
# Pi 브리지(scripts/n6_bridge.py)가 connect한다 — 방향을 뒤집어야 연결이 된다.
ap.add_argument("--fusion", choices=["local", "n6", "n6npu"], default="local",
                help="local=이 프로세스에서 융합, n6=보드에서 융합(추론은 여기), n6npu=보드 NPU가 추론+융합 (ROI 이미지를 보낸다)")
ap.add_argument("--n6-bind", default="tcp://*:5558", help="Pi 브리지가 접속할 주소")
ap.add_argument("--n6-timeout", type=float, default=3.0, help="브리지 응답 대기(초)")
ap.add_argument("--slip-local", action="store_true",
                help="2차 방어(IMU 미끄러짐 감지)를 보드 대신 이 프로세스에서. 기본: --fusion n6/n6npu 면 보드(STM32N6 ThreadX)가 판정")
ap.add_argument("--no-video", action="store_true")
ap.add_argument("--show-patch", action="store_true", help="빙판 구간 표시(BLACK ICE 라벨·테두리·기둥) 켜기. 기본은 끔 — 실제처럼 보여야 한다")
ap.add_argument("--no-overlay", action="store_true", help="(구) 빙판 시각화 오버레이 끄기 — 이제 기본이 끔")
ap.add_argument("--no-bird", action="store_true", help="조감(3인칭) 카메라 끄기")
ap.add_argument("--weather", default="winter",
                help="winter=기존 겨울(흐림·젖음) 설정, 그 외 CARLA 프리셋 이름 (ClearNoon, WetNoon, HardRainNoon, ClearSunset, ClearNight ...)")
ap.add_argument("--tag", default=None, help="출력 파일 이름 태그 (기본: 시나리오에서 자동)")
ap.add_argument("--views", default="split,bev,lidar,lidar_sem",
                help="같은 주행에서 동시에 녹화할 시점: split(좌 1인칭+우 조감), front, bev, lidar(높이색 3D 점군), lidar_sem(클래스색 점군+3D 박스) (콤마 구분)")
ap.add_argument("--no-native-ice", action="store_true",
                help="엔진 내장 빙판 타일(거칠기 0.05 평면) 안 깔기. 기본은 깐다 — 조감/라이다 시점에서도 실제 노면처럼 보이게")
ap.add_argument("--ice-subtle", type=float, default=1.0, help="1인칭 합성 얼음의 반사 강도 배율 (1.0=학습 분포 그대로, 낮추면 더 티 안 남)")
ap.add_argument("--debug-box", action="store_true", help="시뮬 안에 디버그 박스도 그리기")
ap.add_argument("--road-texture", default=None,
                help="실제 노면 사진(RSCD) 폴더. 주면 도로 메시에는 마른 아스팔트 사진 모자이크를, 빙판 타일에는 얼음 사진 모자이크를 런타임 재질로 입힌다 (실험)")
ap.add_argument("--ice-texture-class", default="ice", help="--road-texture 시 빙판 타일에 쓸 RSCD 클래스 접미사 (ice / water-asphalt-smooth / wet-asphalt-smooth)")
ap.add_argument("--export-labels", default=None,
                help="2D 분할 라벨 내보내기 폴더: 매 5프레임마다 1인칭 이미지 + 마스크 PNG(0 배경, 1 도로, 2 차선, 3 빙판 구간, 4 차량)")
ap.add_argument("--tire-friction", type=float, default=1.0,
                help="차량 타이어 마찰 계수. CARLA 기본 3.5는 비현실적(빙판을 벗어나면 0.5초 만에 정지). 마른 아스팔트 ≈ 1.0")
ap.add_argument("--log-dyn", default=None, help="CSV 경로: t, speed, steer_eq, gz, ay, ax, brake 를 매 틱 기록 (감지기 모델 보정용)")
ap.add_argument("--no-secondary", action="store_true",
                help="2차 방어(IMU 미끄러짐 감지) 끔 — '방어 없음' 기준선. 1차도 끄면 아무 개입 없이 빙판에 진입한다 (충돌 센서로 결과 기록)")
ap.add_argument("--traffic", type=int, default=0, help="옆 차로에 스폰할 주변 차량 수 (자율주행, 같은 방향)")
ap.add_argument("--lead-stop", type=float, default=0.0,
                help="빙판 중심에서 이만큼 앞(m)의 자차 차로에 정차 차량(비상등)을 둔다. 0=없음. 2차 방어의 회피 판단을 보여주는 장애물")
a = ap.parse_args()
TIRE_MU = a.tire_friction
try: sys.stdout.reconfigure(line_buffering=True)   # 크래시/kill 시에도 로그가 남게
except Exception: pass
a.no_overlay = not a.show_patch          # 표시는 명시적으로 켤 때만

out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
dt = 1.0 / a.fps
client = carla.Client(a.host, a.port); client.set_timeout(60.0)
world = client.get_world()
if a.town not in world.get_map().name:
    print(f"[sim] loading {a.town} ..."); world = client.load_world(a.town)
cmap = world.get_map()

# ---- 동기 모드 + 겨울 날씨 -------------------------------------------------
orig = world.get_settings()
st = world.get_settings(); st.synchronous_mode = True; st.fixed_delta_seconds = dt
world.apply_settings(st)
tm = client.get_trafficmanager(8000); tm.set_synchronous_mode(True)

if a.weather == "winter":
    w = world.get_weather()
    w.cloudiness, w.precipitation, w.precipitation_deposits, w.wetness = 70.0, 0.0, 30.0, 40.0
    w.sun_altitude_angle = -15.0 if a.night else 25.0
elif a.weather == "Snow":
    # CARLA에 눈 프리셋이 없다: 흐리고 뿌옇고 낮은 해 + 마른 노면(강수 입자는 비처럼 보여 끈다). 쌓인 눈·내리는 눈은 아래서 만든다.
    w = carla.WeatherParameters(cloudiness=95.0, precipitation=0.0, precipitation_deposits=0.0, wetness=8.0,
                                wind_intensity=35.0, sun_azimuth_angle=60.0, sun_altitude_angle=18.0,
                                fog_density=14.0, fog_distance=35.0, fog_falloff=0.6, scattering_intensity=1.0)
else:
    w = getattr(carla.WeatherParameters, a.weather)
world.set_weather(w)
snow = (a.weather == "Snow")
snow_field = SnowField(camcfg.WIDTH, camcfg.HEIGHT) if snow else None
snow_field_b = SnowField(camcfg.WIDTH, camcfg.HEIGHT, n=300, seed=9) if snow else None
world_textured = False        # 엔진 재질을 바꿨으면 끝날 때 reload_world 로 되돌린다
if a.export_labels:
    os.makedirs(a.export_labels, exist_ok=True)
night = bool(w.sun_altitude_angle < 0)   # 얼음 합성 파라미터·기상 컨텍스트가 이 값을 쓴다
print(f"[sim] weather={a.weather} sun={w.sun_altitude_angle:.0f} cloud={w.cloudiness:.0f} rain={w.precipitation:.0f} "
      f"deposits={w.precipitation_deposits:.0f} wet={w.wetness:.0f} night={night}")
views = set(v.strip() for v in a.views.split(",") if v.strip())

actors = []
autopiloted = []          # Traffic Manager에 등록된 차량 — 정리 시 먼저 해제해야 한다
cleaned = False
def cleanup():
    global cleaned
    if cleaned: return
    dbg = os.environ.get("ICE_CLEANUP_DEBUG")
    # 자율주행 등록을 먼저 해제한다. 등록된 채로 차량을 파괴하면 종료 시 Traffic Manager 소멸자가 파괴된 액터를
    # 건드려 C++ 예외(terminate)로 죽는다 — 경고 없이 끝난 주행(시간 초과 등)에서만 나타나던 크래시의 원인.
    for v in list(globals().get("autopiloted", [])):
        try: v.set_autopilot(False, 8000)
        except Exception: pass
    for x in actors:                      # 콜백을 먼저 끊는다 (destroyed actor 크래시 예방)
        try:
            if dbg: print(f"[cleanup] stop {x.type_id} {x.id}", flush=True)
            if hasattr(x, "is_listening") and x.is_listening: x.stop()
        except Exception as e:
            if dbg: print(f"[cleanup] stop 실패 {e}", flush=True)
    for x in actors:
        try:
            if dbg: print(f"[cleanup] destroy {x.type_id} {x.id}", flush=True)
            x.destroy()
        except Exception as e:
            if dbg: print(f"[cleanup] destroy 실패 {e}", flush=True)
    if dbg: print("[cleanup] settings 복원", flush=True)
    try:
        world.apply_settings(orig); tm.set_synchronous_mode(False)
    except Exception:
        pass
    cleaned = True

try:
    # ---- 차량 스폰 (긴 직선 구간 선택) ----------------------------------
    bl = world.get_blueprint_library()
    vbp = bl.filter("vehicle.tesla.model3")[0]
    spawns = cmap.get_spawn_points()
    ego = None
    for sp in spawns:
        wp = cmap.get_waypoint(sp.location, project_to_road=True)
        # 앞으로 patch_ahead+60m 직진 가능한 지점 고르기
        cur, ok, moved = wp, True, 0.0
        while moved < a.patch_ahead + 60:
            nxt = cur.next(5.0)
            if not nxt: ok = False; break
            cur = nxt[0]; moved += 5.0
        if not ok: continue
        ego = world.try_spawn_actor(vbp, sp)
        if ego: break
    if ego is None: raise RuntimeError("차량 스폰 실패")
    actors.append(ego); world.tick()
    # 미끄러짐 감지기(파이썬 참조·보드 C 코어)는 steer(-1~1)를 최대 35°로 조향각에 환산한다. CARLA 차량의 실제 최대 조향각은
    # 다르므로(테슬라 70°) 그 비율로 steer 를 환산해 넘긴다. 안 맞추면 급조향 때 기대 yaw rate 가 절반이 되어
    # 마른 노면에서도 '미끄러짐'으로 오판한다 (주변 차량 끼어들기 때 실제로 오탐이 났다).
    try:
        _ms = float(ego.get_physics_control().wheels[0].max_steer_angle)
    except Exception:
        _ms = 35.0
    steer_gain = _ms / 35.0
    print(f"[sim] 차량 최대 조향각 {_ms:.0f}° → 감지기 조향 환산 배율 {steer_gain:.2f}")
    def set_tire_friction(v, mu):
        try:
            pc = v.get_physics_control()
            for wh in pc.wheels: wh.tire_friction = float(mu)
            v.apply_physics_control(pc); return True
        except Exception: return False
    if a.control_no_ice:
        # 패치 객체는 거리 계산·로그에 쓰이므로 남기되, 물리도 외관도 정상 노면과 같게 만든다.
        a.friction = a.tire_friction if a.tire_friction > 0 else 1.0
        a.no_native_ice = True
        a.no_ice_render = True
        print(f"[대조군] 빙판 없음 — 마찰 {a.friction:.2f}, 합성·타일 끔. "
              f"여기서 나오는 1차 경보는 전부 오경보다.", flush=True)
    if a.tire_friction > 0 and set_tire_friction(ego, a.tire_friction):
        print(f"[sim] 타이어 마찰 {a.tire_friction:.2f} (CARLA 기본 3.5 대신 — 빙판 밖 제동 거리가 현실적이 된다)")

    # ---- 블랙아이스 패치 -------------------------------------------------
    wp_ice = waypoint_ahead(cmap, ego, a.patch_ahead)
    patch = spawn_ice_patch(world, wp_ice, length_m=a.patch_len, width_m=7.0, friction=a.friction)
    actors.append(patch.actor)
    traffic_vehicles, lead_vehicle = [], None
    if a.traffic > 0 or a.lead_stop > 0:
        traffic_vehicles, lead_vehicle = spawn_traffic(world, bl, cmap, tm, ego, a.traffic, wp_ice, a.lead_stop,
                                                       np.random.default_rng(a.ice_seed + 100))
        actors.extend(traffic_vehicles)
        if lead_vehicle is not None: actors.append(lead_vehicle); traffic_vehicles = traffic_vehicles + [lead_vehicle]
        print(f"[traffic] 주변 차량 {len(traffic_vehicles) - (1 if lead_vehicle else 0)}대"
              + (f", 정차 차량 빙판 중심 +{a.lead_stop:.0f}m" if lead_vehicle is not None else ""))
    print(f"[sim] ice patch at {wp_ice.transform.location} friction={a.friction} len={a.patch_len}m")
    if lead_vehicle is not None:
        # 스폰 직후의 get_location() 은 동기 모드에서 아직 갱신되지 않아 (0,0,0) 을 준다.
        # 그 값으로 거리를 재면 원점까지의 428 m 가 나오고, 원점 근처 도로(47/-1)로 스냅된다.
        # 이전 세션이 "정차 차량이 428 m 밖에 놓였다"고 본 것은 이 착시였다. 틱 뒤에 다시 잰다.
        for _ in range(2):
            world.tick()
        _lw = cmap.get_waypoint(lead_vehicle.get_location())
        _d = wp_ice.transform.location.distance(lead_vehicle.get_location())
        _ok = (_lw.road_id == wp_ice.road_id and _lw.lane_id == wp_ice.lane_id)
        print(f"[traffic] (틱 후 확인) 정차 차량 {_lw.road_id}/{_lw.lane_id}, 빙판에서 {_d:.0f} m, "
              f"차로일치 {'예' if _ok else '아니오'}", flush=True)
    poly = patch_road_polygon(cmap, patch)
    if not a.no_native_ice:
        try:
            tiles, tile_names = spawn_native_ice(world, bl, wp_ice, patch)
            actors.extend(tiles)
            print(f"[ice] 엔진 내장 빙판 타일 {len(tiles)}개 스폰, 재질 적용 {len(tile_names)}개 (거칠기 0.05)")
        except Exception as e:
            print(f"[ice] 엔진 내장 빙판 실패 (합성만 사용): {e}")

    # ---- 센서 -----------------------------------------------------------
    cbp = camcfg.apply_camera_bp(bl.find("sensor.camera.rgb")); cbp.set_attribute("sensor_tick", "0.0")
    cam = world.spawn_actor(cbp, camcfg.camera_transform(carla), attach_to=ego)
    actors.append(cam)
    # 조감(3인칭) 카메라: 영상 오른쪽 절반. 보여주기용이며 모델 입력과 무관하다.
    # 차 뒤 10m·높이 30m·아래로 55°: 아래쪽에 차, 위쪽으로 약 90m 앞 도로까지 보인다.
    bird, Kb = None, None
    if not a.no_bird:
        bbp = bl.find("sensor.camera.rgb")
        bbp.set_attribute("image_size_x", str(camcfg.WIDTH)); bbp.set_attribute("image_size_y", str(camcfg.HEIGHT))
        bbp.set_attribute("fov", "90"); bbp.set_attribute("sensor_tick", "0.0")
        bird = world.spawn_actor(bbp, carla.Transform(carla.Location(x=-10.0, z=30.0), carla.Rotation(pitch=-55.0)),
                                 attach_to=ego)
        actors.append(bird)
        Kb = camera_intrinsics(camcfg.WIDTH, camcfg.HEIGHT, 90.0)
        sem_b = None
        if snow:
            sb2 = bl.find("sensor.camera.semantic_segmentation")
            sb2.set_attribute("image_size_x", str(camcfg.WIDTH)); sb2.set_attribute("image_size_y", str(camcfg.HEIGHT))
            sb2.set_attribute("fov", "90"); sb2.set_attribute("sensor_tick", "0.0")
            sem_b = world.spawn_actor(sb2, carla.Transform(carla.Location(x=-10.0, z=30.0), carla.Rotation(pitch=-55.0)), attach_to=ego)
            actors.append(sem_b)
    # 얼음 합성에는 도로/하늘 마스크가 필요하다 → 수집기와 같은 설정의 시맨틱 카메라
    sem = None
    if not a.no_ice_render or a.export_labels:      # 라벨 내보내기도 시맨틱 카메라가 필요하다
        sbp = camcfg.apply_camera_bp(bl.find("sensor.camera.semantic_segmentation"), exposure=None)
        sbp.set_attribute("sensor_tick", "0.0")
        sem = world.spawn_actor(sbp, camcfg.camera_transform(carla), attach_to=ego)
        actors.append(sem)
    slidar, slidars = None, deque(maxlen=2)
    if "lidar_sem" in views:
        sbp2 = bl.find("sensor.lidar.ray_cast_semantic")
        for k_, v_ in dict(channels="64", range="90", points_per_second="2400000", rotation_frequency=str(a.fps),
                           upper_fov="10", lower_fov="-26", sensor_tick="0.0").items():
            sbp2.set_attribute(k_, v_)
        slidar = world.spawn_actor(sbp2, carla.Transform(carla.Location(x=0.0, z=2.4)), attach_to=ego); actors.append(slidar)
        slidar.listen(lambda d: slidars.append(np.frombuffer(d.raw_data, LIDAR_DT).copy()))
    lidar, lidars = None, deque(maxlen=2)
    if "lidar" in views:
        lbp = bl.find("sensor.lidar.ray_cast")
        for k_, v_ in dict(channels="64", range="90", points_per_second="2400000", rotation_frequency=str(a.fps),
                           upper_fov="10", lower_fov="-26", sensor_tick="0.0").items():
            lbp.set_attribute(k_, v_)
        lidar = world.spawn_actor(lbp, carla.Transform(carla.Location(x=0.0, z=2.4)), attach_to=ego); actors.append(lidar)
        lidar.listen(lambda d: lidars.append(np.frombuffer(d.raw_data, np.float32).reshape(-1, 4).copy()))
    ibp = bl.find("sensor.other.imu"); ibp.set_attribute("sensor_tick", "0.0")
    imu = world.spawn_actor(ibp, carla.Transform(), attach_to=ego); actors.append(imu)
    collisions = deque(maxlen=4)
    colsen = world.spawn_actor(bl.find("sensor.other.collision"), carla.Transform(), attach_to=ego); actors.append(colsen)
    colsen.listen(lambda e: collisions.append(e))
    if a.road_texture:
        try:
            nr, nt = apply_real_road_textures(world, a.road_texture, a.ice_texture_class,
                                              tile_names if 'tile_names' in dir() else [], np.random.default_rng(a.ice_seed + 7))
            print(f"[tex] 실제 노면 사진 텍스처: 도로 객체 {nr}개(마른 아스팔트), 빙판 타일 {nt}개({a.ice_texture_class}) — 끝나면 reload_world 로 복원")
            world_textured = True
        except Exception as e:
            print(f"[tex] 실제 노면 텍스처 실패: {e}")
    if snow:
        print("[snow] 쌓인 눈은 시맨틱 마스크로 카메라 영상에 덮는다 (Town04 지형 재질은 런타임 텍스처 API가 먹히지 않았다)")

    K = camera_intrinsics(camcfg.WIDTH, camcfg.HEIGHT, camcfg.FOV)
    frames, sems, imus, birds, sems_b = deque(maxlen=2), deque(maxlen=2), deque(maxlen=8), deque(maxlen=2), deque(maxlen=2)
    cam.listen(lambda im: frames.append(im))
    if bird is not None:
        bird.listen(lambda im: birds.append(im))
        if snow and sem_b is not None:
            sem_b.listen(lambda im: sems_b.append(im))
    if sem is not None:
        sem.listen(lambda im: sems.append(im))
    imu.listen(lambda m: imus.append(m))

    # 얼음 외관은 주행 내내 같아야 한다 (프레임마다 재추첨하면 화면이 깜빡이고,
    # 모델 입력도 프레임마다 달라져 위험도 추정이 흔들린다)
    ice_rng = np.random.default_rng(a.ice_seed)
    ice_params = random_ice_params(ice_rng, night=night)
    ice_params["specular"] = float(ice_params["specular"] * a.ice_subtle)
    if not a.no_ice_render:
        print("[ice] 합성 파라미터 " + " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                                              for k, v in ice_params.items()))

    # ---- 1차/2차 판정기 --------------------------------------------------
    det = None if (a.disable_primary or a.gt_detect or a.fusion == "n6npu") else RoadNetDetector(a.model)
    # n6npu: 모델 입력 형식 (eq3 int8 네트워크, atonn이 입력 양자화를 망 밖으로 뺌). scripts/n6_frame_client.py와 동일.
    NPU_IN_SCALE, NPU_IN_ZP = 0.018658448, -14
    NPU_MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1); NPU_STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
    def npu_quantize_roi(bgr_frame):
        roi = camcfg.crop_roi(bgr_frame)
        im = cv2.resize(roi, (224, 224), interpolation=cv2.INTER_AREA)
        x = (im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - NPU_MEAN) / NPU_STD
        return np.clip(np.round(x / NPU_IN_SCALE) + NPU_IN_ZP, -128, 127).astype(np.int8).tobytes()
    if not a.fixed_weather_ctx:
        # CARLA 날씨를 그대로 관측값으로 옮긴다. precipitation 은 0~100 스케일이라 mm/h 로 환산한다
        # (100 = 폭우 ≈ 20 mm/h 로 잡았다). 노면 젖음(precipitation_deposits)도 강수 흔적으로 더한다.
        _w = world.get_weather()
        _precip = _w.precipitation / 100.0 * 20.0 + _w.precipitation_deposits / 100.0 * 2.0
        _hour = 23 if _w.sun_altitude_angle < 0 else (5 if _w.sun_altitude_angle < 15 else 13)
        _obs = WeatherObs(temp_c=a.ctx_temp, humidity=min(99.0, 88.0 + _w.precipitation / 10.0),
                          temp_trend_c_per_h=(-1.0 if a.ctx_temp <= 2.0 else 0.0),
                          precip_mm=round(_precip, 2))
        ctx = build_context(_obs, LocationCtx(feature="bridge", hour=_hour))
        print(f"[ctx] 실제 날씨 반영: 기온 {a.ctx_temp:+.1f}도, 강수 {_precip:.1f} mm/h, "
              f"태양고도 {_w.sun_altitude_angle:.0f}도 → 시각 {_hour}시", flush=True)
    else:
        ctx = build_context(WeatherObs(temp_c=a.ctx_temp, humidity=88.0,
                                       temp_trend_c_per_h=(-1.0 if a.ctx_temp <= 2.0 else 0.0)),
                            LocationCtx(feature="bridge", hour=5 if not night else 23))
    fuser = RiskFuser(ctx.weights, threshold=ctx.threshold)
    # 기상이 1차 방어를 못 믿을 조건이면 스스로 끄고 2차에 맡긴다.
    # 근거: 빙판이 **없는** 대조군에서도 폭우 주행의 최대 위험도가 0.963 이라 어떤 문턱으로도
    # 못 막는다 (`정리/07`). 못 막을 경보를 내는 것보다 "지금은 못 본다"고 말하는 편이 낫다.
    primary_distrusted = not getattr(ctx, "primary_trustworthy", True)
    if primary_distrusted and not a.disable_primary:
        a.disable_primary = True
        print(f"[ctx] 1차 방어 신뢰 불가 → 끄고 2차에 맡긴다: "
              f"{getattr(ctx, 'distrust_reason', '')}", flush=True)

    # 기온이 얼음을 허락하지 않으면 얼음 경보 자체를 내지 않는다. 위와 의미가 다르다 —
    # 저쪽은 "카메라를 못 믿겠다", 이쪽은 "얼음이 있을 수 없다"이다.
    # 근거: 빙판 없는 젖은 노면 대조군에서 1차가 노면 확률 0.87 로 얼음이라 단언했다.
    # 확인 10프레임을 통과했고 문턱을 올려도 못 막는다. 영상만으로는 젖음과 얼음이
    # 갈리지 않는다는 뜻이고, 그러면 갈라 줄 쪽은 맥락뿐이다.
    # 2차 방어는 끄지 않는다 — 따뜻해도 젖은 노면은 미끄럽다.
    ice_impossible = not getattr(ctx, "ice_possible", True)
    if ice_impossible and not a.disable_primary:
        a.disable_primary = True
        print(f"[ctx] 얼음 불가능 조건 → 1차 얼음 경보 끔 (2차는 그대로): "
              f"{getattr(ctx, 'no_ice_reason', '')}", flush=True)

    # ---- 융합을 보드로 보낼 경우: Pi 브리지 연결 대기 ----
    n6 = None
    n6_stats = {"board": 0, "fallback": 0, "board_ns_sum": 0}
    if a.fusion in ("n6", "n6npu"):
        import zmq
        zctx = zmq.Context()
        n6 = zctx.socket(zmq.REQ)
        n6.setsockopt(zmq.RCVTIMEO, int(a.n6_timeout * 1000))
        n6.setsockopt(zmq.SNDTIMEO, int(a.n6_timeout * 1000))
        n6.setsockopt(zmq.REQ_RELAXED, 1)      # 타임아웃 후에도 소켓을 재사용할 수 있게
        n6.setsockopt(zmq.REQ_CORRELATE, 1)
        n6.bind(a.n6_bind)
        print(f"[n6] {a.n6_bind} 에서 Pi 브리지 접속 대기 — Pi에서 scripts/n6_bridge.py 실행", flush=True)
        while True:                             # 브리지가 붙을 때까지 기다린다
            try:
                n6.send_json({"kind": "ping"}); r = n6.recv_json()
                print(f"[n6] 브리지 연결됨, 보드 {r.get('board')}", flush=True); break
            except zmq.Again:
                print("[n6] 대기 중...", flush=True)
        # 기상 컨텍스트로 보드 임계값·가중치를 설정
        n6.send_json({"kind": "ctx", "threshold": ctx.threshold, "alpha": ctx.weights.alpha,
                      "beta": ctx.weights.beta, "gamma": ctx.weights.gamma,
                      "primary_trustworthy": bool(getattr(ctx, "primary_trustworthy", True))})
        n6.recv_json()
        print(f"[n6] ctx 전송: threshold={ctx.threshold} weights=({ctx.weights.alpha},"
              f"{ctx.weights.beta},{ctx.weights.gamma})", flush=True)
    slip = SlipDetector(rate_hz=a.fps, confirm_samples=3, min_speed_mps=a.min_detect_kph / 3.6)
    # 판정 규칙 A/B — 같은 궤적에서 두 규칙이 각각 언제 확정하는지 동시에 잰다.
    # 보드 펌웨어는 재기록에 물리 접근(BOOT1 스위치 + SWD)이 필요해 원격으로 못 바꾼다.
    # 그래서 호스트에서 두 참조 구현을 나란히 돌려 **같은 주행선 위에서** 비교한다.
    # slip      : 현재 규칙 (기본 타원). 보드가 판정하면 폴백용으로만 쓰인다.
    # slip_box  : 예전 직사각형 규칙. 보드에 실제로 올라가 있는 것과 같다.
    slip_box = SlipDetector(rate_hz=a.fps, confirm_samples=3,
                            min_speed_mps=a.min_detect_kph / 3.6, ellipse_rule=False)
    t_rule = {"ellipse": None, "box": None}     # 각 규칙의 첫 확정 시각
    slip_lat: list[tuple] = []                  # 보드 2차 응답 표본별 (t, µs)
    # 2차 방어 판정 주체: 보드(기본) / 로컬. 보드가 IMU 패킷에 답하지 않으면(구 펌웨어) 3회 실패 후 로컬로 내려가고 보고한다.
    slip_board = (n6 is not None) and not a.slip_local
    slip_stats = {"board": 0, "fallback": 0, "ns_sum": 0, "ns_max": 0, "fail_streak": 0}
    if slip_board:
        try:
            n6.send_json({"kind": "imu_reset", "dt": dt, "min_speed": a.min_detect_kph / 3.6}); r_ = n6.recv_json()
            if not r_.get("ok"):
                print(f"[n6] 보드 slip 경로 없음 ({r_.get('error')}) → 2차 방어 로컬", flush=True); slip_board = False
            else:
                print("[n6] 2차 방어(IMU 미끄러짐 감지) 판정 주체: STM32N6 (ThreadX)", flush=True)
        except zmq.Again:
            print("[n6] imu_reset 무응답 → 2차 방어 로컬", flush=True); slip_board = False
    print(f"[ctx] prior={ctx.risk_prior} threshold={ctx.threshold} weights=({ctx.weights.alpha},{ctx.weights.beta},{ctx.weights.gamma})"
          f" 1차신뢰={getattr(ctx, 'primary_trustworthy', True)}")

    for _ in range(int(0.5 * a.fps)):      # 스폰 낙하 충격 소산
        world.tick()
    ego.set_autopilot(True, 8000); autopiloted.append(ego)
    tm.ignore_lights_percentage(ego, 100); tm.auto_lane_change(ego, False)
    tm.vehicle_percentage_speed_difference(ego, (30.0 - a.target_kph) / 30.0 * 100.0)

    tag = "gt" if a.gt_detect else ("secondary_only" if a.disable_primary else "primary")
    if not a.no_ice_render and not a.gt_detect:
        tag += "_icerender"
    if a.fusion == "n6":
        tag += "_n6"
    if a.fusion == "n6npu":
        tag += "_n6npu"
    if a.tag: tag = a.tag
    vws = {}
    seg_logged = False
    events, rows = [], []
    alarm_hits = 0            # 1차 경보가 연속으로 문턱을 넘은 프레임 수 (확정 계층)
    state = "DRIVE"           # DRIVE → WARN → BRAKE → STOPPED / SLIP → EMERG
    t_sim = 0.0; t_warn = None; t_slip = None; entered = False; warn_info = None; sec_info = None; sec_base = None
    local_emerg = LocalEmergency(); emerg_mode = None; lane_departed = False; spun = False
    control_lost = False; t_lost = None
    dyn_log = open(a.log_dyn, "w") if a.log_dyn else None
    if dyn_log: dyn_log.write("t,speed,steer_eq,gz,ay,ax,brake,inside\n")
    lane_err = lat_off = 0.0; gap_f = gap_l = gap_r = 999.0; board_ctrl = None; slip_lane_id = None; perception_degraded = False
    for step in range(a.max_steps):
        if a.debug_box:
            world.debug.draw_box(carla.BoundingBox(patch.location + carla.Location(z=0.1),
                                 carla.Vector3D(patch.extent[0], patch.extent[1], 0.05)),
                                 patch.transform.rotation, 0.08, carla.Color(120, 220, 255), dt * 1.5)
        world.tick(); t_sim += dt
        v = ego.get_velocity(); spd = math.sqrt(v.x**2 + v.y**2 + v.z**2)
        loc = ego.get_location()
        dist = loc.distance(patch.location)
        inside = patch.contains(loc, margin=1.0)
        if inside and not entered:
            entered = True; events.append({"t": round(t_sim,2), "event": "patch_enter", "speed_kph": round(spd*3.6,1)})
            print(f"[{t_sim:6.2f}s] 빙판 진입  속도 {spd*3.6:.1f} km/h")

        # ---------- 카메라 프레임 (추론과 영상이 같은 프레임을 본다) ----------
        # rng을 매 프레임 같은 시드로 새로 만든다. ice_rng을 그대로 넘기면 경계 노이즈와
        # 두께 얼룩이 프레임마다 재추첨되어 화면이 깜빡이고 모델 입력도 흔들린다.
        view = None
        if frames:
            im = frames[-1]
            view = np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, :3].copy()
            if not a.no_ice_render and sems:
                sm = sems[-1]
                seg = np.frombuffer(sm.raw_data, np.uint8).reshape(sm.height, sm.width, 4)[:, :, 2].copy()
                mask = patch_image_mask(poly, cam, K, view.shape)
                if snow_field is not None:
                    view = snow_cover(view, seg, np.random.default_rng(step))   # 쌓인 눈 (지형·인도·초목)
                    view = snow_field.step(view, t_sim)      # 내리는 눈 — 카메라가 실제로 보는 것, 모델 입력에도 들어간다
                if mask.any():
                    if not a.no_native_ice:
                        # 내장 타일은 시맨틱 태그가 도로가 아니라(Static/Other/Unlabeled) 합성 알파가 0이 된다 → 도로로 재라벨
                        if not seg_logged:
                            u_, c_ = np.unique(seg[mask], return_counts=True)
                            print(f"[ice] 패치 영역 시맨틱 태그 분포: {dict(zip(u_.tolist(), c_.tolist()))}"); seg_logged = True
                        seg[mask & np.isin(seg, (0, 22, 23, 24))] = 1
                    view = composite_ice(view, seg, mask, rng=np.random.default_rng(a.ice_seed), **ice_params)

        if a.export_labels and view is not None and sems and step % 5 == 0:
            # 2D 분할 라벨: CARLA 시맨틱(도로 1, 차선 24, 차량 14) + 빙판 구간 폴리곤(3)
            seg_l = np.frombuffer(sems[-1].raw_data, np.uint8).reshape(sems[-1].height, sems[-1].width, 4)[:, :, 2]
            lab = np.zeros(seg_l.shape, np.uint8)
            lab[seg_l == 1] = 1; lab[seg_l == 24] = 2; lab[np.isin(seg_l, (14, 15, 16))] = 4
            pm = patch_image_mask(poly, cam, K, view.shape)
            lab[pm & (lab != 4)] = 3
            cv2.imwrite(os.path.join(a.export_labels, f"{tag}_{step:05d}.jpg"), view, [cv2.IMWRITE_JPEG_QUALITY, 92])
            cv2.imwrite(os.path.join(a.export_labels, f"{tag}_{step:05d}_label.png"), lab)
            vis = view.copy(); pal = {1: (90, 90, 90), 2: (255, 255, 255), 3: (255, 160, 40), 4: (40, 40, 220)}
            for kk, col_ in pal.items(): vis[lab == kk] = (0.55 * vis[lab == kk] + 0.45 * np.array(col_)).astype(np.uint8)
            cv2.imwrite(os.path.join(a.export_labels, f"{tag}_{step:05d}_vis.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # ---------- 1차 방어 ----------
        risk = 0.0; p = np.zeros(4); fired = False
        # 경고 이후에도 추론을 계속한다. 상태로 막으면 제동 중 프레임의 HUD가 확률 0.00,
        # risk 0.00으로 굳어 "무엇을 보고 멈췄는지"가 영상에 남지 않는다.
        if not a.disable_primary:
            if a.gt_detect:
                # 정답 기반: 패치 앞 detect_range 안에 들어오면 감지 (도메인갭 우회 데모)
                ahead = dist - patch.extent[0]
                risk = 1.0 if 0 < ahead <= a.detect_range else 0.0
                fired = risk > 0
            elif view is not None and a.fusion == "n6npu":
                vd = None
                try:
                    n6.send_multipart([json.dumps({"kind": "frame"}).encode(), npu_quantize_roi(view)])
                    vd = n6.recv_json()
                except zmq.Again:
                    vd = None
                if vd and vd.get("ok"):
                    lg = np.array(vd["logits"], np.float32); e = np.exp(lg - lg.max()); p = e / e.sum()
                    risk = float(vd["risk"]); fired = bool(vd["alarm"])
                    # 확정 계층: 보드가 낸 경보를 연속 N 프레임 요구로 한 번 더 거른다.
                    # 왜: 빙판이 **없는** 대조군에서 위험도가 특정 지점에서만 0.1 초쯤 튄다
                    # (WetNoon 489프레임 중 18, 최장 연속 6프레임 / ClearNight 209 중 5, 최장 3).
                    # 진짜 빙판은 한 번 오르면 정지까지 유지된다. 실측으로 K=8 이면 두 오경보가
                    # 모두 사라지고 진짜 빙판 9주행은 하나도 안 놓친다 (지연 +0.14~0.66 s).
                    # 원래 있어야 할 곳은 보드 융합 C 코드다. 펌웨어를 다시 구우려면 ST-LINK 와
                    # 물리 스위치가 필요해 지금은 호스트에 둔다 — 반영되면 이 블록을 지운다.
                    if fired:
                        alarm_hits += 1
                        if alarm_hits < a.alarm_confirm:
                            fired = False
                    else:
                        alarm_hits = 0
                    n6_stats["board"] += 1; n6_stats["board_ns_sum"] += int(vd["infer_us"]) * 1000
                else:
                    n6_stats["fallback"] += 1; fired = False   # 보드 무응답: 이 프레임은 판정 없음 (로컬 모델이 없다)
                    n6_stats["fail_streak"] = n6_stats.get("fail_streak", 0) + 1
                    if n6_stats["fail_streak"] == 10 and not perception_degraded:
                        perception_degraded = True
                        events.append({"t": round(t_sim,2), "event": "perception_degraded", "reason": "npu verdict timeout x10"})
                        print(f"[{t_sim:6.2f}s] !! 인지 경로 저하 — 보드 NPU 응답 10프레임 연속 없음. 2차 방어만으로 운행")
                if vd and vd.get("ok"): n6_stats["fail_streak"] = 0
            elif view is not None and det is not None:
                p = det.infer(np.ascontiguousarray(view[:, :, ::-1]))
                # 반사도/차선 헤드는 미학습 → None으로 제외 (0/1을 넣으면 가중 합에서
                # 최솟값이 되어 위험도 상한이 0.35로 잘리고 임계값 0.441에 닿지 못한다)
                if n6 is not None:
                    vd = None
                    try:
                        n6.send_json({"kind": "infer", "p": [float(z) for z in p],
                                      "spec": None, "lane": None})
                        vd = n6.recv_json()
                    except zmq.Again:
                        vd = None
                    if vd and vd.get("ok"):
                        risk = float(vd["risk"]); fired = bool(vd["alarm"])
                        n6_stats["board"] += 1; n6_stats["board_ns_sum"] += vd["board_ns"]
                    else:
                        # 조용히 로컬로 되돌아가면 "보드가 판정했다"는 주장이 거짓이 된다.
                        # 세어두고 종료 시 반드시 보고한다.
                        r = fuser.fuse(p.tolist(), spec=None, lane=None)
                        risk = r.risk; fired = r.alarm
                        n6_stats["fallback"] += 1
                else:
                    r = fuser.fuse(p.tolist(), spec=None, lane=None)
                    risk = r.risk; fired = r.alarm
            # 워밍업(스폰 충격·가속) 동안은 1차도 발화하지 않는다. 정지 상태에서 0.02초에 경고가 난 적이 있다
            # (폭우 노면을 얼음으로 본 오탐) — 그 경우도 주행 중 판단으로 기록돼야 원인이 보인다.
            if fired and state == "DRIVE" and t_sim >= a.warmup:
                state = "WARN"; t_warn = t_sim
                edge = dist - patch.extent[0]
                # HUD는 ASCII만 쓴다. cv2의 Hershey 폰트는 한글 글리프가 없어 '???'로 찍힌다.
                warn_info = (f"PRIMARY WARNING - BLACK ICE  risk {risk:.2f}  "
                             f"edge {edge:.1f}m  {spd*3.6:.0f}km/h")
                events.append({"t": round(t_sim,2), "event": "primary_warning", "risk": round(float(risk),3),
                               "dist_to_patch_m": round(dist,1), "dist_to_edge_m": round(edge,1),
                               "speed_kph": round(spd*3.6,1)})
                print(f"[{t_sim:6.2f}s] 1차 경고  위험도 {risk:.2f}  패치까지 {dist:.1f}m "
                      f"(가장자리 {edge:.1f}m)  속도 {spd*3.6:.1f} km/h")

        # ---------- 2차 방어 ----------
        if step % 5 == 0 and t_sim >= a.warmup:
            try:
                le_, lo_, _wp = lane_state(cmap, ego)
                if not lane_departed and (abs(lo_) > 2.5 or abs(le_) > 0.6):
                    lane_departed = True
                    events.append({"t": round(t_sim,2), "event": "lane_departure", "lat_off_m": round(lo_,2), "heading_err_deg": round(math.degrees(le_),1),
                                   "speed_kph": round(spd*3.6,1), "inside_ice": bool(inside)})
                    print(f"[{t_sim:6.2f}s] 차선 이탈  횡 {lo_:+.1f}m  방향 오차 {math.degrees(le_):+.0f}°  속도 {spd*3.6:.0f} km/h")
                if not spun and abs(le_) > 1.5:
                    spun = True
                    events.append({"t": round(t_sim,2), "event": "spin", "heading_err_deg": round(math.degrees(le_),1), "speed_kph": round(spd*3.6,1)})
                    print(f"[{t_sim:6.2f}s] !! 스핀 (차선 대비 {math.degrees(le_):+.0f}°)")
                # 기준선에서 제어를 잃으면 자율주행을 뗀다.
                # 그대로 두면 트래픽 매니저가 "경로로 복귀"를 계속 시도해, 스핀으로 역방향을 본 차가
                # 다시 가속해 빙판으로 유턴해 들어갔다(실측: 14 s 에 빠져나갔다가 17 s 에 되돌아감).
                # 그 뒤의 충돌은 빙판이 아니라 역주행 탓이라 기준선 통계를 오염시킨다.
                # 제어를 잃은 차에 운전자 입력을 주지 않는 쪽이 정직하다 — 관성과 마찰만 남긴다.
                if a.no_secondary and (spun or lane_departed) and not control_lost:
                    control_lost = True; t_lost = t_sim
                    events.append({"t": round(t_sim,2), "event": "control_lost",
                                   "reason": "spin" if spun else "lane_departure", "speed_kph": round(spd*3.6,1)})
                    print(f"[{t_sim:6.2f}s] 제어 상실 — 자율주행 해제, 관성 주행으로 둔다")
                    try: ego.set_autopilot(False, 8000)
                    except Exception: pass
            except Exception:
                pass
        if collisions and state != "CRASH":
            ce = collisions[-1]; other = getattr(ce.other_actor, "type_id", "?"); imp = ce.normal_impulse
            state = "CRASH"; t_crash = t_sim
            events.append({"t": round(t_sim,2), "event": "collision", "with": other.split(".")[-1], "speed_kph": round(spd*3.6,1),
                           "impulse": round(math.sqrt(imp.x**2 + imp.y**2 + imp.z**2), 1), "dist_to_patch_m": round(dist,1)})
            print(f"[{t_sim:6.2f}s] !! 충돌: {other}  속도 {spd*3.6:.1f} km/h")
            try: ego.set_autopilot(False, 8000)
            except Exception: pass
            sec_info = (f"NO DEFENSE (baseline)  ->  COLLISION with {other.split('.')[-1]} at {spd*3.6:.0f} km/h" if a.no_secondary
                        else f"COLLISION with {other.split('.')[-1]} at {spd*3.6:.0f} km/h")
        if dyn_log is not None and imus and t_sim >= a.warmup:          # 감지기 모델 보정용 로그 — 2차 방어 켜짐/꺼짐과 무관하게 기록
            m_ = imus[-1]; tr_ = ego.get_transform(); c_ = ego.get_control()
            dyn_log.write(f"{t_sim:.3f},{spd:.3f},{float(c_.steer) * steer_gain:.4f},{m_.gyroscope.z:.4f},"
                          f"{m_.accelerometer.y - G * math.sin(math.radians(tr_.rotation.roll)):.4f},"
                          f"{m_.accelerometer.x + G * math.sin(math.radians(tr_.rotation.pitch)):.4f},{c_.brake:.2f},{int(inside)}\n")
        if imus and t_sim >= a.warmup and not a.no_secondary:
            m = imus[-1]
            tr = ego.get_transform()
            roll = math.radians(tr.rotation.roll)
            ay = m.accelerometer.y - G * math.sin(roll)   # 경사/롤로 새어든 중력 성분 제거
            gz = m.gyroscope.z
            ctrl = ego.get_control()
            ax = m.accelerometer.x + G * math.sin(math.radians(tr.rotation.pitch))   # 경사로 새어든 중력 성분 제거 (근사)
            lane_err, lat_off, cur_wp = lane_state(cmap, ego)
            gap_f, gap_l, gap_r = neighbor_gaps(traffic_vehicles, ego, cur_wp) if traffic_vehicles else (999.0, 999.0, 999.0)
            if not traffic_vehicles:      # 차량이 없어도 차로 존재 여부는 알려준다
                if not _same_dir_lane(cur_wp.get_left_lane(), cur_wp): gap_l = -1.0
                if not _same_dir_lane(cur_wp.get_right_lane(), cur_wp): gap_r = -1.0
            steer_eq = float(ctrl.steer) * steer_gain                # 35° 기준 환산 조향 (감지기 모델과 일치)
            ev_local = slip.step(t_sim, ay, gz, spd, steer_eq)      # 로컬 참조 구현은 항상 같이 돌린다 (폴백·비교용)
            ev_box = slip_box.step(t_sim, ay, gz, spd, steer_eq)    # 예전 직사각형 규칙 — 비교용, 제어에는 쓰지 않는다
            if ev_local and t_rule["ellipse"] is None:
                t_rule["ellipse"] = (t_sim, ev_local.trigger, round(ev_local.ay_g, 3), round(ev_local.yaw_err, 3))
            if ev_box and t_rule["box"] is None:
                t_rule["box"] = (t_sim, ev_box.trigger, round(ev_box.ay_g, 3), round(ev_box.yaw_err, 3))
            ev, cmd, decided_by = None, None, "local"
            board_ctrl = None                                       # 보드가 준 이번 샘플의 비상 제어 명령 (emerg 중)
            if slip_board:
                sv = None
                try:
                    n6.send_json({"kind": "imu", "ay": float(ay), "gz": float(gz), "speed": float(spd),
                                  "steer": steer_eq, "dt": dt, "min_speed": a.min_detect_kph / 3.6,
                                  "lane_err": float(lane_err), "lat_off": float(lat_off),
                                  "gap_front": float(gap_f), "gap_left": float(gap_l), "gap_right": float(gap_r),
                                  "ax": float(ax), "brake_cmd": float(ctrl.brake),
                                  "ice_lane_off": float(lane_center_offset(cmap, ego, cur_wp, slip_lane_id) if slip_lane_id is not None else 0.0),
                                  "front_rel_v": float(getattr(neighbor_gaps, "front_rel_v", 0.0))})
                    sv = n6.recv_json()
                except zmq.Again:
                    sv = None
                if sv and sv.get("ok"):
                    slip_stats["board"] += 1; slip_stats["ns_sum"] += int(sv["latency_ns"]); slip_stats["fail_streak"] = 0
                    slip_stats["ns_max"] = max(slip_stats["ns_max"], int(sv["latency_ns"]))
                    # 표본마다 남긴다 — 집계(평균·최댓값)만으로는 "응답이 시간에 걸쳐 평평하다"를
                    # 그림으로 못 보여준다.
                    # 표본별로 NPU 가동 여부를 적지는 않는다. 호스트–브리지 통신이 직렬이라
                    # 프레임 판정을 받은 뒤에야 IMU 를 보내기 때문이다. 주행 단위 플래그
                    # (npu_busy = 1차 켜짐)만 의미가 있고, 그건 board_slip_latency 에 이미 있다.
                    slip_lat.append((round(t_sim, 3), round(int(sv["latency_ns"]) / 1000.0, 2)))
                    if sv.get("emerg"):
                        board_ctrl = {"brake": float(sv["brake"]), "steer": float(sv["steer"]), "mode": sv.get("mode", "?")}
                    if sv["slip"]:
                        ev = SlipEvent(t=t_sim, trigger=sv["trigger"], ay_g=float(sv["ay_g"]), yaw_err=float(sv["yaw_err"]))
                        cmd = {"brake": float(sv["brake"]), "steer": float(sv["steer"]), "throttle": 0.0}
                        decided_by = "stm32n6"
                else:
                    slip_stats["fallback"] += 1; slip_stats["fail_streak"] += 1
                    ev = ev_local
                    if slip_stats["fail_streak"] >= 3:
                        print(f"[{t_sim:6.2f}s] 보드 IMU 응답 3회 연속 실패 → 남은 주행은 2차 방어 로컬", flush=True); slip_board = False
            else:
                ev = ev_local
            if ev and state not in ("EMERG",):
                t_slip = t_sim; state = "EMERG"; slip_lane_id = cur_wp.lane_id      # 미끄러진 차로 기억 (그쪽으로 회피 금지)
                cmd = cmd or emergency_command(ev)
                events.append({"t": round(t_sim,2), "event": "secondary_slip", "trigger": ev.trigger, "decided_by": decided_by,
                               "ay_g": round(ev.ay_g,3), "yaw_err": round(ev.yaw_err,3), "speed_kph": round(spd*3.6,1),
                               # 타원 규칙이 먼저 확정했으면 ev_local 은 이미 소진돼 None 이다.
                               # 그 경우를 불일치로 적으면 거짓말이 된다 — 먼저 잡은 것이지 안 잡은 게 아니다.
                               "local_agrees": bool(ev_local or t_rule["ellipse"])})
                print(f"[{t_sim:6.2f}s] 2차 미끄러짐 감지 ({ev.trigger}) ay={ev.ay_g:.2f}g yaw_err={ev.yaw_err:.2f} → 비상 제어")
                who = "STM32N6 RTOS" if decided_by == "stm32n6" else "local"
                sec_base = f"2ND DEFENSE [{who}] IMU SLIP ({ev.trigger}) ay {ev.ay_g:.2f}g yaw {ev.yaw_err:.2f}"
                sec_info = sec_base
                print(f"           판정 주체: {who}  (로컬 참조 구현 동시 확정: {'예' if ev_local else '아니오'})", flush=True)
                ego.set_autopilot(False, 8000)
                ego.apply_control(carla.VehicleControl(throttle=0.0, brake=cmd["brake"], steer=cmd["steer"]))

        # ---------- 상태별 제어 ----------
        if control_lost and state not in ("CRASH",):
            # 조향·가속 없음. 브레이크도 없다 — 빙판 위에서 잠긴 바퀴는 조향을 잃을 뿐이다.
            ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0, steer=0.0))
        if state == "CRASH":
            ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, steer=0.0))
            if t_sim - t_crash > 2.0: break
        if state == "WARN":
            ego.set_autopilot(False, 8000); state = "BRAKE"
        if state == "BRAKE":
            ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, steer=0.0))
            if spd < 0.3:
                state = "STOPPED"
                events.append({"t": round(t_sim,2), "event": "stopped", "dist_to_patch_m": round(dist,1),
                               "stopped_before_patch": bool(dist > patch.extent[0])})
                print(f"[{t_sim:6.2f}s] 정지 완료  패치까지 {dist:.1f}m  (진입 {'회피' if dist > patch.extent[0] else '실패'})")
        elif state == "EMERG":
            if board_ctrl is not None:
                b_, s_cmd, mode_now, who_now = board_ctrl["brake"], board_ctrl["steer"], board_ctrl["mode"], "STM32N6 RTOS"
            elif emerg_mode == "stopped" and slip_board:
                b_, s_cmd, mode_now, who_now = 1.0, 0.0, "stopped", "STM32N6 RTOS"   # 보드가 '정지'로 끝낸 뒤에는 정지 유지 (라벨 유지)
            else:
                b_, s_cmd, mode_now = local_emerg.step(spd, gz if imus else 0.0, lane_err if imus else 0.0, lat_off if imus else 0.0,
                                                       gap_f if imus else 999.0, gap_l if imus else 999.0, gap_r if imus else 999.0)
                who_now = "local"
            ego.apply_control(carla.VehicleControl(throttle=0.0, brake=float(b_), steer=float(s_cmd)))
            if mode_now != emerg_mode:
                emerg_mode = mode_now
                events.append({"t": round(t_sim,2), "event": "emergency_mode", "mode": mode_now, "by": who_now,
                               "gap_front_m": round(gap_f,1), "gap_left_m": round(gap_l,1), "gap_right_m": round(gap_r,1), "speed_kph": round(spd*3.6,1)})
                print(f"[{t_sim:6.2f}s] 비상 제어 모드 → {mode_now} [{who_now}]  앞차 {gap_f:.0f}m 좌 {gap_l:.0f}m 우 {gap_r:.0f}m")
            mode_txt = {"lane_keep": "LANE KEEP + ABS", "evade_left": "EVADE LEFT (free lane)", "evade_right": "EVADE RIGHT (free lane)",
                        "hard_stop": "HARD STOP (no free lane)", "stopped": "STOPPED"}.get(mode_now, mode_now)
            sec_info = (sec_base or "2ND DEFENSE") + f"  ->  [{who_now}] {mode_txt}  brake {b_:.2f} steer {s_cmd:+.2f}  gap {gap_f:.0f}m"
            if spd < 0.3:
                state = "STOPPED"
                events.append({"t": round(t_sim,2), "event": "stabilized", "slip_to_stop_ms": round((t_sim - t_slip)*1000,1)})
                print(f"[{t_sim:6.2f}s] 비상 제어로 정지")

        rows.append({"t": round(t_sim,3), "speed_kph": round(spd*3.6,2), "dist_m": round(dist,2),
                     "risk": round(float(risk),3), "state": state, "inside": bool(inside),
                     "p": [round(float(z),3) for z in p]})

        # ---------- 영상 (여러 시점을 같은 틱에서 동시에 녹화) ----------
        if not a.no_video and view is not None:
            col = {"DRIVE": (0,255,0), "WARN": (0,220,255), "BRAKE": (0,140,255), "EMERG": (0,0,255), "STOPPED": (200,200,200), "CRASH": (0,0,255)}[state]
            icecol = (255, 200, 90) if not inside else (100, 100, 255)
            hud = f"{t_sim:5.2f}s {spd*3.6:5.1f}km/h  patch {dist:5.1f}m  risk {risk:.2f}  {state}"
            def put_hud(img, name):
                cv2.putText(img, hud, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
                if warn_info is not None:      # 1차 경고 발화 시점의 근거를 계속 띄워둔다
                    cv2.rectangle(img, (0, 38), (img.shape[1], 74), (0, 0, 150), -1)
                    cv2.putText(img, warn_info, (10, 63), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255,255,255), 1, cv2.LINE_AA)
                if sec_info is not None:       # 2차 방어 발화 근거
                    y0 = 78 if warn_info is not None else 38
                    cv2.rectangle(img, (0, y0), (img.shape[1], y0 + 36), (0, 90, 180), -1)
                    cv2.putText(img, sec_info, (10, y0 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.42 if len(sec_info) > 70 else 0.5, (255,255,255), 1, cv2.LINE_AA)
                if perception_degraded:
                    cv2.rectangle(img, (0, img.shape[0] - 60), (img.shape[1], img.shape[0] - 44), (0, 0, 120), -1)
                    cv2.putText(img, "AI PERCEPTION PATH DEGRADED (no NPU verdict) - RTOS 2nd defense only", (10, img.shape[0] - 48),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
                if a.disable_primary and a.no_secondary:
                    cv2.putText(img, "BASELINE: NO DEFENSE - camera missed, no IMU slip detector (for comparison)",
                                (10, img.shape[0] - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
                elif a.disable_primary:
                    cv2.putText(img, "CAMERA: MISSED (simulated) - 2nd defense armed: IMU slip detector on "
                                 + ("STM32N6 (ThreadX)" if slip_board or (sec_info and "STM32N6" in sec_info) else "host"),
                                (10, img.shape[0] - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
                cv2.putText(img, name, (10, img.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)
                if inside: cv2.putText(img, "ICE", (img.shape[1]-90, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,0,255), 2)
                return img
            # 1인칭 (모델 입력 카메라)
            fr = view.copy()
            if not a.no_overlay:
                oa = 0.45 if a.no_ice_render else 0.0    # 합성 시엔 채우지 않는다 (윤곽·기둥만) — 노면이 실제처럼 보여야 한다
                fr = draw_patch_overlay(fr, poly, cam, K, color=icecol, alpha=oa,
                                        label=f"BLACK ICE  {dist:.0f}m" if dist > 3 else "BLACK ICE")
            fr = put_hud(fr, "FRONT CAM (NPU input)")
            if det is not None:
                h, wd = fr.shape[:2]
                cv2.rectangle(fr, (int(wd*det.roi_left), int(h*det.roi_top)), (int(wd*det.roi_right), int(h*det.roi_bottom)), (255,255,0), 1)
                cv2.putText(fr, "  ".join(f"{c[:4]} {v:.2f}" for c, v in zip(ROAD_CLASSES, p)), (10, h-54),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,0), 1)
            # 조감 (내장 빙판이 실제로 보이므로 윤곽만 옅게)
            bv = None
            if bird is not None and birds:
                bi = birds[-1]
                bv = np.frombuffer(bi.raw_data, np.uint8).reshape(bi.height, bi.width, 4)[:, :, :3].copy()
                if snow_field_b is not None:
                    if sems_b:
                        sb_ = sems_b[-1]; segb = np.frombuffer(sb_.raw_data, np.uint8).reshape(sb_.height, sb_.width, 4)[:, :, 2]
                        bv = snow_cover(bv, segb, np.random.default_rng(step + 1))
                    bv = snow_field_b.step(bv, t_sim, wind=0.3, haze=0.06)
                if not a.no_overlay:
                    bv = draw_patch_overlay(bv, poly, bird, Kb, color=icecol, alpha=0.06,
                                            label=f"BLACK ICE  {dist:.0f}m" if dist > 3 else "BLACK ICE")
                bv = put_hud(bv, "BIRD EYE")
            # 라이다 (위에서 본 점군)
            ld = None; lds = None
            ego_tf_now = ego.get_transform()
            vboxes = vehicle_boxes(traffic_vehicles, ego_tf_now) if traffic_vehicles else []
            if lidar is not None and lidars:
                ld = put_hud(render_lidar3d(lidars[-1], ego_tf_now, poly if a.show_patch else ([], []), icecol, boxes=vboxes),
                             "LIDAR 64ch 3D point cloud (height color) + 3D boxes")
            if slidar is not None and slidars:
                sp_ = slidars[-1]; xyz = np.stack([sp_["x"], sp_["y"], sp_["z"], sp_["cos"]], 1).astype(np.float32)
                cols_ = sem_colors(sp_["tag"])
                # 빙판 구간 점: 폴리곤 안(차량 좌표계 판정) 이면서 노면 근처
                if len(poly[0]) >= 2:
                    P = world_to_lidar(list(poly[0]) + list(reversed(poly[1])), ego_tf_now)
                    inside_ = cv2.pointPolygonTest  # 폴리곤 판정은 OpenCV 로
                    cnt = P[:, :2].astype(np.float32).reshape(-1, 1, 2)
                    near = np.abs(xyz[:, 2] + 2.4) < 0.25
                    idx = np.where(near)[0]
                    if len(idx):
                        sel = np.array([cv2.pointPolygonTest(cnt, (float(xyz[i, 0]), float(xyz[i, 1])), False) >= 0 for i in idx[::2]])
                        cols_[idx[::2][sel]] = SEM_ICE
                if snow:      # 내리는 눈 입자 노이즈 (WADS 그림처럼 센서 주변 흩뿌려진 점)
                    nrng = np.random.default_rng(step)
                    ns = nrng.uniform([-6, -6, -2.3, 0], [14, 6, 2.5, 1], (900, 4)).astype(np.float32)
                    xyz = np.vstack([xyz, ns]); cols_ = np.vstack([cols_, np.tile(np.array(SEM_SNOW, np.uint8), (900, 1))])
                lds = put_hud(render_lidar3d(xyz, ego_tf_now, poly if a.show_patch else ([], []), icecol, colors=cols_, boxes=vboxes),
                              "SEMANTIC LIDAR 64ch (class color) + 3D boxes")
            outs = {}
            if "front" in views: outs["front"] = fr
            if "bev" in views and bv is not None: outs["bev"] = bv
            if "split" in views and bv is not None:
                if bv.shape[0] != fr.shape[0]:
                    bv = cv2.resize(bv, (int(bv.shape[1] * fr.shape[0] / bv.shape[0]), fr.shape[0]))
                outs["split"] = np.hstack([fr, bv])
            if "lidar" in views and ld is not None: outs["lidar"] = ld
            if "lidar_sem" in views and lds is not None: outs["lidar_sem"] = lds
            for k, img in outs.items():
                if k not in vws:
                    vws[k] = cv2.VideoWriter(str(out / f"demo_{tag}_{k}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), a.fps,
                                             (img.shape[1], img.shape[0]))
                vws[k].write(img)

        if control_lost and (spd < 0.3 or t_sim - t_lost > 8.0):
            events.append({"t": round(t_sim,2), "event": "baseline_end",
                           "reason": "정지" if spd < 0.3 else "제어 상실 8초 경과", "speed_kph": round(spd*3.6,1)}); break
        if a.no_secondary and a.disable_primary and entered and dist > patch.extent[0] + 40 and state == "DRIVE":
            events.append({"t": round(t_sim,2), "event": "passed_patch", "speed_kph": round(spd*3.6,1)}); break
        if state == "STOPPED" and t_sim > 1.0 and spd < 0.2:
            if len([e for e in events if e["event"] in ("stopped","stabilized")]) and t_sim - events[-1]["t"] > 1.5:
                break
    else:
        events.append({"t": round(t_sim,2), "event": "timeout"})

    for v_ in vws.values(): v_.release()
    if vws: print("[video] " + ", ".join(f"demo_{tag}_{k}.mp4" for k in vws))
    if slip_stats["board"]:
        if slip_lat:
            (out / f"slip_latency_{tag}.json").write_text(json.dumps(
                {"npu_busy": not a.disable_primary, "samples": slip_lat}))
        _lat = {"t": round(t_sim,2), "event": "board_slip_latency", "samples": slip_stats["board"], "npu_busy": not a.disable_primary,
                "avg_us": round(slip_stats['ns_sum']/max(slip_stats['board'],1)/1000, 1), "max_us": round(slip_stats['ns_max']/1000, 1)}
        events.append(_lat)
        # WCET 는 안전 논증의 핵심인데 events_{tag}.json 은 태그당 하나뿐이라 재실행하면 덮인다.
        # (문서의 "NPU 유휴 최대 13.6 us" 가 이미 현재 데이터 16.2 us 와 어긋난다.)
        # 주장하려면 지워지지 않는 기록이 있어야 하므로 여기에 한 줄씩 덧붙인다.
        try:
            import datetime as _dt
            _rec = dict(_lat)
            _rec.update(tag=tag, weather=a.weather, target_kph=a.target_kph, fusion=a.fusion,
                        traffic=a.traffic, at=_dt.datetime.now().isoformat(timespec="seconds"))
            with open(os.path.join(os.path.dirname(str(out)), "board_wcet.jsonl"), "a") as _fh:
                _fh.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        except Exception as _e:
            print(f"[wcet] 누적 로그 기록 실패: {_e}", flush=True)
    # 판정 규칙 A/B — 같은 주행선에서 타원과 직사각형이 각각 언제 확정했나
    # 둘 다 발화하지 않아도 기록한다 — "안 터졌다"는 것 자체가 증거일 때가 있다
    # (조향 지연 보정을 넣은 뒤 대조군에서 침묵하는 것이 그렇다).
    rule_ab = None
    if not a.no_secondary:
        _pe = next((e["t"] for e in events if e["event"] == "patch_enter"), None)
        def _pack(v):
            if not v: return None
            t_, tg, ay_, ye_ = v
            return {"t": round(t_, 2), "trigger": tg, "ay_g": ay_, "yaw_err": ye_,
                    "after_enter": (round(t_ - _pe, 2) if _pe is not None else None)}
        rule_ab = {"ellipse": _pack(t_rule["ellipse"]), "box": _pack(t_rule["box"]),
                   "yaw_lag_s": float(getattr(slip, "yaw_lag_s", 0.0))}
        e_, b_ = rule_ab["ellipse"], rule_ab["box"]
        if e_ and b_:
            rule_ab["gain_s"] = round(b_["t"] - e_["t"], 2)
        print(f"\n[규칙 A/B] 타원 {e_['t']:.2f}s ({e_['trigger']})" if e_ else "\n[규칙 A/B] 타원 미발화",
              f" / 직사각형 {b_['t']:.2f}s ({b_['trigger']})" if b_ else " / 직사각형 미발화",
              (f" → 타원이 {rule_ab['gain_s']:+.2f}s 빠름" if rule_ab.get("gain_s") else ""), flush=True)
    (out / f"events_{tag}.json").write_text(json.dumps(
        {"args": vars(a), "events": events, "rule_ab": rule_ab}, indent=1))
    (out / f"trace_{tag}.json").write_text(json.dumps(rows))
    if a.fusion in ("n6", "n6npu"):
        tot = n6_stats["board"] + n6_stats["fallback"]
        avg = n6_stats["board_ns_sum"] / max(n6_stats["board"], 1) / 1000.0
        print(f"\n=== 융합 경로 ===")
        print(f"  보드 {'NPU 추론+' if a.fusion == 'n6npu' else ''}판정 {n6_stats['board']}프레임, "
              f"{'무응답' if a.fusion == 'n6npu' else '로컬 폴백'} {n6_stats['fallback']}프레임 ({n6_stats['fallback']/max(tot,1)*100:.1f}%), "
              f"보드 평균 {avg/1000:.2f}ms" if a.fusion == "n6npu" else
              f"  보드 판정 {n6_stats['board']}프레임, 로컬 폴백 {n6_stats['fallback']}프레임 ({n6_stats['fallback']/max(tot,1)*100:.1f}%), 보드 평균 {avg:.2f}us")
        if n6_stats["fallback"]:
            print("  ※ 폴백이 있었다 — 그 프레임의 판정은 보드가 아니라 이 프로세스가 했다")
        if slip_stats["board"] or slip_stats["fallback"]:
            print(f"  2차 방어(IMU): 보드 판정 {slip_stats['board']}샘플, 로컬 폴백 {slip_stats['fallback']}샘플, "
                  f"보드 평균 {slip_stats['ns_sum']/max(slip_stats['board'],1)/1000:.1f}us, 최대(WCET 관측) {slip_stats['ns_max']/1000:.1f}us")
            pass
        try:
            n6.send_json({"kind": "bye"}); n6.recv_json()
        except Exception:
            pass

    print("\n=== 요약 ===")
    for e in events: print(" ", e)
    print(f"저장: {out}", flush=True)
    if world_textured:                       # 요약을 먼저 찍는다 — 배치가 요약을 보고 다음 주행으로 넘어간다
        cleanup(); cleaned = True
        try:
            print("[world] reload_world 로 재질 복원 중...", flush=True); client.reload_world(False); print("[world] 복원 완료", flush=True)
        except Exception as e:
            print(f"[world] reload_world 실패: {e} — 다음 주행 전에 수동 복원 필요")
except BaseException:
    import traceback; traceback.print_exc(); sys.stdout.flush()
    raise
finally:
    cleanup()
