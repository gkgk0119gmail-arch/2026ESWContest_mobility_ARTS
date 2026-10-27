#!/usr/bin/env python3
"""2차 방어 시연: 마찰 인식 제동 시점 (SLIP_ENVELOPE) — 빙판 위 정지 차량 앞에서 '언제부터 서야 하나'.

장면: Town04 평탄 직선, 빙판(마찰 트리거 + 광택 타일) 위에 고장 차량이 서 있다. 자차는 간격 유지 순항(운전자 개입 없음).
같은 C 코드(slip_envelope.h, 보드(안전 전용 RTOS)·파이 격리 코어가 포함하는 그대로)로 두 조건을 비교한다.
  --arm dry : 마찰을 마른 노면(표 마지막 칸)으로 고정 — 노면을 모르는 지금의 긴급제동과 같은 가정
  --arm mu  : 빙판 진입 때 '노면 확인 탭'(제동 0.3 × 0.4 s)으로 마찰을 재고 그 값으로 계산
            (진입 시점은 1차 방어가 '애매'하다고 본 순간을 대신한다 — 이 스크립트에서는 빙판 진입 위치로 정한다)
출력: <out>/env_<tag>.csv (틱마다), env_<tag>.json (요약), --video 면 env_<tag>.mp4 (추적 카메라 + 상태 패널)
CARLA 마찰 트리거 결함 두 가지를 피한다.
  · 벗어나면 마찰이 3.5 로 돌아간다 → 빙판을 정지 차량 바로 앞까지 넓게 깐다.
  · 트리거 안에 다른 차량이 있으면 자차가 트리거 밖에 있어도 처음부터 그 마찰이 걸린다 (2026-10-26 직접 확인)
    → 빙판을 정지 차량 2.5 m 앞(차체 중심 기준)에서 끝내 정지 차량이 트리거 밖에 서게 한다.
시작할 때 남은 액터를 지운다. 시작 1~3 s 가속으로 출발점이 마른 노면인지 확인해 요약에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sw/src"))
import carla  # noqa: E402
from icepredict.pi.slip_ctypes import SlipCoreC  # noqa: E402
from icepredict.sim.blackice import (spawn_ice_patch, follow_lane, waypoint_ahead, patch_road_polygon,  # noqa: E402
                                     camera_intrinsics, draw_patch_overlay)

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2000)
ap.add_argument("--arm", choices=["dry", "mu"], required=True)
ap.add_argument("--friction", type=float, default=0.2)
ap.add_argument("--target-kph", type=float, default=60.0)
ap.add_argument("--patch-ahead", type=float, default=170.0, help="출발점에서 빙판 시작까지 m")
ap.add_argument("--patch-len", type=float, default=260.0); ap.add_argument("--patch-width", type=float, default=30.0)
ap.add_argument("--obstacle-in", type=float, default=150.0, help="빙판 시작에서 정지 차량까지 m")
ap.add_argument("--max-time", type=float, default=45.0)
ap.add_argument("--spawn-rank", type=int, default=0, help="직선 점수 순위 (0 = 가장 곧고 평탄한 곳)")
ap.add_argument("--out", default=str(ROOT / "logs/envelope")); ap.add_argument("--tag", default=None)
ap.add_argument("--video", action="store_true")
ap.add_argument("--camera-risk", action="store_true",
                help="1차 방어 모델의 실제 위험도로 탭 여부를 판단한다 (env_should_probe). 끄면 빙판 진입 위치에서 탭 (이전 결과 재현용)")
ap.add_argument("--model", default=os.path.expanduser("~/icepredict/models/roadnet_v1/roadnet.onnx"))
ap.add_argument("--temp", type=float, default=-3.0, help="외기 온도 °C (탭 조건, 1차 맥락)")
ap.add_argument("--weather", default="ClearNoon")
ap.add_argument("--no-ice", action="store_true", help="대조군: 마찰 트리거·얼음 합성 없음 (탭이 잘못 나가는지)")
ap.add_argument("--zone-before", type=float, default=40.0,
                help="결빙 취약 구간(지도 정보)이 빙판 시작보다 이만큼 앞에서 시작한다 m — 시스템은 구간 안 어디가 빙판인지 모른다")
ap.add_argument("--no-zone", action="store_true", help="결빙 취약 구간 정보 없음 (카메라 계기만)")
ap.add_argument("--dump-frames", default=None, help="1차 카메라 프레임(합성 후)을 0.5 s 마다 저장할 폴더")
ap.add_argument("--tiles", action="store_true", help="광택 타일을 실제로 깐다 (재질 적용이 안 되면 회색 체크무늬로 보인다 — 기본은 화면에 빙판 구역을 그린다)")
a = ap.parse_args()
tag = a.tag or (f"{a.arm}_f{a.friction}_k{a.target_kph:.0f}_o{a.obstacle_in:.0f}"
                + (f"_cam_{a.weather}_t{a.temp:+.0f}" + ("_noice" if a.no_ice else "") + ("_nozone" if a.no_zone else "") if a.camera_risk else ""))
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
DT = 0.02
MU_DRY = 0.6725
_MT = json.loads((ROOT / "sw/docs/data/gain_sched/mu_table.json").read_text())["table"]
_MU = sorted((v["mu_eff"], v["decel_by_brake"][3]) for v in _MT.values())
def true_stop(v, mu, t_r=0.3):   # 화면 표시용 정답: 이 빙판의 실측 감속(제동 1.0)으로 낸 정지 거리 — 제어에는 쓰지 않는다
    dec = float(np.interp(mu, [m for m, _ in _MU], [d for _, d in _MU]))
    return v * t_r + v * v / (2 * dec)

client = carla.Client(a.host, a.port); client.set_timeout(60.0)
world = client.get_world()
if "Town04" not in world.get_map().name:
    world = client.load_world("Town04"); time.sleep(2)
cmap = world.get_map()
orig = world.get_settings()
for x in world.get_actors():                       # 앞 주행이 남긴 트리거·차량·센서 (트리거가 남으면 마찰이 오염된다)
    if x.type_id.startswith(("static.trigger", "vehicle.", "sensor.", "static.prop.mesh")):
        try: x.destroy()
        except Exception: pass
s = world.get_settings(); s.synchronous_mode = True; s.fixed_delta_seconds = DT; world.apply_settings(s)
world.set_weather(getattr(carla.WeatherParameters, a.weather))
actors = []


def cleanup():
    for x in actors[::-1]:
        try: x.destroy()
        except Exception: pass
    world.apply_settings(orig)


def straight_spawn(need, rank=0):
    cands = []
    for sp in cmap.get_spawn_points():
        wp0 = cmap.get_waypoint(sp.location, project_to_road=True); y0 = wp0.transform.rotation.yaw; z0 = wp0.transform.location.z
        cur, moved, dy, dz, ok = wp0, 0.0, 0.0, 0.0, True
        while moved < need:
            nx = cur.next(5.0)
            if not nx: ok = False; break
            cur = nx[0]; moved += 5.0
            dy = max(dy, abs((cur.transform.rotation.yaw - y0 + 180) % 360 - 180)); dz = max(dz, abs(cur.transform.location.z - z0))
        if ok: cands.append((dy + 10 * dz, sp))
    cands.sort(key=lambda c: c[0])
    # 서로 50 m 이상 떨어진 출발점만 순위로 센다 (같은 도로의 이웃 스폰점이 순위를 다 차지하지 않게)
    picked = []
    for c in cands:
        if all(c[1].location.distance(p[1].location) > 50 for p in picked): picked.append(c)
    return picked[min(rank, len(picked) - 1)]


def spawn_tiles(entry_wp, length, tile=7.0):
    """빙판을 눈에 보이게: 차로를 따라 광택 평면 타일 (물리 없음). 재질은 carla_demo.spawn_native_ice 와 같다."""
    bl = world.get_blueprint_library(); before = set(world.get_names_of_all_objects()); n = int(math.ceil(length / tile))
    for i in range(n):
        wp = follow_lane(entry_wp, i * tile + tile / 2)
        bp = bl.find("static.prop.mesh"); bp.set_attribute("mesh_path", "/Engine/BasicShapes/Plane.Plane")
        bp.set_attribute("scale", str(tile)); bp.set_attribute("mass", "0")
        t = wp.transform
        act = world.try_spawn_actor(bp, carla.Transform(carla.Location(t.location.x, t.location.y, t.location.z + 0.01), carla.Rotation(yaw=t.rotation.yaw)))
        if act: act.set_simulate_physics(False); actors.append(act)
    for _ in range(3): world.tick()
    S = 4; diff = carla.TextureColor(S, S); arm = carla.TextureFloatColor(S, S)
    for y in range(S):
        for x in range(S):
            diff.set(x, y, carla.Color(150, 175, 195, 255)); arm.set(x, y, carla.FloatColor(1.0, 0.05, 0.0, 0.0))
    for name in set(world.get_names_of_all_objects()) - before:
        try:
            world.apply_color_texture_to_object(name, carla.MaterialParameter.Diffuse, diff)
            world.apply_float_color_texture_to_object(name, carla.MaterialParameter.AO_Roughness_Metallic_Emissive, arm)
        except Exception: pass


try:
    sc, sp = straight_spawn(a.patch_ahead + a.obstacle_in + 40, a.spawn_rank)
    bl = world.get_blueprint_library()
    ego = world.try_spawn_actor(bl.find("vehicle.tesla.model3"), sp); actors.append(ego); world.tick()
    steer_gain = float(ego.get_physics_control().wheels[0].max_steer_angle) / 35.0
    wp0 = cmap.get_waypoint(ego.get_location(), project_to_road=True)
    entry = follow_lane(wp0, a.patch_ahead)
    a.patch_len = min(a.patch_len, a.obstacle_in - 2.5)   # 정지 차량은 트리거 밖 (위 결함)
    center = follow_lane(wp0, a.patch_ahead + a.patch_len / 2)
    patch = spawn_ice_patch(world, center, length_m=a.patch_len, width_m=a.patch_width, friction=(1.0 if a.no_ice else a.friction))
    actors.append(patch.actor)
    if a.tiles: spawn_tiles(entry, a.patch_len)
    ow = follow_lane(entry, a.obstacle_in); ot = ow.transform
    obst = world.try_spawn_actor(bl.find("vehicle.audi.a2"), carla.Transform(ot.location + carla.Location(z=0.3), ot.rotation))
    actors.append(obst); world.tick(); obst.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
    print(f"[env] 직선 점수 {sc:.2f}, 빙판 {a.patch_ahead:.0f}~{a.patch_ahead + a.patch_len:.0f} m (friction {a.friction}), "
          f"정지 차량 빙판 시작 +{a.obstacle_in:.0f} m, 조건 {a.arm}", flush=True)
    imu_ax = [0.0]
    imu = world.spawn_actor(bl.find("sensor.other.imu"), carla.Transform(), attach_to=ego); actors.append(imu)
    imu_ay = [0.0]
    imu.listen(lambda m: (imu_ax.__setitem__(0, m.accelerometer.x), imu_ay.__setitem__(0, m.accelerometer.y)))
    hit = []
    col = world.spawn_actor(bl.find("sensor.other.collision"), carla.Transform(), attach_to=ego); actors.append(col)
    col.listen(lambda e: hit.append(e.other_actor.type_id))
    frame = [None]
    if a.video:
        import cv2
        cb = bl.find("sensor.camera.rgb"); W, H = 900, 540; FOV = 85
        cb.set_attribute("image_size_x", str(W)); cb.set_attribute("image_size_y", str(H)); cb.set_attribute("fov", str(FOV))
        cam = world.spawn_actor(cb, carla.Transform(carla.Location(x=-11.0, z=6.5), carla.Rotation(pitch=-17)), attach_to=ego)
        K = camera_intrinsics(W, H, FOV); ice_poly = patch_road_polygon(cmap, patch, half_width_m=7.0)
        front = [None]                                     # 1차 방어 카메라 시점 (작은 화면)
        fb = bl.find("sensor.camera.rgb"); fb.set_attribute("image_size_x", "320"); fb.set_attribute("image_size_y", "180"); fb.set_attribute("fov", "90")
        fcam = world.spawn_actor(fb, carla.Transform(carla.Location(x=1.6, z=1.4)), attach_to=ego); actors.append(fcam)
        fcam.listen(lambda im: front.__setitem__(0, np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, :3].copy()))
        actors.append(cam); cam.listen(lambda im: frame.__setitem__(0, np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, :3].copy()))
        vw = cv2.VideoWriter(str(out / f"env_{tag}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 25, (W + 380, H))

    risk_now = [-1.0]
    if a.camera_risk:
        # 1차 방어: carla_demo 와 같은 카메라·얼음 합성·모델·융합 (icepredict.sim.camera, ice_render, RoadNetDetector, RiskFuser)
        from icepredict.sim import camera as camcfg
        from icepredict.sim.blackice import RoadNetDetector, patch_image_mask
        from icepredict.sim.ice_render import composite_ice, random_ice_params
        from icepredict.pi.fusion import RiskFuser
        from icepredict.pi.context import WeatherObs, LocationCtx, build_context
        rgb_buf, sem_buf = [None], [None]
        rc = world.spawn_actor(camcfg.apply_camera_bp(bl.find("sensor.camera.rgb")), camcfg.camera_transform(carla), attach_to=ego); actors.append(rc)
        sc_ = world.spawn_actor(camcfg.apply_camera_bp(bl.find("sensor.camera.semantic_segmentation"), exposure=None), camcfg.camera_transform(carla), attach_to=ego)
        actors.append(sc_)
        rc.listen(lambda im: rgb_buf.__setitem__(0, im)); sc_.listen(lambda im: sem_buf.__setitem__(0, im))
        Kc = camera_intrinsics(camcfg.WIDTH, camcfg.HEIGHT, camcfg.FOV); poly_c = patch_road_polygon(cmap, patch)
        ice_params = random_ice_params(np.random.default_rng(7), night="Night" in a.weather)
        det = RoadNetDetector(a.model)
        _w = world.get_weather(); _precip = _w.precipitation / 100.0 * 20.0 + _w.precipitation_deposits / 100.0 * 2.0
        ctx = build_context(WeatherObs(temp_c=a.temp, humidity=min(99.0, 88.0 + _w.precipitation / 10.0),
                                       temp_trend_c_per_h=(-1.0 if a.temp <= 2.0 else 0.0), precip_mm=round(_precip, 2)),
                            LocationCtx(feature="bridge", hour=23 if _w.sun_altitude_angle < 0 else 13))
        fuser = RiskFuser(ctx.weights, threshold=ctx.threshold)
        print(f"[env] 1차 방어 모델 켬 — 융합 문턱 {ctx.threshold:.2f}, 기온 {a.temp:+.0f}°C, 날씨 {a.weather}"
              + (", 빙판 없음(대조군)" if a.no_ice else ""), flush=True)
        cam_view = [None]
        from icepredict.common.protocol import ROAD_CLASSES; ICE_IDX = ROAD_CLASSES.index("black_ice")

        def camera_risk():
            if rgb_buf[0] is None or sem_buf[0] is None: return -1.0
            im = rgb_buf[0]; view = np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, :3].copy()
            if not a.no_ice:
                sm = sem_buf[0]; seg = np.frombuffer(sm.raw_data, np.uint8).reshape(sm.height, sm.width, 4)[:, :, 2].copy()
                mask = patch_image_mask(poly_c, rc, Kc, view.shape)
                if mask.any(): view = composite_ice(view, seg, mask, rng=np.random.default_rng(7), **ice_params)
            cam_view[0] = view
            p = det.infer(np.ascontiguousarray(view[:, :, ::-1]))
            if a.dump_frames and step % 25 == 0:
                import cv2; os.makedirs(a.dump_frames, exist_ok=True)
                cv2.imwrite(os.path.join(a.dump_frames, f"{step:05d}_gap{gap:.0f}_ice{p[ICE_IDX]:.2f}.jpg"), view)
            return float(fuser.fuse(p.tolist(), spec=None, lane=None).risk)
    core = SlipCoreC(DT, 3, 25 / 3.6, variant="env"); core.env_reset(MU_DRY)
    v_t = a.target_kph / 3.6
    log = open(out / f"env_{tag}.csv", "w")
    log.write("t,v,ax,gap,inside,mode,brake,throttle,mu,d_stop,d_need,v_max,risk,blocked\n")
    blk_count = {}
    ev = {"tag": tag, "arm": a.arm, "friction": a.friction, "target_kph": a.target_kph, "obstacle_in": a.obstacle_in,
          "patch_ahead": a.patch_ahead}
    probed = False; t = 0.0; prev_v = 0.0; t_end = None; last_mode = None; hold_t = 0.0
    for step in range(int(a.max_time / DT)):
        world.tick(); t += DT
        tf = ego.get_transform(); vel = ego.get_velocity(); v = math.hypot(vel.x, vel.y)
        y = math.radians(tf.rotation.yaw); ol = obst.get_location()
        dx, dy = ol.x - tf.location.x, ol.y - tf.location.y
        gap = dx * math.cos(y) + dy * math.sin(y) - 0.5 * (ego.bounding_box.extent.x * 2 + obst.bounding_box.extent.x * 2)
        inside = patch.contains(tf.location)
        blk = []
        s_prog = tf.location.distance(sp.location)              # 직선 구간 진행 거리
        zone = (not a.no_zone) and (a.patch_ahead - a.zone_before <= s_prog <= a.patch_ahead + a.patch_len)
        if a.camera_risk:
            if step % 2 == 0: risk_now[0] = camera_risk()
            if a.arm == "mu":
                started, blk = core.env_should_probe(DT, risk_now[0], a.temp, v, imu_ax[0], imu_ay[0], 0.0, gap, zone)
                for b_ in blk: blk_count[b_] = blk_count.get(b_, 0) + 1
                if started:
                    probed = True
                    ev.setdefault("probes", []).append({"t": round(t, 2), "by": started, "kph": round(v * 3.6, 1), "gap": round(gap, 1),
                                                        "risk": round(risk_now[0], 3), "inside_ice": bool(inside)})
                    why = "결빙 취약 구간" if started == "zone" else f"1차 위험도 {risk_now[0]:.2f} 애매 → 본 지점 도착"
                    print(f"[{t:6.2f}s] {why} → 노면 확인 탭 — {v*3.6:.1f} km/h, 빙판 {'위' if inside else '밖'}, 정지 차량 {gap:.0f} m", flush=True)
        elif a.arm == "mu" and inside and not probed:
            core.env_probe(); probed = True; ev["t_probe"] = round(t, 2); ev["v_probe_kph"] = round(v * 3.6, 1)
            print(f"[{t:6.2f}s] 빙판 의심 → 노면 확인 탭 (뗌 0.2 s + 제동 0.3 × 0.4 s) — {v*3.6:.1f} km/h, 정지 차량까지 {gap:.0f} m", flush=True)
        o = core.env_step(DT, v, imu_ax[0], gap, 0.0)
        if o["mode"] != last_mode:
            print(f"[{t:6.2f}s] {o['mode']:8s} μ {o['mu']:.3f}  {v*3.6:5.1f} km/h  간격 {gap:6.1f} m  필요 {o['d_need']:6.1f} m", flush=True)
            ev.setdefault("modes", []).append({"t": round(t, 2), "mode": o["mode"], "gap": round(gap, 1), "kph": round(v * 3.6, 1),
                                               "mu": round(o["mu"], 4), "d_need": round(o["d_need"], 1)})
            if o["mode"] != "PROBE" and last_mode == "PROBE":
                ev["mu_probe"] = round(o["mu_probe"], 4)
                if ev.get("probes"): ev["probes"][-1]["mu_probe"] = round(o["mu_probe"], 4)
            last_mode = o["mode"]
        # 차선 유지 + 간격 유지 순항
        wp = cmap.get_waypoint(tf.location, project_to_road=True); nx = wp.next(6.0); tg = (nx[0] if nx else wp).transform.location
        le = (math.atan2(tg.y - tf.location.y, tg.x - tf.location.x) - y + math.pi) % (2 * math.pi) - math.pi
        st = max(-0.5, min(0.5, 1.0 * le / 0.61086524 / steer_gain))
        if o["mode"] == "PREBRAKE": thr = o["thr_cap"]                     # 예비 제동 중: 섬이 낸 가속 페달 그대로
        else: thr = min(max(0.0, min(0.8, 0.6 * (v_t - v) + 0.25)), o["thr_cap"]) if o["brake"] == 0 else 0.0
        if hit: thr, brk = 0.0, 1.0
        else: brk = o["brake"]
        ego.apply_control(carla.VehicleControl(throttle=float(thr), brake=float(brk), steer=float(st)))
        log.write(f"{t:.2f},{v:.3f},{imu_ax[0]:.3f},{gap:.2f},{int(inside)},{o['mode']},{brk:.3f},{thr:.3f},{o['mu']:.4f},{o['d_stop']:.2f},{o['d_need']:.2f},{o['v_max']:.2f},{risk_now[0]:.3f},{'|'.join(blk)}\n")
        if hit and "collision" not in ev:
            ev["collision"] = {"t": round(t, 2), "with": hit[0], "pre_kph": round(prev_v * 3.6, 1)}
            print(f"[{t:6.2f}s] 충돌 — {hit[0]}, 직전 {prev_v*3.6:.1f} km/h", flush=True); t_end = t + 2.0
        if o["mode"] == "HOLD" or (v < 0.05 and t > 5):
            hold_t += DT
            if hold_t > 0.2 and "stopped" not in ev and not hit:
                ev["stopped"] = {"t": round(t, 2), "gap": round(gap, 2)}; print(f"[{t:6.2f}s] 정지 — 정지 차량까지 {gap:.1f} m", flush=True); t_end = t + 2.0
        if a.video and frame[0] is not None and step % 2 == 0:
            import cv2
            img = frame[0].copy() if a.no_ice else draw_patch_overlay(
                frame[0].copy(), ice_poly, cam, K, color=(255, 215, 150), alpha=0.38,
                label=f"BLACK ICE  mu {0.82 * a.friction:.2f}" + ("" if a.camera_risk else "  (camera missed)"))
            if a.camera_risk and cam_view[0] is not None:  # 왼쪽 위: 1차 방어 카메라가 실제로 본 프레임과 위험도
                fi = cv2.resize(cam_view[0], (320, 240)); r_ = risk_now[0]
                col_ = (60, 60, 255) if r_ >= 0.6 else (0, 200, 255) if r_ >= 0.3 else (120, 220, 120)
                cv2.rectangle(fi, (0, 0), (319, 239), (230, 230, 230), 2); cv2.rectangle(fi, (0, 208), (320, 240), (0, 0, 0), -1)
                cv2.putText(fi, f"1st defense risk {r_:.2f}  " + ("ALARM" if r_ >= 0.6 else "UNSURE" if r_ >= 0.3 else "clear"),
                            (6, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col_, 1, cv2.LINE_AA)
                img[12:252, 12:332] = fi
            elif front[0] is not None:                     # 왼쪽 위: 1차 방어 카메라 — 이 장면은 카메라가 놓쳤다고 가정한다
                fi = front[0].copy(); cv2.rectangle(fi, (0, 0), (319, 179), (230, 230, 230), 2)
                cv2.rectangle(fi, (0, 152), (320, 180), (0, 0, 0), -1)
                cv2.putText(fi, "1st defense camera: ice NOT detected", (6, 171), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 180, 255), 1, cv2.LINE_AA)
                img[12:192, 12:332] = fi
                cv2.putText(img, "(missed - simulated)", (14, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            P = np.full((H, 380, 3), (24, 24, 26), np.uint8)
            def put(txt, yy, sz=0.62, col=(235, 235, 235), th=1):
                cv2.putText(P, txt, (18, yy), cv2.FONT_HERSHEY_SIMPLEX, sz, col, th, cv2.LINE_AA)
            title = "BASELINE: dry-road braking point" if a.arm == "dry" else "2ND DEFENSE: friction-aware braking"
            put(title, 34, 0.55, (120, 200, 255) if a.arm == "mu" else (180, 180, 180), 1)
            put(f"speed  {v*3.6:5.1f} km/h", 76, 0.75)
            mu_txt = f"{o['mu']:.2f}" + ("  (assumed dry)" if a.arm == "dry" else ("  (measured)" if probed and o['mode'] != 'PROBE' else ""))
            put(f"road mu  {mu_txt}", 112, 0.62)
            mc = {"CRUISE": (200, 200, 200), "PROBE": (0, 215, 255), "CAUTION": (0, 200, 255), "PREBRAKE": (0, 150, 255),
                  "BRAKE": (60, 60, 255), "HOLD": (90, 220, 90)}.get(o["mode"], (255, 255, 255))
            put(o["mode"] if o["mode"] != "PROBE" else "ROAD PROBE TAP", 156, 0.9, mc, 2)
            # 막대: 남은 간격 vs 필요 정지 거리 (0~180 m)
            sc_ = 340 / 180.0
            def bar(yy, val, col, label):
                cv2.rectangle(P, (18, yy), (18 + int(min(val, 180) * sc_), yy + 22), col, -1)
                cv2.putText(P, f"{label} {val:5.0f} m", (18, yy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 210, 210), 1, cv2.LINE_AA)
            bar(205, max(gap, 0.0), (200, 200, 200), "gap to stopped car")
            bar(265, o["d_need"], (60, 60, 255) if o["d_need"] >= gap else (90, 200, 90), "system: needed to stop")
            if inside: bar(325, true_stop(v, 0.82 * a.friction) + 4.0, (0, 200, 255), "real on this ice")
            put(f"ice: {'ON' if inside else 'ahead'}" + ("   freeze-prone zone (map)" if zone else ""), 375, 0.55,
                (255, 220, 120) if inside else (170, 170, 170))
            if "collision" in ev: put(f"CRASH  {ev['collision']['pre_kph']:.0f} km/h", 440, 1.0, (60, 60, 255), 2)
            if "stopped" in ev: put(f"STOPPED  {ev['stopped']['gap']:.1f} m short", 440, 0.85, (90, 220, 90), 2)
            put("same C code (slip_envelope.h)", 500, 0.45, (150, 150, 150))
            vw.write(np.hstack([img, P]))
        prev_v = v
        if t_end is not None and t >= t_end: break
    else:
        ev["timeout"] = True
    log.close()
    ev["result"] = "crash" if "collision" in ev else ("stopped" if "stopped" in ev else "timeout")
    _X = np.genfromtxt(out / f"env_{tag}.csv", delimiter=",", names=True, dtype=None, encoding=None)
    _st = [float(x) for x, th in zip(_X["ax"][50:150], _X["throttle"][50:150]) if float(th) > 0.7]
    ev["start_accel"] = round(max(_st), 2) if _st else None          # 마른 노면이면 4 m/s² 이상, 빙판이면 μg 근처
    ev["patch_len_used"] = a.patch_len
    if a.camera_risk:
        rr = np.genfromtxt(out / f"env_{tag}.csv", delimiter=",", names=True, dtype=None, encoding=None)
        rk = np.array([float(x) for x in rr["risk"]]); ins = np.array([int(x) for x in rr["inside"]])
        ev.update(weather=a.weather, temp=a.temp, no_ice=a.no_ice, zone=not a.no_zone, zone_before=a.zone_before, risk_max=round(float(rk.max()), 3),
                  risk_frac_gray=round(float(((rk >= 0.3) & (rk < 0.6)).mean()), 3), risk_frac_alarm=round(float((rk >= 0.6).mean()), 3),
                  risk_first_gray_t=(round(float(np.array([float(x) for x in rr["t"]])[np.argmax(rk >= 0.3)]), 2) if (rk >= 0.3).any() else None),
                  n_probes=len(ev.get("probes", [])), blocked_ticks=blk_count)
    (out / f"env_{tag}.json").write_text(json.dumps(ev, indent=1, ensure_ascii=False))
    print(f"[env] 결과 {ev['result']} → {out / f'env_{tag}.json'}", flush=True)
    if a.video: vw.release()
finally:
    cleanup()
