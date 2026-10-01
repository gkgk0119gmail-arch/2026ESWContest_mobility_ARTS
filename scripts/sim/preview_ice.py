#!/usr/bin/env python3
"""블랙아이스 합성 미리보기: CARLA에서 RGB+semseg 한 프레임 받아 원본/합성 비교 이미지 저장."""
import sys, os, math
from pathlib import Path
from collections import deque
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import numpy as np, cv2, carla
from icepredict.sim.blackice import spawn_ice_patch, waypoint_ahead, camera_intrinsics, patch_road_polygon, world_to_image
from icepredict.sim.ice_render import composite_ice, composite_wet, random_ice_params, road_mask

W, H, FOV = 640, 480, 90.0
c = carla.Client("127.0.0.1", 2000); c.set_timeout(60.0)
w = c.get_world()
if "Town04" not in w.get_map().name: w = c.load_world("Town04")
cm = w.get_map(); bl = w.get_blueprint_library()
orig = w.get_settings(); st = w.get_settings(); st.synchronous_mode = True; st.fixed_delta_seconds = 0.05
w.apply_settings(st)
wx = w.get_weather(); wx.cloudiness = 70; wx.precipitation_deposits = 20; wx.wetness = 30; wx.sun_altitude_angle = 25
w.set_weather(wx)
actors = []
try:
    sp = cm.get_spawn_points()[0]
    ego = w.try_spawn_actor(bl.filter("vehicle.tesla.model3")[0], sp); actors.append(ego); w.tick()
    cbp = bl.find("sensor.camera.rgb"); cbp.set_attribute("image_size_x", str(W)); cbp.set_attribute("image_size_y", str(H)); cbp.set_attribute("fov", str(FOV))
    sbp = bl.find("sensor.camera.semantic_segmentation"); sbp.set_attribute("image_size_x", str(W)); sbp.set_attribute("image_size_y", str(H)); sbp.set_attribute("fov", str(FOV))
    tf = carla.Transform(carla.Location(x=1.4, z=1.5), carla.Rotation(pitch=-12))
    cam = w.spawn_actor(cbp, tf, attach_to=ego); sem = w.spawn_actor(sbp, tf, attach_to=ego); actors += [cam, sem]
    cq, sq = deque(maxlen=1), deque(maxlen=1)
    cam.listen(lambda i: cq.append(i)); sem.listen(lambda i: sq.append(i))
    wp = waypoint_ahead(cm, ego, 18.0)
    patch = spawn_ice_patch(w, wp, length_m=45.0, width_m=7.0, friction=0.02); actors.append(patch.actor)
    poly = patch_road_polygon(cm, patch)
    for _ in range(12): w.tick()
    im, sm = cq[-1], sq[-1]
    bgr = np.frombuffer(im.raw_data, np.uint8).reshape(H, W, 4)[:, :, :3].copy()
    seg = np.frombuffer(sm.raw_data, np.uint8).reshape(H, W, 4)[:, :, 2].copy()   # BGRA의 R채널 = 태그
    print("semantic tags:", sorted(set(np.unique(seg).tolist()))[:12])
    print("road px:", int(road_mask(seg).sum()))
    K = camera_intrinsics(W, H, FOV)
    left, right = poly
    pl = world_to_image(left, cam, K); pr = world_to_image(right, cam, K)
    pts = [p for p in pl if p] + [p for p in reversed(pr) if p]
    mask = np.zeros((H, W), bool)
    if len(pts) >= 3:
        mm = np.zeros((H, W), np.uint8); cv2.fillPoly(mm, [np.array(pts, np.int32)], 255); mask = mm > 0
    print("patch px:", int(mask.sum()), " ice px:", int((mask & road_mask(seg)).sum()))
    rng = np.random.default_rng(0)
    tiles = [("original", bgr)]
    for i in range(3):
        tiles.append((f"ice#{i}", composite_ice(bgr, seg, mask, rng=rng, **random_ice_params(rng))))
    tiles.append(("wet", composite_wet(bgr, seg, mask, rng=rng)))
    seg_vis = cv2.applyColorMap((seg * 9 % 255).astype(np.uint8), cv2.COLORMAP_JET)
    tiles.append(("semantic", seg_vis))
    row1 = np.hstack([cv2.putText(t.copy(), n, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2) for n, t in tiles[:3]])
    row2 = np.hstack([cv2.putText(t.copy(), n, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2) for n, t in tiles[3:]])
    cv2.imwrite("/tmp/ice_preview.png", np.vstack([row1, row2]))
    # ROI(모델 입력) 크롭 비교
    def roi(x): return cv2.resize(x[int(H*0.62):int(H*0.97), int(W*0.30):int(W*0.70)], (224,224))
    cv2.imwrite("/tmp/ice_roi.png", np.hstack([roi(t) for _, t in tiles[:5]]))
    print("saved /tmp/ice_preview.png, /tmp/ice_roi.png")
finally:
    for x in actors:
        try: x.destroy()
        except Exception: pass
    w.apply_settings(orig)
