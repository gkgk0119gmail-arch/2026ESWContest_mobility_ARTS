#!/usr/bin/env python3
"""IcePredict CARLA 데모: 자율주행 중 블랙아이스 이중 방어.

1차 (예방): 전방 카메라 → RoadNet 추론 → 베이지안 융합 위험도 > 임계값 → 경고 후 제동
2차 (사후): 1차가 놓쳐 타이어가 빙판에 닿아 미끄러지면 IMU 칼만 필터가 감지 → 비상 안정화

사용:
  python scripts/carla_demo.py                     # 1차+2차 모두 켬
  python scripts/carla_demo.py --disable-primary   # 1차 끄고 2차만 (미끄러짐 시연)
  python scripts/carla_demo.py --gt-detect         # 카메라 대신 정답 거리로 1차 판정 (도메인갭 우회)
"""
import argparse, json, math, os, sys, time
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, cv2, carla

from icepredict.common.protocol import ROAD_CLASSES
from icepredict.pi.fusion import RiskFuser
from icepredict.pi.context import WeatherObs, LocationCtx, build_context
from icepredict.pi.imu_slip import SlipDetector, emergency_command, G
from icepredict.sim.blackice import spawn_ice_patch, waypoint_ahead, RoadNetDetector

ICE = ROAD_CLASSES.index("black_ice")

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
ap.add_argument("--no-video", action="store_true")
a = ap.parse_args()

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

w = world.get_weather()
w.cloudiness, w.precipitation, w.precipitation_deposits, w.wetness = 70.0, 0.0, 30.0, 40.0
w.sun_altitude_angle = -15.0 if a.night else 25.0
world.set_weather(w)

