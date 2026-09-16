#!/usr/bin/env python3
"""CARLA 자동 라벨링 데이터셋 생성.

자율주행으로 맵을 돌며 프레임마다:
  1) RGB + semantic segmentation 촬영
  2) 자아 차선 전방에 놓인 빙판 패치를 카메라에 투영해 마스크 생성
  3) ice_render로 블랙아이스/젖음 외형을 합성 (도메인 랜덤화)
  4) 모델 입력 ROI를 잘라 224x224로 저장, 파일명에 라벨 기록
라벨: normal / wet / black_ice   (pothole은 CARLA에서 재현이 어려워 제외)

파일명: <seq>_<town>_<weather>_<class>.jpg
"""
import argparse, json, math, os, random, sys, time
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, cv2, carla
from icepredict.sim.blackice import spawn_ice_patch, waypoint_ahead, camera_intrinsics, patch_road_polygon, world_to_image
from icepredict.sim.ice_render import composite_ice, composite_wet, random_ice_params, road_mask

W, H, FOV = 640, 480, 90.0
ROI = (0.62, 0.97, 0.30, 0.70)      # top, bottom, left, right  (carla_demo와 동일)

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/dataset/carla"))
ap.add_argument("--target", type=int, default=10000, help="목표 이미지 수")
ap.add_argument("--towns", default="Town04,Town06,Town03")
ap.add_argument("--episode-steps", type=int, default=400)
ap.add_argument("--fps", type=int, default=20)
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2000)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--save-full", action="store_true", help="ROI 대신 전체 프레임도 저장")
a = ap.parse_args()

rng = np.random.default_rng(a.seed); random.seed(a.seed)
out = Path(a.out); (out / "images").mkdir(parents=True, exist_ok=True)
dt = 1.0 / a.fps
client = carla.Client(a.host, a.port); client.set_timeout(120.0)
K = camera_intrinsics(W, H, FOV)

WEATHERS = {
    "day_clear":  dict(cloudiness=15, precipitation=0, precipitation_deposits=0,  wetness=0,  sun_altitude_angle=60, fog_density=2),
    "day_cloudy": dict(cloudiness=80, precipitation=0, precipitation_deposits=10, wetness=15, sun_altitude_angle=35, fog_density=8),
    "dawn":       dict(cloudiness=60, precipitation=0, precipitation_deposits=20, wetness=25, sun_altitude_angle=8,  fog_density=15),
    "dusk":       dict(cloudiness=50, precipitation=0, precipitation_deposits=15, wetness=20, sun_altitude_angle=3,  fog_density=12),
    "night":      dict(cloudiness=70, precipitation=0, precipitation_deposits=25, wetness=30, sun_altitude_angle=-20, fog_density=10),
    "rain":       dict(cloudiness=95, precipitation=70, precipitation_deposits=70, wetness=80, sun_altitude_angle=25, fog_density=20),
}

def roi_crop(img):
    t, b, l, r = ROI
    return img[int(H * t):int(H * b), int(W * l):int(W * r)]

def patch_mask(cam, poly):
    left, right = poly
    pl = world_to_image(left, cam, K); pr = world_to_image(right, cam, K)
    pts = [p for p in pl if p] + [p for p in reversed(pr) if p]
    m = np.zeros((H, W), np.uint8)
    if len(pts) >= 3:
        cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
    return m > 0

