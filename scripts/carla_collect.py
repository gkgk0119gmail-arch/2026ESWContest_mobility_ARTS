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
from icepredict.sim.blackice import (spawn_ice_patch, camera_intrinsics, patch_road_polygon,
                                     world_to_image, patch_image_mask)
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
# 에피소드 워치독: pure pursuit은 차가 경로를 벗어나면 진행 인덱스가 멈추고
# done()이 영원히 False가 된다. 그러면 ROI가 도로가 아니라 모든 프레임이
# 버려지면서 수집이 무한정 헛돈다. 아래 세 조건 중 하나라도 걸리면 에피소드를 끊는다.
ap.add_argument("--episode-steps", type=int, default=1600)   # 에피소드 최대 틱 (20fps=80초)
ap.add_argument("--stall-ticks", type=int, default=120)      # 경로 진행 없이 버틸 틱 (6초)
ap.add_argument("--offroute-m", type=float, default=12.0)    # 경로점에서 이만큼 벌어지면 이탈
# 수집용 마찰. 빙판 '외관'은 composite_ice가 합성하므로 물리 마찰을 낮출 이유가 없다.
# 0.02로 두면 차가 스핀아웃해 경로를 이탈하고 프레임이 전부 버려진다.
# (데모 scripts/carla_demo.py는 실제 미끄러짐이 필요하므로 그대로 0.02를 쓴다.)
ap.add_argument("--ice-friction", type=float, default=0.9)
# 빙판 커버리지 채택 구간. v1은 0.45 미만을 전부 버려서 '마른 아스팔트 → 얼음' 경계가
# 데이터셋에 0장이었다. 50m 전방 경고가 목적이면 정작 필요한 건 멀리 경계가 보이는
# 프레임이다. 0.20~0.45를 edge, 0.45 이상을 full로 나눠 각각 절반씩 채운다.
ap.add_argument("--ice-cover-min", type=float, default=0.20)
ap.add_argument("--ice-cover-split", type=float, default=0.45)
# 야간 ROI가 거의 새까만 프레임은 배울 내용이 없다 (v1에서 normal의 28%가 밝기 25 미만).
ap.add_argument("--min-roi-brightness", type=float, default=22.0)
# 클래스만 모으고 수집을 끝낼지 (재수집용). 예) --only black_ice
ap.add_argument("--only", default="", help="이 클래스만 수집 (쉼표 구분). 빈 값=전체")
ap.add_argument("--start-seq", type=int, default=0, help="파일명 시작 번호 (기존 데이터에 덧붙일 때)")

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
    "dry_night":   ("dry", dict(cloudiness=60, precipitation=0,  precipitation_deposits=0,  wetness=0,  sun_altitude_angle=-10, fog_density=8)),
    "wet_cloudy":  ("wet", dict(cloudiness=90, precipitation=0,  precipitation_deposits=60, wetness=70, sun_altitude_angle=30, fog_density=10)),
    "wet_rain":    ("wet", dict(cloudiness=95, precipitation=65, precipitation_deposits=80, wetness=90, sun_altitude_angle=25, fog_density=18)),
    "wet_dusk":    ("wet", dict(cloudiness=80, precipitation=20, precipitation_deposits=55, wetness=65, sun_altitude_angle=6,  fog_density=15)),
    "wet_night":   ("wet", dict(cloudiness=75, precipitation=0,  precipitation_deposits=65, wetness=75, sun_altitude_angle=-10, fog_density=10)),
}
WNAMES = list(WEATHERS)
# 그룹별 목록. 날씨를 완전 무작위로 뽑으면 젖음이 연속으로 나와 normal이 한참 비어 있다
# (실제로 첫 두 에피소드가 모두 wet으로 뽑혀 normal 0장이었다). 에피소드마다 건조/젖음을
# 번갈아 고르고 그룹 안에서만 무작위로 뽑아 클래스가 고르게 차도록 한다.
DRY = [k for k, v in WEATHERS.items() if v[0] == "dry"]
WET = [k for k, v in WEATHERS.items() if v[0] == "wet"]

