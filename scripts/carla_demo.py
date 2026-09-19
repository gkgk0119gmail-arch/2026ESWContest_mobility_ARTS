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
import argparse, json, math, os, sys, time
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, cv2, carla

from icepredict.common.protocol import ROAD_CLASSES
from icepredict.pi.fusion import RiskFuser
from icepredict.pi.context import WeatherObs, LocationCtx, build_context
from icepredict.pi.imu_slip import SlipDetector, emergency_command, G
from icepredict.sim.blackice import (spawn_ice_patch, waypoint_ahead, RoadNetDetector,
                                     camera_intrinsics, patch_road_polygon, draw_patch_overlay,
                                     patch_image_mask)
from icepredict.sim.ice_render import composite_ice, random_ice_params
from icepredict.sim import camera as camcfg

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
ap.add_argument("--no-ice-render", action="store_true", help="카메라 프레임에 얼음 합성 안 함")
ap.add_argument("--ice-seed", type=int, default=7, help="얼음 외관 파라미터 시드 (프레임마다 고정)")
# 융합을 STM32N6에서 돌린다. 학교망이 유선→무선을 차단하므로 데스크탑이 bind하고
# Pi 브리지(scripts/n6_bridge.py)가 connect한다 — 방향을 뒤집어야 연결이 된다.
ap.add_argument("--fusion", choices=["local", "n6", "n6npu"], default="local",
                help="local=이 프로세스에서 융합, n6=보드에서 융합(추론은 여기), n6npu=보드 NPU가 추론+융합 (ROI 이미지를 보낸다)")
