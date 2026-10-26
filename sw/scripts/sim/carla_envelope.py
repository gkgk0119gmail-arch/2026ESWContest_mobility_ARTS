#!/usr/bin/env python3
"""2차 방어 시연: 마찰 인식 제동 시점 (SLIP_ENVELOPE) — 빙판 위 정지 차량 앞에서 '언제부터 서야 하나'.

장면: Town04 평탄 직선, 빙판(마찰 트리거 + 광택 타일) 위에 고장 차량이 서 있다. 자차는 간격 유지 순항(운전자 개입 없음).
같은 C 코드(slip_envelope.h, 보드·파이 안전 섬이 포함하는 그대로)로 두 조건을 비교한다.
  --arm dry : 마찰을 마른 노면(표 마지막 칸)으로 고정 — 노면을 모르는 지금의 긴급제동과 같은 가정
  --arm mu  : 빙판 진입 때 '노면 확인 탭'(제동 0.3 × 0.4 s)으로 마찰을 재고 그 값으로 계산
            (진입 시점은 1차 방어가 '애매'하다고 본 순간을 대신한다 — 이 스크립트에서는 빙판 진입 위치로 정한다)
출력: <out>/env_<tag>.csv (틱마다), env_<tag>.json (요약), --video 면 env_<tag>.mp4 (추적 카메라 + 상태 패널)
CARLA 마찰 트리거 결함(벗어나면 마찰 3.5 복귀)이 끼지 않게 빙판을 길고 넓게 깐다. 시작할 때 남은 액터를 지운다.
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
ap.add_argument("--out", default=str(ROOT / "logs/envelope")); ap.add_argument("--tag", default=None)
ap.add_argument("--video", action="store_true")
ap.add_argument("--tiles", action="store_true", help="광택 타일을 실제로 깐다 (재질 적용이 안 되면 회색 체크무늬로 보인다 — 기본은 화면에 빙판 구역을 그린다)")
a = ap.parse_args()
tag = a.tag or f"{a.arm}_f{a.friction}_k{a.target_kph:.0f}_o{a.obstacle_in:.0f}"
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
world.set_weather(carla.WeatherParameters.ClearNoon)
actors = []


def cleanup():
    for x in actors[::-1]:
        try: x.destroy()
        except Exception: pass
    world.apply_settings(orig)


def straight_spawn(need):
    best = None
    for sp in cmap.get_spawn_points():
        wp0 = cmap.get_waypoint(sp.location, project_to_road=True); y0 = wp0.transform.rotation.yaw; z0 = wp0.transform.location.z
        cur, moved, dy, dz, ok = wp0, 0.0, 0.0, 0.0, True
        while moved < need:
            nx = cur.next(5.0)
            if not nx: ok = False; break
            cur = nx[0]; moved += 5.0
            dy = max(dy, abs((cur.transform.rotation.yaw - y0 + 180) % 360 - 180)); dz = max(dz, abs(cur.transform.location.z - z0))
        if ok and (best is None or dy + 10 * dz < best[0]): best = (dy + 10 * dz, sp)
    return best


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
    sc, sp = straight_spawn(a.patch_ahead + a.obstacle_in + 40)
    bl = world.get_blueprint_library()
    ego = world.try_spawn_actor(bl.find("vehicle.tesla.model3"), sp); actors.append(ego); world.tick()
    steer_gain = float(ego.get_physics_control().wheels[0].max_steer_angle) / 35.0
    wp0 = cmap.get_waypoint(ego.get_location(), project_to_road=True)
    entry = follow_lane(wp0, a.patch_ahead)
    center = follow_lane(wp0, a.patch_ahead + a.patch_len / 2)
    patch = spawn_ice_patch(world, center, length_m=a.patch_len, width_m=a.patch_width, friction=a.friction); actors.append(patch.actor)
    if a.tiles: spawn_tiles(entry, min(a.patch_len, a.obstacle_in + 30))
    ow = follow_lane(entry, a.obstacle_in); ot = ow.transform
    obst = world.try_spawn_actor(bl.find("vehicle.audi.a2"), carla.Transform(ot.location + carla.Location(z=0.3), ot.rotation))
    actors.append(obst); world.tick(); obst.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
    print(f"[env] 직선 점수 {sc:.2f}, 빙판 {a.patch_ahead:.0f}~{a.patch_ahead + a.patch_len:.0f} m (friction {a.friction}), "
          f"정지 차량 빙판 시작 +{a.obstacle_in:.0f} m, 조건 {a.arm}", flush=True)
    imu_ax = [0.0]
    imu = world.spawn_actor(bl.find("sensor.other.imu"), carla.Transform(), attach_to=ego); actors.append(imu)
    imu.listen(lambda m: imu_ax.__setitem__(0, m.accelerometer.x))
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

    core = SlipCoreC(DT, 3, 25 / 3.6, variant="env"); core.env_reset(MU_DRY)
    v_t = a.target_kph / 3.6
    log = open(out / f"env_{tag}.csv", "w")
    log.write("t,v,ax,gap,inside,mode,brake,throttle,mu,d_stop,d_need,v_max\n")
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
        if a.arm == "mu" and inside and not probed:
            core.env_probe(); probed = True; ev["t_probe"] = round(t, 2); ev["v_probe_kph"] = round(v * 3.6, 1)
            print(f"[{t:6.2f}s] 빙판 의심 → 노면 확인 탭 (뗌 0.2 s + 제동 0.3 × 0.4 s) — {v*3.6:.1f} km/h, 정지 차량까지 {gap:.0f} m", flush=True)
        o = core.env_step(DT, v, imu_ax[0], gap, 0.0)
        if o["mode"] != last_mode:
            print(f"[{t:6.2f}s] {o['mode']:8s} μ {o['mu']:.3f}  {v*3.6:5.1f} km/h  간격 {gap:6.1f} m  필요 {o['d_need']:6.1f} m", flush=True)
            ev.setdefault("modes", []).append({"t": round(t, 2), "mode": o["mode"], "gap": round(gap, 1), "kph": round(v * 3.6, 1),
                                               "mu": round(o["mu"], 4), "d_need": round(o["d_need"], 1)})
            if o["mode"] == "CRUISE" and last_mode == "PROBE": ev["mu_probe"] = round(o["mu_probe"], 4)
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
        log.write(f"{t:.2f},{v:.3f},{imu_ax[0]:.3f},{gap:.2f},{int(inside)},{o['mode']},{brk:.3f},{thr:.3f},{o['mu']:.4f},{o['d_stop']:.2f},{o['d_need']:.2f},{o['v_max']:.2f}\n")
        if hit and "collision" not in ev:
            ev["collision"] = {"t": round(t, 2), "with": hit[0], "pre_kph": round(prev_v * 3.6, 1)}
            print(f"[{t:6.2f}s] 충돌 — {hit[0]}, 직전 {prev_v*3.6:.1f} km/h", flush=True); t_end = t + 2.0
        if o["mode"] == "HOLD" or (v < 0.05 and t > 5):
            hold_t += DT
            if hold_t > 0.2 and "stopped" not in ev and not hit:
                ev["stopped"] = {"t": round(t, 2), "gap": round(gap, 2)}; print(f"[{t:6.2f}s] 정지 — 정지 차량까지 {gap:.1f} m", flush=True); t_end = t + 2.0
        if a.video and frame[0] is not None and step % 2 == 0:
            import cv2
            img = draw_patch_overlay(frame[0].copy(), ice_poly, cam, K, color=(255, 215, 150), alpha=0.38,
                                     label=f"BLACK ICE  mu {0.82 * a.friction:.2f}  (camera missed)")
            if front[0] is not None:                       # 왼쪽 위: 1차 방어 카메라 — 이 장면은 카메라가 놓쳤다고 가정한다
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
            put(f"ice: {'ON' if inside else 'ahead'}", 375, 0.6, (255, 220, 120) if inside else (170, 170, 170))
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
    (out / f"env_{tag}.json").write_text(json.dumps(ev, indent=1, ensure_ascii=False))
    print(f"[env] 결과 {ev['result']} → {out / f'env_{tag}.json'}", flush=True)
    if a.video: vw.release()
finally:
    cleanup()