actors = []
def cleanup():
    for x in actors:
        try: x.destroy()
        except Exception: pass
    world.apply_settings(orig); tm.set_synchronous_mode(False)

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

    # ---- 블랙아이스 패치 -------------------------------------------------
    wp_ice = waypoint_ahead(cmap, ego, a.patch_ahead)
    patch = spawn_ice_patch(world, wp_ice, length_m=a.patch_len, width_m=7.0, friction=a.friction)
    actors.append(patch.actor)
    print(f"[sim] ice patch at {wp_ice.transform.location} friction={a.friction} len={a.patch_len}m")

    # ---- 센서 -----------------------------------------------------------
    cbp = bl.find("sensor.camera.rgb")
    cbp.set_attribute("image_size_x", "640"); cbp.set_attribute("image_size_y", "480")
    cbp.set_attribute("fov", "90"); cbp.set_attribute("sensor_tick", "0.0")
    cam = world.spawn_actor(cbp, carla.Transform(carla.Location(x=1.4, z=1.5), carla.Rotation(pitch=-12.0)), attach_to=ego)
    actors.append(cam)
    ibp = bl.find("sensor.other.imu"); ibp.set_attribute("sensor_tick", "0.0")
    imu = world.spawn_actor(ibp, carla.Transform(), attach_to=ego); actors.append(imu)

    frames, imus = deque(maxlen=2), deque(maxlen=8)
    cam.listen(lambda im: frames.append(im))
    imu.listen(lambda m: imus.append(m))

    # ---- 1차/2차 판정기 --------------------------------------------------
    det = None if (a.disable_primary or a.gt_detect) else RoadNetDetector(a.model)
    ctx = build_context(WeatherObs(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                        LocationCtx(feature="bridge", hour=5 if not a.night else 23))
    fuser = RiskFuser(ctx.weights, threshold=ctx.threshold)
    slip = SlipDetector(rate_hz=a.fps, confirm_samples=3, min_speed_mps=a.min_detect_kph / 3.6)
    print(f"[ctx] prior={ctx.risk_prior} threshold={ctx.threshold} weights=({ctx.weights.alpha},{ctx.weights.beta},{ctx.weights.gamma})")

    for _ in range(int(0.5 * a.fps)):      # 스폰 낙하 충격 소산
        world.tick()
    ego.set_autopilot(True, 8000)
    tm.ignore_lights_percentage(ego, 100); tm.auto_lane_change(ego, False)
    tm.vehicle_percentage_speed_difference(ego, (30.0 - a.target_kph) / 30.0 * 100.0)

    tag = "gt" if a.gt_detect else ("secondary_only" if a.disable_primary else "primary")
    vw = None
    events, rows = [], []
    state = "DRIVE"           # DRIVE → WARN → BRAKE → STOPPED / SLIP → EMERG
    t_sim = 0.0; t_warn = None; t_slip = None; entered = False
    for step in range(a.max_steps):
        world.tick(); t_sim += dt
        v = ego.get_velocity(); spd = math.sqrt(v.x**2 + v.y**2 + v.z**2)
        loc = ego.get_location()
        dist = loc.distance(patch.location)
        inside = patch.contains(loc, margin=1.0)
        if inside and not entered:
            entered = True; events.append({"t": round(t_sim,2), "event": "patch_enter", "speed_kph": round(spd*3.6,1)})
            print(f"[{t_sim:6.2f}s] 빙판 진입  속도 {spd*3.6:.1f} km/h")

        # ---------- 1차 방어 ----------
        risk = 0.0; p = np.zeros(4)
        if state in ("DRIVE", "WARN") and not a.disable_primary:
            if a.gt_detect:
                # 정답 기반: 패치 앞 detect_range 안에 들어오면 감지 (도메인갭 우회 데모)
                ahead = dist - patch.extent[0]
                risk = 1.0 if 0 < ahead <= a.detect_range else 0.0
                fired = risk > 0
            elif frames:
                im = frames[-1]
                rgb = np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, :3][:, :, ::-1]
                p = det.infer(np.ascontiguousarray(rgb))
                r = fuser.fuse(p.tolist(), spec=0.0, lane=1.0)   # 반사도/차선 헤드는 미학습 → 중립값
                risk = r.risk; fired = r.alarm
            else:
                fired = False
            if fired and state == "DRIVE":
                state = "WARN"; t_warn = t_sim
                events.append({"t": round(t_sim,2), "event": "primary_warning", "risk": round(float(risk),3),
                               "dist_to_patch_m": round(dist,1), "speed_kph": round(spd*3.6,1)})
                print(f"[{t_sim:6.2f}s] 1차 경고  위험도 {risk:.2f}  패치까지 {dist:.1f}m  속도 {spd*3.6:.1f} km/h")

        # ---------- 2차 방어 ----------
        if imus and t_sim >= a.warmup:
            m = imus[-1]
            tr = ego.get_transform()
            roll = math.radians(tr.rotation.roll)
            ay = m.accelerometer.y - G * math.sin(roll)   # 경사/롤로 새어든 중력 성분 제거
            gz = m.gyroscope.z
            ctrl = ego.get_control()
            ev = slip.step(t_sim, ay, gz, spd, ctrl.steer)
            if ev and state not in ("EMERG",):
                t_slip = t_sim; state = "EMERG"
                cmd = emergency_command(ev)
                events.append({"t": round(t_sim,2), "event": "secondary_slip", "trigger": ev.trigger,
                               "ay_g": round(ev.ay_g,3), "yaw_err": round(ev.yaw_err,3), "speed_kph": round(spd*3.6,1)})
                print(f"[{t_sim:6.2f}s] 2차 미끄러짐 감지 ({ev.trigger}) ay={ev.ay_g:.2f}g yaw_err={ev.yaw_err:.2f} → 비상 제어")
                ego.set_autopilot(False, 8000)
                ego.apply_control(carla.VehicleControl(throttle=0.0, brake=cmd["brake"], steer=cmd["steer"]))

        # ---------- 상태별 제어 ----------
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
            ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.8, steer=ego.get_control().steer * 0.5))
            if spd < 0.3:
                state = "STOPPED"
                events.append({"t": round(t_sim,2), "event": "stabilized", "slip_to_stop_ms": round((t_sim - t_slip)*1000,1)})
                print(f"[{t_sim:6.2f}s] 비상 제어로 정지")

        rows.append({"t": round(t_sim,3), "speed_kph": round(spd*3.6,2), "dist_m": round(dist,2),
                     "risk": round(float(risk),3), "state": state, "inside": bool(inside),
                     "p": [round(float(z),3) for z in p]})

        # ---------- 영상 ----------
        if not a.no_video and frames:
            im = frames[-1]
            bgr = np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, :3].copy()
            if vw is None:
                vw = cv2.VideoWriter(str(out / f"demo_{tag}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (im.width, im.height))
            col = {"DRIVE": (0,255,0), "WARN": (0,220,255), "BRAKE": (0,140,255), "EMERG": (0,0,255), "STOPPED": (200,200,200)}[state]
            cv2.putText(bgr, f"{t_sim:5.2f}s {spd*3.6:5.1f}km/h  patch {dist:5.1f}m  risk {risk:.2f}  {state}",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            if det is not None:
                h, wd = bgr.shape[:2]
                cv2.rectangle(bgr, (int(wd*det.roi_left), int(h*det.roi_top)), (int(wd*det.roi_right), int(h*det.roi_bottom)), (255,255,0), 1)
                cv2.putText(bgr, "  ".join(f"{c[:4]} {v:.2f}" for c, v in zip(ROAD_CLASSES, p)), (10, h-14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,0), 1)
            if inside: cv2.putText(bgr, "ICE", (im.width-90, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,0,255), 2)
            vw.write(bgr)

        if state == "STOPPED" and t_sim > 1.0 and spd < 0.2:
            if len([e for e in events if e["event"] in ("stopped","stabilized")]) and t_sim - events[-1]["t"] > 1.5:
                break
    else:
        events.append({"t": round(t_sim,2), "event": "timeout"})

    if vw: vw.release()
    (out / f"events_{tag}.json").write_text(json.dumps({"args": vars(a), "events": events}, indent=1))
    (out / f"trace_{tag}.json").write_text(json.dumps(rows))
    print("\n=== 요약 ===")
    for e in events: print(" ", e)
    print(f"저장: {out}")
finally:
    cleanup()
