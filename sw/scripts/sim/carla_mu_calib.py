#!/usr/bin/env python3
"""CARLA 마찰 배수(--friction) → 실효 마찰 μ_eff 보정 실험 (게인 스케줄링 2단계).

CARLA 의 friction trigger 값은 물리 마찰계수가 아니라 PhysX 타이어 마찰 배수다. 이후 모든 축(마찰 추정·제어표·대리 모델)을
'바퀴가 실제로 낼 수 있는 가속도' 로 맞추려면 배수 → μ_eff 를 실측해야 한다.

직선 구간에 긴 마찰 트리거(기본 140 m)를 깔고, 그 앞에서 40 km/h 로 맞춘 뒤 들어가 시험 입력을 준다.
  brake_B   : 일정 제동 B (조향 0)                  → 평균 감속, 바퀴 잠김(B 를 올려도 감속이 줄어드는 지점)
  steer_S   : 계단 조향 S (제동·가속 0)              → 최대 횡가속
  pulse     : 보드 제어기와 같은 펄스 제동(0.70/0.35, 16샘플) + 사인 조향 → 대리 모델 보정용 동역학
  combo     : 제동 0.5 + 조향 0.2 (마찰원 결합)
μ_eff = max over trials of 0.1 s 평균 √(ax²+ay²) / g  (차체 좌표 가속도, 월드 가속도를 차체로 돌린 값)

매 틱 상태·입력을 CSV 로 남겨 4단계 대리 모델 보정에 그대로 쓴다.
사용: python carla_mu_calib.py --port 2000 --frictions 0.02,0.04 --out ~/icepredict/logs/gain_sched/mu_calib
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
import carla  # noqa: E402
import numpy as np  # noqa: E402

from icepredict.sim.blackice import follow_lane, spawn_ice_patch  # noqa: E402

G = 9.80665
ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=2000)
ap.add_argument("--town", default="Town04")
ap.add_argument("--frictions", default="0.02,0.04,0.06,0.08,0.12,0.2,0.3,0.5,1.0")
ap.add_argument("--trials", default="brake_0.3,brake_0.5,brake_0.7,brake_1.0,steer_0.1,steer_0.2,steer_0.35,pulse,combo")
ap.add_argument("--kph", type=float, default=40.0)
ap.add_argument("--approach", type=float, default=70.0, help="스폰 지점에서 마찰 구간 시작까지 (m)")
ap.add_argument("--patch-len", type=float, default=140.0)
ap.add_argument("--tire-friction", type=float, default=1.0, help="마찰 구간 밖 타이어 마찰 (데모 기본과 같다)")
ap.add_argument("--fps", type=int, default=50)
ap.add_argument("--max-trial-s", type=float, default=14.0)
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/logs/gain_sched/mu_calib"))
a = ap.parse_args()
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
dt = 1.0 / a.fps

client = carla.Client(a.host, a.port); client.set_timeout(60.0)
world = client.get_world()
if not world.get_map().name.endswith(a.town):
    world = client.load_world(a.town); time.sleep(2)
cmap = world.get_map()
orig = world.get_settings()
st = world.get_settings(); st.synchronous_mode = True; st.fixed_delta_seconds = dt
world.apply_settings(st)
bl = world.get_blueprint_library()
vbp = bl.filter("vehicle.tesla.model3")[0]


def wrap(a_):
    return (a_ + math.pi) % (2 * math.pi) - math.pi


def straightness(wp, dist):
    """wp 에서 dist 앞까지 차로를 따라가며 (방향 변화 최댓값 rad, 높이 변화 최댓값 m).
    빙판(μ≈0.02)에서는 2 % 경사의 중력(0.2 m/s²)이 마찰력보다 커서 제동해도 차가 가속한다 — 평탄해야 한다."""
    y0 = math.radians(wp.transform.rotation.yaw); z0 = wp.transform.location.z; worst = dz = 0.0; cur = wp; moved = 0.0
    while moved < dist:
        nxt = cur.next(5.0)
        if not nxt: return 9.0, 99.0
        cur = nxt[0]; moved += 5.0
        worst = max(worst, abs(wrap(math.radians(cur.transform.rotation.yaw) - y0)))
        dz = max(dz, abs(cur.transform.location.z - z0))
    return worst, dz


# 가장 곧은 긴 구간을 고른다 (접근 + 마찰 구간 + 여유 40 m). 결과가 시험마다 같아야 하므로 한 번 정해 고정한다.
need = a.approach + a.patch_len + 40.0
best = None
for sp in cmap.get_spawn_points():
    wp = cmap.get_waypoint(sp.location, project_to_road=True)
    yd, dz = straightness(wp, need)
    score = math.degrees(yd) + 10.0 * dz                      # 1° 방향 변화 ≈ 0.1 m 높이 변화로 같게 친다
    if best is None or score < best[0]: best = (score, sp, wp, yd, dz)
_, spawn_tf, spawn_wp, straight_dev, straight_dz = best
print(f"[calib] 직선 구간: 방향 변화 최대 {math.degrees(straight_dev):.2f}°, 높이 변화 최대 {straight_dz:.2f} m / {need:.0f} m,"
      f" 스폰 {spawn_tf.location}", flush=True)
patch_center = follow_lane(spawn_wp, a.approach + a.patch_len / 2)


def set_tire(v, mu):
    pc = v.get_physics_control()
    for wh in pc.wheels: wh.tire_friction = float(mu)
    v.apply_physics_control(pc)


def body(vec, yaw):
    """월드 벡터를 차체(x 앞, y 오른쪽 — CARLA 왼손 좌표) 로."""
    c, s = math.cos(yaw), math.sin(yaw)
    return vec.x * c + vec.y * s, -vec.x * s + vec.y * c


def lane_keep(ego):
    """접근 구간 차선 유지(순수 추종). 마찰 구간 안에서는 시험 입력만 준다."""
    tf = ego.get_transform(); loc = tf.location; yaw = math.radians(tf.rotation.yaw)
    wp = cmap.get_waypoint(loc, project_to_road=True); tgt = wp.next(8.0)[0].transform.location
    err = wrap(math.atan2(tgt.y - loc.y, tgt.x - loc.x) - yaw)
    return max(-0.3, min(0.3, 1.2 * err))


def run_trial(fr, name):
    ego = world.try_spawn_actor(vbp, spawn_tf)
    if ego is None: raise RuntimeError("스폰 실패")
    for _ in range(int(0.5 * a.fps)): world.tick()
    set_tire(ego, a.tire_friction)
    imu = world.spawn_actor(bl.find("sensor.other.imu"), carla.Transform(), attach_to=ego)
    last = {}; imu.listen(lambda m: last.__setitem__("m", m))
    col = world.spawn_actor(bl.find("sensor.other.collision"), carla.Transform(), attach_to=ego)
    hit = []; col.listen(lambda e: hit.append(e))
    rows = []; t = 0.0; t_in = None; n_in = 0
    kind, val = (name.split("_") + [None])[:2]
    val = float(val) if val else None
    v_tgt = a.kph / 3.6
    try:
        while t < 60.0:
            tf = ego.get_transform(); yaw = math.radians(tf.rotation.yaw)
            v = ego.get_velocity(); acc = ego.get_acceleration(); av = ego.get_angular_velocity()
            vx, vy = body(v, yaw); ax, ay = body(acc, yaw); spd = math.hypot(v.x, v.y)
            inside = patch.contains(tf.location)
            if inside and t_in is None: t_in = t
            thr = brk = st_ = 0.0
            if t_in is None:                                  # 접근: 속도 맞추고 차선 유지
                thr = max(0.0, min(0.8, 0.6 * (v_tgt - spd) + 0.25)); st_ = lane_keep(ego)
                if spd > v_tgt + 0.3: thr = 0.0; brk = 0.1
            else:
                k = n_in; n_in += 1; tt = k * dt
                if kind == "brake": brk = val
                elif kind == "steer": st_ = val if tt > 0.3 else 0.0
                elif kind == "pulse": brk = 0.7 if (k >> 3) & 1 else 0.35; st_ = 0.15 * math.sin(2 * math.pi * 0.5 * tt)
                elif kind == "combo": brk = 0.5; st_ = 0.2 if tt > 0.3 else 0.0
            ego.apply_control(carla.VehicleControl(throttle=thr, brake=brk, steer=st_))
            m = last.get("m")
            rows.append((t, tf.location.x, tf.location.y, yaw, vx, vy, math.radians(av.z), ax, ay,
                         m.accelerometer.x if m else 0.0, m.accelerometer.y if m else 0.0, m.gyroscope.z if m else 0.0,
                         st_, thr, brk, int(inside)))
            world.tick(); t += dt
            if t_in is not None:
                if spd < 0.5 or (t - t_in) > a.max_trial_s or hit: break
                if not inside and (t - t_in) > 1.0: break
            elif t > 30.0: break
    finally:
        imu.stop(); col.stop()
        for x in (imu, col, ego): x.destroy()
        world.tick()
    arr = np.array(rows, dtype=np.float64)
    hdr = "t,x,y,yaw,vx,vy,r,ax,ay,imu_ax,imu_ay,imu_gz,steer,throttle,brake,inside"
    np.savetxt(out / f"f{fr:g}_{name}.csv", arr, delimiter=",", header=hdr, comments="", fmt="%.5f")
    return arr, bool(hit)


summary = {"straight_dev_deg": math.degrees(straight_dev), "straight_dz_m": straight_dz, "kph": a.kph, "tire_friction_outside": a.tire_friction,
           "patch_len": a.patch_len, "fps": a.fps, "frictions": {}}
sp = out / "summary.json"
if sp.exists():
    summary = json.loads(sp.read_text()); summary.setdefault("frictions", {})
try:
    for fr in [float(x) for x in a.frictions.split(",")]:
        patch = spawn_ice_patch(world, patch_center, length_m=a.patch_len, width_m=14.0, friction=fr)
        world.tick()
        res = {}
        for name in a.trials.split(","):
            arr, hit = run_trial(fr, name)
            ins = arr[:, 15] > 0.5
            if ins.sum() < 10:
                res[name] = {"error": "마찰 구간에 들어가지 못함"}; continue
            a_tot = np.hypot(arr[ins, 7], arr[ins, 8])
            k = max(1, int(0.1 * a.fps)); a_s = np.convolve(a_tot, np.ones(k) / k, mode="valid")
            seg = arr[ins]; t_rel = seg[:, 0] - seg[0, 0]
            late = t_rel > 0.5                                       # 진입 직후 0.5 s 는 하중 이동 과도 구간
            res[name] = {"n": int(ins.sum()), "a_peak": float(a_s.max()), "mu_peak": float(a_s.max() / G),
                         "decel_mean": float(-seg[late, 7].mean()) if late.any() else None,
                         "ay_peak": float(np.abs(seg[:, 8]).max()), "v_in": float(math.hypot(seg[0, 4], seg[0, 5])),
                         "collision": hit}
            print(f"[calib] f={fr:g} {name:10s} μ_peak {res[name]['mu_peak']:.3f}  감속 {res[name]['decel_mean'] or 0:.2f} m/s²"
                  f"  진입 {res[name]['v_in']*3.6:.1f} km/h{'  충돌' if hit else ''}", flush=True)
        mu_eff = max(r["mu_peak"] for r in res.values() if "mu_peak" in r)
        summary["frictions"][f"{fr:g}"] = {"mu_eff": mu_eff, "trials": res}
        print(f"[calib] ===== friction {fr:g} → μ_eff {mu_eff:.3f}", flush=True)
        sp.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        patch.actor.destroy(); world.tick()
finally:
    world.apply_settings(orig)
print("[calib] 끝", flush=True)