ap.add_argument("--n6-bind", default="tcp://*:5558", help="Pi 브리지가 접속할 주소")
ap.add_argument("--n6-timeout", type=float, default=3.0, help="브리지 응답 대기(초)")
ap.add_argument("--no-video", action="store_true")
ap.add_argument("--no-overlay", action="store_true", help="빙판 시각화 오버레이 끄기")
ap.add_argument("--debug-box", action="store_true", help="시뮬 안에 디버그 박스도 그리기")
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
    for x in actors:                      # 콜백을 먼저 끊는다 (destroyed actor 크래시 예방)
        try:
            if hasattr(x, "is_listening") and x.is_listening: x.stop()
        except Exception: pass
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
    poly = patch_road_polygon(cmap, patch)

    # ---- 센서 -----------------------------------------------------------
    cbp = camcfg.apply_camera_bp(bl.find("sensor.camera.rgb")); cbp.set_attribute("sensor_tick", "0.0")
    cam = world.spawn_actor(cbp, camcfg.camera_transform(carla), attach_to=ego)
    actors.append(cam)
    # 얼음 합성에는 도로/하늘 마스크가 필요하다 → 수집기와 같은 설정의 시맨틱 카메라
    sem = None
    if not a.no_ice_render:
        sbp = camcfg.apply_camera_bp(bl.find("sensor.camera.semantic_segmentation"), exposure=None)
        sbp.set_attribute("sensor_tick", "0.0")
        sem = world.spawn_actor(sbp, camcfg.camera_transform(carla), attach_to=ego)
        actors.append(sem)
    ibp = bl.find("sensor.other.imu"); ibp.set_attribute("sensor_tick", "0.0")
    imu = world.spawn_actor(ibp, carla.Transform(), attach_to=ego); actors.append(imu)

    K = camera_intrinsics(camcfg.WIDTH, camcfg.HEIGHT, camcfg.FOV)
    frames, sems, imus = deque(maxlen=2), deque(maxlen=2), deque(maxlen=8)
    cam.listen(lambda im: frames.append(im))
    if sem is not None:
        sem.listen(lambda im: sems.append(im))
    imu.listen(lambda m: imus.append(m))

    # 얼음 외관은 주행 내내 같아야 한다 (프레임마다 재추첨하면 화면이 깜빡이고,
    # 모델 입력도 프레임마다 달라져 위험도 추정이 흔들린다)
    ice_rng = np.random.default_rng(a.ice_seed)
    ice_params = random_ice_params(ice_rng, night=a.night)
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
    ctx = build_context(WeatherObs(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                        LocationCtx(feature="bridge", hour=5 if not a.night else 23))
    fuser = RiskFuser(ctx.weights, threshold=ctx.threshold)

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
                      "beta": ctx.weights.beta, "gamma": ctx.weights.gamma})
        n6.recv_json()
        print(f"[n6] ctx 전송: threshold={ctx.threshold} weights=({ctx.weights.alpha},"
              f"{ctx.weights.beta},{ctx.weights.gamma})", flush=True)
    slip = SlipDetector(rate_hz=a.fps, confirm_samples=3, min_speed_mps=a.min_detect_kph / 3.6)
    print(f"[ctx] prior={ctx.risk_prior} threshold={ctx.threshold} weights=({ctx.weights.alpha},{ctx.weights.beta},{ctx.weights.gamma})")

    for _ in range(int(0.5 * a.fps)):      # 스폰 낙하 충격 소산
        world.tick()
    ego.set_autopilot(True, 8000)
    tm.ignore_lights_percentage(ego, 100); tm.auto_lane_change(ego, False)
    tm.vehicle_percentage_speed_difference(ego, (30.0 - a.target_kph) / 30.0 * 100.0)

    tag = "gt" if a.gt_detect else ("secondary_only" if a.disable_primary else "primary")
    if not a.no_ice_render and not a.gt_detect:
        tag += "_icerender"
    if a.fusion == "n6":
        tag += "_n6"
    if a.fusion == "n6npu":
        tag += "_n6npu"
    vw = None
    events, rows = [], []
    state = "DRIVE"           # DRIVE → WARN → BRAKE → STOPPED / SLIP → EMERG
    t_sim = 0.0; t_warn = None; t_slip = None; entered = False; warn_info = None
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
                if mask.any():
                    view = composite_ice(view, seg, mask, rng=np.random.default_rng(a.ice_seed), **ice_params)

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
                    n6_stats["board"] += 1; n6_stats["board_ns_sum"] += int(vd["infer_us"]) * 1000
                else:
                    n6_stats["fallback"] += 1; fired = False   # 보드 무응답: 이 프레임은 판정 없음 (로컬 모델이 없다)
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
            if fired and state == "DRIVE":
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
        if not a.no_video and view is not None:
            bgr = view.copy()
            if vw is None:
                vw = cv2.VideoWriter(str(out / f"demo_{tag}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), a.fps,
                                     (bgr.shape[1], bgr.shape[0]))
            if not a.no_overlay:
                icecol = (255, 200, 90) if not inside else (100, 100, 255)
                oa = 0.45 if a.no_ice_render else 0.10   # 합성 시엔 노면을 덮지 않게 옅게
                bgr = draw_patch_overlay(bgr, poly, cam, K, color=icecol, alpha=oa,
                                         label=f"BLACK ICE  {dist:.0f}m" if dist > 3 else "BLACK ICE")
            col = {"DRIVE": (0,255,0), "WARN": (0,220,255), "BRAKE": (0,140,255), "EMERG": (0,0,255), "STOPPED": (200,200,200)}[state]
            cv2.putText(bgr, f"{t_sim:5.2f}s {spd*3.6:5.1f}km/h  patch {dist:5.1f}m  risk {risk:.2f}  {state}",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            if det is not None:
                h, wd = bgr.shape[:2]
                cv2.rectangle(bgr, (int(wd*det.roi_left), int(h*det.roi_top)), (int(wd*det.roi_right), int(h*det.roi_bottom)), (255,255,0), 1)
                cv2.putText(bgr, "  ".join(f"{c[:4]} {v:.2f}" for c, v in zip(ROAD_CLASSES, p)), (10, h-14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,0), 1)
            if warn_info is not None:      # 경고 발화 시점의 근거를 계속 띄워둔다
                cv2.rectangle(bgr, (0, 38), (bgr.shape[1], 74), (0, 0, 150), -1)
                cv2.putText(bgr, warn_info, (10, 63), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255,255,255), 1, cv2.LINE_AA)
            if inside: cv2.putText(bgr, "ICE", (bgr.shape[1]-90, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,0,255), 2)
            vw.write(bgr)

        if state == "STOPPED" and t_sim > 1.0 and spd < 0.2:
            if len([e for e in events if e["event"] in ("stopped","stabilized")]) and t_sim - events[-1]["t"] > 1.5:
                break
    else:
        events.append({"t": round(t_sim,2), "event": "timeout"})

    if vw: vw.release()
    (out / f"events_{tag}.json").write_text(json.dumps({"args": vars(a), "events": events}, indent=1))
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
        try:
            n6.send_json({"kind": "bye"}); n6.recv_json()
        except Exception:
            pass

    print("\n=== 요약 ===")
    for e in events: print(" ", e)
    print(f"저장: {out}")
finally:
    cleanup()
