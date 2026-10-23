#!/usr/bin/env python3
"""CARLA 0.9.16 마찰 트리거 결함 측정 — 빙판(트리거)을 벗어나면 타이어 마찰이 어떻게 되나.

54 km/h 에서 조향 0.6 계단을 주고 0.4~0.8 s 평균 횡가속을 잰다 (타이어가 포화하는 입력이라 마찰 차이가 바로 보인다).
  none         : 트리거 없음. 데모처럼 spawn 직후 apply_physics_control 로 tire_friction 1.0 을 준다
  dry_trigger  : friction 1.0 트리거 안
  after_ice    : 빙판(0.05) 트리거를 빠져나온 뒤
결과(2026-10-23): none 19.6 · dry_trigger 5.3 · after_ice 19.9 m/s² — tire_friction 설정은 효과가 없고,
트리거를 벗어나면 기본 3.5 (횡 약 2 g) 로 돌아간다. **다른 작업이 안 쓰는 전용 서버**에 붙여야 한다.
사용: python carla_friction_probe.py --port 2030 --out friction_probe.json
"""
import argparse, json, math, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
import carla  # noqa: E402
from icepredict.sim.blackice import spawn_ice_patch, follow_lane  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=2030); ap.add_argument("--out", required=True)
a = ap.parse_args()
c = carla.Client("127.0.0.1", a.port); c.set_timeout(60); w = c.get_world()
if not w.get_map().name.endswith("Town04"): w = c.load_world("Town04")
st = w.get_settings(); orig = w.get_settings(); st.synchronous_mode = True; st.fixed_delta_seconds = 0.02; w.apply_settings(st)
m = w.get_map(); bp = w.get_blueprint_library().filter("vehicle.tesla.model3")[0]
sp = None
for s in m.get_spawn_points():
    wp = m.get_waypoint(s.location); ok = True; cur = wp
    for _ in range(50):
        n = cur.next(5.0)
        if not n or abs(n[0].transform.location.z - wp.transform.location.z) > 0.3: ok = False; break
        cur = n[0]
    if ok: sp = s; break
W0 = m.get_waypoint(sp.location)

def lat(v):
    y = math.radians(v.get_transform().rotation.yaw); acc = v.get_acceleration(); return abs(-acc.x * math.sin(y) + acc.y * math.cos(y))

def run(case):
    v = w.spawn_actor(bp, sp); [w.tick() for _ in range(20)]
    pc = v.get_physics_control()
    for wh in pc.wheels: wh.tire_friction = 1.0                  # 데모가 하는 것과 같다
    v.apply_physics_control(pc); w.tick()
    trig, ice, state = [], None, "before"
    if case == "dry_trigger": trig.append(spawn_ice_patch(w, follow_lane(W0, 120.0), length_m=200.0, width_m=20.0, friction=1.0))
    if case == "after_ice": ice = spawn_ice_patch(w, follow_lane(W0, 60.0), length_m=20.0, width_m=12.0, friction=0.05)
    w.tick(); k0 = None; acc = []
    for k in range(3000):
        loc = v.get_location(); spd = math.hypot(v.get_velocity().x, v.get_velocity().y)
        if ice is not None:
            ins = ice.contains(loc)
            if state == "before" and ins: state = "in"
            if state == "in" and not ins: state = "after"
        ready = (case == "none" and spd > 15) or (case == "dry_trigger" and spd > 15 and trig[0].contains(loc, margin=-3)) \
            or (case == "after_ice" and state == "after" and not ice.contains(loc, margin=8.0) and spd > 1)
        if k0 is None and ready: k0 = k
        if k0 is not None:
            v.apply_control(carla.VehicleControl(steer=0.6))
            if 20 <= k - k0 <= 40: acc.append(lat(v))
            if k - k0 > 40: break
        else:
            v.apply_control(carla.VehicleControl(throttle=0.6 if spd < 15.5 else 0.0))
        w.tick()
    v.destroy()
    for t in trig + ([ice] if ice else []): t.actor.destroy()
    w.tick()
    return sum(acc) / max(len(acc), 1)

res = {case: round(run(case), 2) for case in ("none", "dry_trigger", "after_ice")}
w.apply_settings(orig)
pathlib.Path(a.out).write_text(json.dumps({"metric": "조향 0.6 계단 0.4~0.8 s 평균 횡가속 (m/s², 54 km/h)", "results": res}, ensure_ascii=False, indent=1))
print(res)
