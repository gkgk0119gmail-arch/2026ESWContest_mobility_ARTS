#!/usr/bin/env python3
"""CARLA 자동 라벨링 데이터셋 생성 (고정 경로 주행).

에피소드마다:
  1) 스폰 지점에서 차선을 따라 경로를 미리 뽑고, 그 위에 빙판 패치를 여러 개 배치
  2) pure pursuit으로 경로를 따라 주행하며 RGB + semantic segmentation 촬영
  3) 패치를 카메라에 투영한 마스크로 블랙아이스/젖음 외형을 합성 (도메인 랜덤화)
  4) 모델 입력 ROI를 224x224로 잘라 저장, 파일명에 라벨 기록

Traffic Manager를 쓰지 않는 이유는 src/icepredict/sim/route.py 주석 참고.
라벨: normal / wet / black_ice  (pothole은 CARLA 재현이 어려워 제외)
파일명: <seq>_<town>_<weather>_<class>.jpg
"""
import argparse, json, math, os, random, sys, time
from collections import deque, Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np, cv2, carla
from icepredict.sim.blackice import spawn_ice_patch, camera_intrinsics, patch_road_polygon, world_to_image
from icepredict.sim.ice_render import composite_ice, random_ice_params, road_mask
from icepredict.sim.route import build_route, PurePursuit
from icepredict.sim import camera as camcfg

W, H, FOV = camcfg.WIDTH, camcfg.HEIGHT, camcfg.FOV
ROI = (camcfg.ROI_TOP, camcfg.ROI_BOTTOM, camcfg.ROI_LEFT, camcfg.ROI_RIGHT)

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/dataset/carla"))
ap.add_argument("--target", type=int, default=10000)
ap.add_argument("--towns", default="Town04,Town03,Town05")   # Town06은 이 빌드에 없음
ap.add_argument("--route-m", type=float, default=450.0)
ap.add_argument("--fps", type=int, default=20)
ap.add_argument("--kph", type=float, default=50.0)
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2000)
ap.add_argument("--seed", type=int, default=0)

a = ap.parse_args()

rng = np.random.default_rng(a.seed); random.seed(a.seed)
out = Path(a.out); (out / "images").mkdir(parents=True, exist_ok=True)
dt = 1.0 / a.fps
client = carla.Client(a.host, a.port); client.set_timeout(120.0)
K = camera_intrinsics(W, H, FOV)

# 건조(dry) / 젖음(wet) 그룹. 'wet' 라벨은 CARLA 엔진이 wetness·precipitation_deposits로
# 실제 렌더링한 노면만 사용한다. 합성으로 만든 젖음을 쓰면 모델이 우리 아티팩트를 배운다.
# 노출이 날아가지 않도록 태양 고도와 cloudiness를 보수적으로 잡았다.
WEATHERS = {
    "dry_noon":    ("dry", dict(cloudiness=25, precipitation=0,  precipitation_deposits=0,  wetness=0,  sun_altitude_angle=45, fog_density=3)),
    "dry_cloudy":  ("dry", dict(cloudiness=85, precipitation=0,  precipitation_deposits=0,  wetness=0,  sun_altitude_angle=30, fog_density=8)),
    "dry_dawn":    ("dry", dict(cloudiness=55, precipitation=0,  precipitation_deposits=0,  wetness=5,  sun_altitude_angle=10, fog_density=14)),
    "dry_night":   ("dry", dict(cloudiness=60, precipitation=0,  precipitation_deposits=0,  wetness=0,  sun_altitude_angle=-20, fog_density=8)),
    "wet_cloudy":  ("wet", dict(cloudiness=90, precipitation=0,  precipitation_deposits=60, wetness=70, sun_altitude_angle=30, fog_density=10)),
    "wet_rain":    ("wet", dict(cloudiness=95, precipitation=65, precipitation_deposits=80, wetness=90, sun_altitude_angle=25, fog_density=18)),
    "wet_dusk":    ("wet", dict(cloudiness=80, precipitation=20, precipitation_deposits=55, wetness=65, sun_altitude_angle=6,  fog_density=15)),
    "wet_night":   ("wet", dict(cloudiness=75, precipitation=0,  precipitation_deposits=65, wetness=75, sun_altitude_angle=-20, fog_density=10)),
}
WNAMES = list(WEATHERS)

def roi_crop(img):
    t, b, l, r = ROI
    return img[int(H * t):int(H * b), int(W * l):int(W * r)]

def poly_mask(cam, poly):
    left, right = poly
    pl = world_to_image(left, cam, K); pr = world_to_image(right, cam, K)
    pts = [p for p in pl if p] + [p for p in reversed(pr) if p]
    m = np.zeros((H, W), np.uint8)
    if len(pts) >= 3:
        cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
    return m > 0

def teardown(world, orig, sensors, actors):
    """센서 콜백을 먼저 끊고 한 틱 흘린 뒤 일괄 파괴. 순서를 지키지 않으면
    CARLA 클라이언트가 'destroyed actor'로 죽는다."""
    for s in sensors:
        try:
            if s is not None and s.is_listening: s.stop()
        except Exception: pass
    try: world.tick()
    except Exception: pass
    ids = [x for x in actors if x is not None]
    try:
        client.apply_batch_sync([carla.command.DestroyActor(x) for x in ids], True)
    except Exception:
        for x in ids:
            try: x.destroy()
            except Exception: pass
    try: world.tick()
    except Exception: pass
    try: world.apply_settings(orig)
    except Exception: pass

CLASSES = ("normal", "wet", "black_ice")
cap = {c: int(a.target / len(CLASSES) * 1.15) for c in CLASSES}   # 약간의 여유
count = {c: 0 for c in CLASSES}
saved, meta, ep = 0, [], 0
towns = [t.strip() for t in a.towns.split(",")]
t_start = time.time()
cur_town = None
world = client.get_world()