def roi_crop(img):
    t, b, l, r = ROI
    return img[int(H * t):int(H * b), int(W * l):int(W * r)]

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
ONLY = tuple(x.strip() for x in a.only.split(",") if x.strip()) or CLASSES
assert all(c in CLASSES for c in ONLY), f"--only: {CLASSES} 중에서 골라라"
cap = {c: (int(a.target / len(ONLY) * 1.15) if c in ONLY else 0) for c in CLASSES}
count = {c: 0 for c in CLASSES}
# 얼음은 '경계가 보이는' 프레임과 'ROI가 얼음으로 찬' 프레임을 절반씩 모은다.
icap = {"edge": cap["black_ice"] // 2, "full": cap["black_ice"] - cap["black_ice"] // 2}
icount = {"edge": 0, "full": 0}
saved, meta, ep = 0, [], 0
towns = [t.strip() for t in a.towns.split(",")]
t_start = time.time()
cur_town = None
world = client.get_world()

while saved < a.target and not all(count[c] >= cap[c] for c in ONLY):
    town = towns[ep % len(towns)]
    group = DRY if ep % 2 == 0 else WET
    wname = group[int(rng.integers(len(group)))]
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
                p = spawn_ice_patch(world, wp, length_m=length, width_m=7.0, friction=a.ice_friction)
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
        ticks, last_i, stalled, reason = 0, -1, 0, "route"
        while not ctrl.done() and saved < a.target:
            c, prog = ctrl.update(ego)
            ego.apply_control(c)
            world.tick()
            ticks += 1

            # --- 워치독 ---
            if ctrl.i > last_i:
                last_i, stalled = ctrl.i, 0
            else:
                stalled += 1
            if stalled >= a.stall_ticks:
                reason = f"정체 {stalled}틱"; break
            if ticks >= a.episode_steps:
                reason = f"틱 상한 {ticks}"; break
            if ego.get_location().distance(ctrl.route[ctrl.i].transform.location) > a.offroute_m:
                reason = "경로 이탈"; break

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
            mask = patch_image_mask(near[1], cam, K, (H, W))
            cover = float((mask & rm)[ys:ye, xs:xe].mean())

            sub = None
            if cover >= a.ice_cover_min:
                cls = "black_ice"
                sub = "full" if cover >= a.ice_cover_split else "edge"
                if icount[sub] >= icap[sub]:
                    continue                         # edge/full 한쪽만 쏠리지 않게
                img = composite_ice(bgr, seg, mask, rng=rng, **random_ice_params(rng, night))
            elif cover > 0.03:
                continue                             # 얼음이 살짝만 걸친 구간은 라벨 노이즈
            else:
                cls = "wet" if surface == "wet" else "normal"
                img = bgr                            # 엔진 렌더링 그대로

            if count[cls] >= cap[cls]:
                continue                             # 클래스 균형 유지
            crop = cv2.resize(roi_crop(img), (224, 224), interpolation=cv2.INTER_AREA)
            if cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean() < a.min_roi_brightness:
                continue                             # 야간 무내용 프레임 (배울 게 없다)
            name = f"{a.start_seq + saved:06d}_{town}_{wname}_{cls}.jpg"
            cv2.imwrite(str(out / "images" / name), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            meta.append({"file": name, "cls": cls, "town": town, "weather": wname, "surface": surface, "cover": round(cover, 3)})
            saved += 1; ep_saved += 1; count[cls] += 1
            if sub: icount[sub] += 1
            if saved % 500 == 0:
                el = time.time() - t_start
                eta = (a.target - saved) / max(saved / el, 1e-6) / 60
                print(f"[{saved}/{a.target}] {el/60:.1f}분 {saved/el:.0f}장/s  남은 {eta:.0f}분  {dict(count)} 얼음{dict(icount)}", flush=True)
        print(f"  ep{ep} {town}/{wname}: {ep_saved}장 ({ticks}틱, {reason})", flush=True)
    finally:
        teardown(world, orig, sensors, actors)

(out / "meta.json").write_text(json.dumps(meta, indent=0))
print("\n=== 수집 완료 ===")
print(f"총 {saved}장  {(time.time()-t_start)/60:.1f}분")
print("클래스:", dict(Counter(m["cls"] for m in meta)))
print("날씨 :", dict(Counter(m["weather"] for m in meta)))
print("맵   :", dict(Counter(m["town"] for m in meta)))
icov = [m["cover"] for m in meta if m["cls"] == "black_ice"]
if icov:
    import numpy as _np
    print(f"얼음 cover: 중앙 {_np.median(icov):.2f}  경계(<{a.ice_cover_split}) {sum(c < a.ice_cover_split for c in icov)}장  "
          f"충만 {sum(c >= a.ice_cover_split for c in icov)}장")