saved = 0
meta = []
towns = [t.strip() for t in a.towns.split(",")]
t_start = time.time()
ep = 0
while saved < a.target:
    town = towns[ep % len(towns)]
    wname = list(WEATHERS)[ep % len(WEATHERS)]
    ep += 1
    world = client.get_world()
    if town not in world.get_map().name:
        world = client.load_world(town)
    cmap = world.get_map(); bl = world.get_blueprint_library()
    orig = world.get_settings()
    st = world.get_settings(); st.synchronous_mode = True; st.fixed_delta_seconds = dt
    world.apply_settings(st)
    tm = client.get_trafficmanager(8000); tm.set_synchronous_mode(True)
    wx = carla.WeatherParameters(**WEATHERS[wname]); world.set_weather(wx)
    night = WEATHERS[wname]["sun_altitude_angle"] < 5
    actors = []
    try:
        sp = random.choice(cmap.get_spawn_points())
        ego = world.try_spawn_actor(bl.filter("vehicle.tesla.model3")[0], sp)
        if ego is None:
            continue
        actors.append(ego)
        if night:
            ego.set_light_state(carla.VehicleLightState(carla.VehicleLightState.LowBeam | carla.VehicleLightState.Position))
        world.tick()
        cbp = bl.find("sensor.camera.rgb"); sbp = bl.find("sensor.camera.semantic_segmentation")
        for b in (cbp, sbp):
            b.set_attribute("image_size_x", str(W)); b.set_attribute("image_size_y", str(H)); b.set_attribute("fov", str(FOV))
        ctf = carla.Transform(carla.Location(x=1.4, z=1.5), carla.Rotation(pitch=-12))
        cam = world.spawn_actor(cbp, ctf, attach_to=ego); sem = world.spawn_actor(sbp, ctf, attach_to=ego)
        actors += [cam, sem]
        cq, sq = deque(maxlen=1), deque(maxlen=1)
        cam.listen(lambda i: cq.append(i)); sem.listen(lambda i: sq.append(i))
        ego.set_autopilot(True, 8000); tm.ignore_lights_percentage(ego, 80); tm.auto_lane_change(ego, False)
        for _ in range(int(1.5 * a.fps)): world.tick()

        patch, poly, remain = None, None, 0
        for step in range(a.episode_steps):
            world.tick()
            if not cq or not sq: continue
            # 주기적으로 앞쪽에 새 패치를 배치 (없거나 지나쳤으면)
            if patch is None or remain <= 0:
                if patch is not None and patch.actor is not None:
                    try:
                        if patch.actor.is_alive: patch.actor.destroy()
                    except Exception: pass
                    if patch.actor in actors: actors.remove(patch.actor)
                    patch = None
                try:
                    wp = waypoint_ahead(cmap, ego, float(rng.uniform(25, 70)))
                    patch = spawn_ice_patch(world, wp, length_m=float(rng.uniform(20, 60)), width_m=7.0, friction=0.02)
                    actors.append(patch.actor)
                    poly = patch_road_polygon(cmap, patch)
                    remain = int(rng.integers(40, 90))
                except Exception:
                    patch, poly = None, None
            remain -= 1

            im, sm = cq[-1], sq[-1]
            bgr = np.frombuffer(im.raw_data, np.uint8).reshape(H, W, 4)[:, :, :3].copy()
            seg = np.frombuffer(sm.raw_data, np.uint8).reshape(H, W, 4)[:, :, 2].copy()
            roi_road = road_mask(seg)[int(H*ROI[0]):int(H*ROI[1]), int(W*ROI[2]):int(W*ROI[3])]
            if roi_road.mean() < 0.55:      # ROI가 도로로 충분히 차지 않으면 버림 (교차로/연석)
                continue

            mask = patch_mask(cam, poly) if poly else np.zeros((H, W), bool)
            cover = (mask & road_mask(seg))[int(H*ROI[0]):int(H*ROI[1]), int(W*ROI[2]):int(W*ROI[3])].mean()

            u = rng.random()
            if cover > 0.45:
                cls = "black_ice"
                img = composite_ice(bgr, seg, mask, rng=rng, **random_ice_params(rng, night))
            elif u < 0.30:
                cls = "wet"
                full = np.ones((H, W), bool)
                img = composite_wet(bgr, seg, full, rng=rng)
            elif cover > 0.05:
                continue                    # 경계 애매 구간은 버림 (라벨 노이즈 방지)
            else:
                cls = "normal"
                img = bgr

            patch_img = cv2.resize(roi_crop(img), (224, 224), interpolation=cv2.INTER_AREA)
            name = f"{saved:06d}_{town}_{wname}_{cls}.jpg"
            cv2.imwrite(str(out / "images" / name), patch_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            meta.append({"file": name, "cls": cls, "town": town, "weather": wname, "cover": round(float(cover), 3)})
            saved += 1
            if saved % 500 == 0:
                el = time.time() - t_start
                print(f"[{saved}/{a.target}] {el/60:.1f}분 경과, {saved/max(el,1):.1f} img/s, 현재 {town}/{wname}", flush=True)
            if saved >= a.target: break
    finally:
        for s_ in (locals().get("cam"), locals().get("sem")):
            try:
                if s_ is not None and s_.is_listening: s_.stop()
            except Exception: pass
        for x in actors:
            try:
                if x is not None and x.is_alive: x.destroy()
            except Exception: pass
        actors.clear(); patch = None; poly = None
        try: world.apply_settings(orig)
        except Exception: pass
        try: tm.set_synchronous_mode(False)
        except Exception: pass

(out / "meta.json").write_text(json.dumps(meta, indent=0))
from collections import Counter
print("\n=== 수집 완료 ===")
print("총", saved, "장", f"{(time.time()-t_start)/60:.1f}분")
print("클래스:", dict(Counter(m["cls"] for m in meta)))
print("날씨:", dict(Counter(m["weather"] for m in meta)))
print("맵:", dict(Counter(m["town"] for m in meta)))