while saved < a.target and not all(count[c] >= cap[c] for c in CLASSES):
    town = towns[ep % len(towns)]
    wname = WNAMES[int(rng.integers(len(WNAMES)))]
    ep += 1
    if cur_town != town:
        world = client.load_world(town); cur_town = town
    cmap = world.get_map(); bl = world.get_blueprint_library()
    orig = world.get_settings()
    st = world.get_settings(); st.synchronous_mode = True; st.fixed_delta_seconds = dt
    world.apply_settings(st)
    surface, wparams = WEATHERS[wname]
    world.set_weather(carla.WeatherParameters(**wparams))
    night = wparams["sun_altitude_angle"] < 5

    sensors, actors = [], []
    try:
        sp = random.choice(cmap.get_spawn_points())
        ego = world.try_spawn_actor(bl.filter("vehicle.tesla.model3")[0], sp)
        if ego is None:
            continue
        actors.append(ego)
        if night:
            ego.set_light_state(carla.VehicleLightState(carla.VehicleLightState.LowBeam | carla.VehicleLightState.Position))
        world.tick()

        route = build_route(cmap, cmap.get_waypoint(sp.location, project_to_road=True), a.route_m, rng=rng)
        if len(route) < 60:
            teardown(world, orig, sensors, actors); continue

        # 경로 위 25% 지점 이후로 빙판 패치 배치 (가속 구간 확보)
        patches = []
        idx = int(len(route) * 0.25)
        while idx < len(route) - 30:
            wp = route[idx]
            length = float(rng.uniform(25, 55))
            try:
                p = spawn_ice_patch(world, wp, length_m=length, width_m=7.0, friction=0.02)
                patches.append((p, patch_road_polygon(cmap, p)))
                actors.append(p.actor)
            except Exception:
                pass
            idx += int(rng.integers(45, 90))      # 90~180m 간격
        if not patches:
            teardown(world, orig, sensors, actors); continue

        cbp = camcfg.apply_camera_bp(bl.find("sensor.camera.rgb"))
        sbp = camcfg.apply_camera_bp(bl.find("sensor.camera.semantic_segmentation"), exposure=None)
        ctf = camcfg.camera_transform(carla)
        cam = world.spawn_actor(cbp, ctf, attach_to=ego); sem = world.spawn_actor(sbp, ctf, attach_to=ego)
        sensors += [cam, sem]; actors += [cam, sem]
        cq, sq = deque(maxlen=1), deque(maxlen=1)
        cam.listen(cq.append); sem.listen(sq.append)

        ctrl = PurePursuit(route, target_kph=a.kph)
        for _ in range(int(1.0 * a.fps)):
            ego.apply_control(carla.VehicleControl(throttle=0.6)); world.tick()

        ep_saved = 0
        while not ctrl.done() and saved < a.target:
            c, prog = ctrl.update(ego)
            ego.apply_control(c)
            world.tick()
            if not cq or not sq:
                continue

            im, sm = cq[-1], sq[-1]
            bgr = np.frombuffer(im.raw_data, np.uint8).reshape(H, W, 4)[:, :, :3].copy()
            seg = np.frombuffer(sm.raw_data, np.uint8).reshape(H, W, 4)[:, :, 2].copy()
            rm = road_mask(seg)
            t, b_, l, r = ROI
            ys, ye, xs, xe = int(H*t), int(H*b_), int(W*l), int(W*r)
            if rm[ys:ye, xs:xe].mean() < 0.70:      # ROI가 도로로 충분히 차지 않으면 버림
                continue

            loc = ego.get_location()
            near = min(patches, key=lambda pp: loc.distance(pp[0].location))
            mask = poly_mask(cam, near[1])
            cover = float((mask & rm)[ys:ye, xs:xe].mean())

            if cover > 0.45:
                cls = "black_ice"
                img = composite_ice(bgr, seg, mask, rng=rng, **random_ice_params(rng, night))
            elif cover > 0.05:
                continue                             # 경계 애매 구간은 라벨 노이즈라 버림
            else:
                cls = "wet" if surface == "wet" else "normal"
                img = bgr                            # 엔진 렌더링 그대로

            if count[cls] >= cap[cls]:
                continue                             # 클래스 균형 유지
            crop = cv2.resize(roi_crop(img), (224, 224), interpolation=cv2.INTER_AREA)
            name = f"{saved:06d}_{town}_{wname}_{cls}.jpg"
            cv2.imwrite(str(out / "images" / name), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            meta.append({"file": name, "cls": cls, "town": town, "weather": wname, "surface": surface, "cover": round(cover, 3)})
            saved += 1; ep_saved += 1; count[cls] += 1
            if saved % 500 == 0:
                el = time.time() - t_start
                eta = (a.target - saved) / max(saved / el, 1e-6) / 60
                print(f"[{saved}/{a.target}] {el/60:.1f}분 {saved/el:.0f}장/s  남은 {eta:.0f}분  {dict(count)}", flush=True)
        print(f"  ep{ep} {town}/{wname}: {ep_saved}장", flush=True)
    finally:
        teardown(world, orig, sensors, actors)

(out / "meta.json").write_text(json.dumps(meta, indent=0))
print("\n=== 수집 완료 ===")
print(f"총 {saved}장  {(time.time()-t_start)/60:.1f}분")
print("클래스:", dict(Counter(m["cls"] for m in meta)))
print("날씨 :", dict(Counter(m["weather"] for m in meta)))
print("맵   :", dict(Counter(m["town"] for m in meta)))
