#!/usr/bin/env python3
"""IcePredict 현장 시연 — Moza 휠·페달로 사람이 운전하고, 2차 방어(마찰 인식 제동 시점)가 라즈베리파이 격리 코어에서 돈다.

장면 (carla_envelope.py 와 같다): Town04 평탄 직선, 결빙 취약 구간(지도) → 블랙아이스(실효 μ ≈ 0.82 × friction) → 정지 차량.
정지 차량은 마찰 트리거 밖에 둔다 (CARLA: 트리거 안에 다른 차가 있으면 자차가 어디서든 그 마찰을 받는다 — 20번 문서).

두 모드를 D 키로 바꾼다 — 같은 격리 코어, 같은 C 코드, 마찰 값만 다르다.
  2차 방어 ON  : 결빙 취약 구간·카메라 애매 → 노면 확인 탭 → 잰 μ 로 정지 거리 → 경고·가속 제한·필요 시 자동 제동
  2차 방어 OFF : 마른 노면 μ 고정 (노면을 모르는 긴급제동의 가정)
운전은 사람 몫이다. 시스템은 조향을 건드리지 않고 경고·가속 제한·제동만 돕는다 (브레이크 = 운전자와 시스템 중 센 쪽).

키: C 휠 영점  D 2차 방어 켜기/끄기  A 크루즈(60 km/h) 켜기/끄기  R 장면 다시 시작  K 파이 메인 프로그램 죽이기
    V 시점 바꾸기(운전석/추적)  I 빙판 구역 보이기  ESC 끝
자동 녹화: --auto (크루즈 + 차선 유지, 사람 입력 없음) --record out.mp4 --defense on|off --kill-at 초

데스크톱→파이 방향이 막힌 망(캠퍼스)에서는 파이가 먼저 인사 패킷을 보낸다:
  파이:   sudo safety_island --udp 5600 --bind 0.0.0.0 --hello <데스크톱IP>:5700   (pi_demo_up.sh)
  파이:   python3 -m icepredict.pi.pi_main --island 127.0.0.1:5600 --hello <데스크톱IP>:5701
  데스크톱: python3 moza_demo.py --island listen:5700 --pi-main listen:5701
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import socket
import struct
import sys
import threading
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sw/src"))
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
import carla  # noqa: E402
import pygame  # noqa: E402
from icepredict.sim.blackice import (spawn_ice_patch, follow_lane, patch_road_polygon, camera_intrinsics,  # noqa: E402
                                     draw_patch_overlay)

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="localhost"); ap.add_argument("--port", type=int, default=2000)
ap.add_argument("--island", default="listen:5700", help="2차 방어 코어 주소 HOST:PORT, listen:PORT(코어의 인사 기다림), local(이 PC 의 ctypes — 시험용)")
ap.add_argument("--pi-main", default="listen:5701", help="파이 메인 프로그램 대역의 인사 포트 (K 키로 죽인다). none 이면 안 씀")
ap.add_argument("--auto", action="store_true", help="사람 입력 없이 크루즈 + 차선 유지 (영상 녹화용)")
ap.add_argument("--defense", choices=["on", "off"], default="on")
ap.add_argument("--friction", type=float, default=0.2); ap.add_argument("--target-kph", type=float, default=60.0)
ap.add_argument("--temp", type=float, default=-3.0)
ap.add_argument("--patch-ahead", type=float, default=170.0); ap.add_argument("--obstacle-in", type=float, default=150.0)
ap.add_argument("--zone-before", type=float, default=40.0); ap.add_argument("--spawn-rank", type=int, default=0)
ap.add_argument("--camera-risk", action="store_true", help="1차 방어 모델로 위험도를 낸다 (onnx 모델 필요)")
ap.add_argument("--model", default=os.path.expanduser("~/icepredict_demo/models/roadnet.onnx"))
ap.add_argument("--size", default="1600x900"); ap.add_argument("--fullscreen", action="store_true")
ap.add_argument("--record", default=None, help="화면을 mp4 로 녹화"); ap.add_argument("--kill-at", type=float, default=None,
                                                                         help="--auto 에서 이 시각(초)에 파이 메인 프로그램을 죽인다")
ap.add_argument("--max-time", type=float, default=60.0, help="--auto 에서 장면 길이")
ap.add_argument("--show-ice", action="store_true", help="처음부터 빙판 구역을 그린다 (I 키)")
ap.add_argument("--view", choices=["driver", "chase"], default="driver")
ap.add_argument("--log-dir", default=os.path.expanduser("~/icepredict_demo/logs"))
a = ap.parse_args()
W, H = (int(x) for x in a.size.split("x")); PANEL = 420; VW = W - PANEL
DT = 0.02; MU_DRY = 0.6725
FONT_PATH = next((p for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf") if os.path.exists(p)), None)


# ---------------------------------------------------------------- 입력: Moza 휠·페달
class PedalReader:
    """SRP 페달 /dev/input/js0 raw 이벤트 (pygame 이 R3 만 열고 페달을 무시한다). 축 0 가속, 1 브레이크."""
    def __init__(self, dev="/dev/input/js0"):
        self.v = {}; self.ok = os.path.exists(dev)
        if self.ok:
            self.f = open(dev, "rb"); threading.Thread(target=self._loop, daemon=True).start()
    def _loop(self):
        while True:
            d = self.f.read(8)
            if len(d) == 8:
                _, val, typ, num = struct.unpack("IhBB", d)
                if typ & 0x02: self.v[num] = val
    def axis(self, i):
        return max(0.0, min(1.0, (self.v.get(i, -32767) + 32767) / 65534))


class WheelFFB:
    """Moza R3 포스피드백 (리눅스 hid-universal-pidff): 중앙 복귀 스프링 + 짧은 진동. 실패하면 조용히 끈다."""
    def __init__(self):
        self.dev = None
        try:
            import evdev
            from evdev import ecodes, ff
            self.e, self.ff = ecodes, ff
            for p in evdev.list_devices():
                d = evdev.InputDevice(p)
                if "R3" in d.name: self.dev = d; break
            if self.dev:
                self.dev.write(ecodes.EV_FF, ecodes.FF_GAIN, 0xC000)
                cond = ff.Condition(right_saturation=0x7FFF, left_saturation=0x7FFF, right_coeff=0x3000, left_coeff=0x3000, deadband=0, center=0)
                eff = ff.Effect(ecodes.FF_SPRING, -1, 0, ff.Trigger(0, 0), ff.Replay(0xFFFF, 0), ff.EffectType(ff_condition_effect=(cond, cond)))
                self.spring = self.dev.upload_effect(eff); self.dev.write(ecodes.EV_FF, self.spring, 1)
        except Exception as ex:
            print(f"[ffb] 포스피드백 끔: {ex}", flush=True); self.dev = None
    def buzz(self, ms=500, mag=0x5000, period=60):
        if not self.dev: return
        try:
            e, ff = self.e, self.ff
            per = ff.Periodic(e.FF_SINE, period, mag, 0, 0, ff.Envelope(0, 0, 0, 0))
            eff = ff.Effect(e.FF_PERIODIC, -1, 0, ff.Trigger(0, 0), ff.Replay(ms, 0), ff.EffectType(ff_periodic_effect=per))
            eid = self.dev.upload_effect(eff); self.dev.write(e.EV_FF, eid, 1)
            threading.Timer(ms / 1000 + 0.2, lambda: self.dev.erase_effect(eid)).start()
        except Exception as ex:
            print(f"[ffb] 진동 실패: {ex}", flush=True)
    def close(self):
        try:
            if self.dev: self.dev.write(self.e.EV_FF, self.spring, 0); self.dev.erase_effect(self.spring)
        except Exception: pass


# ---------------------------------------------------------------- 2차 방어: 파이 격리 코어 (또는 시험용 로컬)
class LocalEnv:
    """시험용: 이 PC 에서 같은 C 코드를 ctypes 로. 화면에 'local' 로 표시한다 (시연에서는 쓰지 않는다)."""
    def __init__(self):
        from icepredict.pi.slip_ctypes import SlipCoreC
        self.c = SlipCoreC(DT, 3, 25 / 3.6, variant="env"); self.c.env_reset(MU_DRY); self.rtt_ms = [0.0]; self.n_timeout = 0
    def reset(self): self.c.env_reset(MU_DRY)
    def step(self, v, ax, ay, gap, v_lead, risk, temp_c, driver_brake, zone, dry_only):
        by = None
        if not dry_only: by, _ = self.c.env_should_probe(DT, risk, temp_c, v, ax, ay, driver_brake, gap, zone)
        o = self.c.env_step(DT, v, ax, gap, v_lead); o.update(probe_by=by, degraded=False); return o


class PiMainLink:
    """파이 메인 프로그램 대역(icepredict.pi.pi_main)의 인사를 받고, K 키에 KILL 을 보낸다."""
    def __init__(self, addr):
        self.dst = None; self.s = None
        if addr == "none": return
        host, port = addr.rsplit(":", 1); self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if host == "listen": self.s.bind(("0.0.0.0", int(port))); self.s.setblocking(False)
        else: self.dst = (host, int(port))
    def poll(self):
        if not self.s: return
        try:
            while True:
                b, src = self.s.recvfrom(64)
                if b[:4] == b"HELO": self.dst = src
        except (BlockingIOError, OSError): pass
    def kill(self):
        if self.s and self.dst: self.s.sendto(b"KILL", self.dst); return True
        return False


# ---------------------------------------------------------------- CARLA 장면
client = carla.Client(a.host, a.port); client.set_timeout(60.0)
world = client.get_world()
if "Town04" not in world.get_map().name: world = client.load_world("Town04"); time.sleep(2)
cmap = world.get_map(); bl = world.get_blueprint_library(); orig = world.get_settings()


def straight_spawn(need, rank):
    cands = []
    for sp in cmap.get_spawn_points():
        wp0 = cmap.get_waypoint(sp.location, project_to_road=True); y0 = wp0.transform.rotation.yaw; z0 = wp0.transform.location.z
        cur, mv, dy, dz, ok = wp0, 0.0, 0.0, 0.0, True
        while mv < need:
            nx = cur.next(5.0)
            if not nx: ok = False; break
            cur = nx[0]; mv += 5.0
            dy = max(dy, abs((cur.transform.rotation.yaw - y0 + 180) % 360 - 180)); dz = max(dz, abs(cur.transform.location.z - z0))
        if ok: cands.append((dy + 10 * dz, sp))
    cands.sort(key=lambda c: c[0]); picked = []
    for c in cands:
        if all(c[1].location.distance(p[1].location) > 50 for p in picked): picked.append(c)
    return picked[min(rank, len(picked) - 1)][1]


class Scene:
    def __init__(self):
        self.actors = []; self.build()

    def build(self):
        for x in world.get_actors():
            if x.type_id.startswith(("static.trigger", "vehicle.", "sensor.")):
                try: x.destroy()
                except Exception: pass
        s = world.get_settings(); s.synchronous_mode = True; s.fixed_delta_seconds = DT; world.apply_settings(s)
        world.set_weather(carla.WeatherParameters.ClearNoon)
        self.sp = straight_spawn(a.patch_ahead + a.obstacle_in + 40, a.spawn_rank)
        self.ego = world.try_spawn_actor(bl.find("vehicle.tesla.model3"), self.sp); self.actors.append(self.ego); world.tick()
        self.steer_gain = float(self.ego.get_physics_control().wheels[0].max_steer_angle) / 35.0
        wp0 = cmap.get_waypoint(self.ego.get_location(), project_to_road=True)
        self.entry = follow_lane(wp0, a.patch_ahead)
        plen = a.obstacle_in - 2.5                                     # 정지 차량은 트리거 밖
        self.patch = spawn_ice_patch(world, follow_lane(wp0, a.patch_ahead + plen / 2), length_m=plen, width_m=30.0, friction=a.friction)
        self.actors.append(self.patch.actor)
        ot = follow_lane(self.entry, a.obstacle_in).transform
        self.obst = world.try_spawn_actor(bl.find("vehicle.audi.a2"), carla.Transform(ot.location + carla.Location(z=0.3), ot.rotation))
        self.actors.append(self.obst); world.tick(); self.obst.apply_control(carla.VehicleControl(brake=1.0, hand_brake=True))
        self.imu = {"ax": 0.0, "ay": 0.0}
        im = world.spawn_actor(bl.find("sensor.other.imu"), carla.Transform(), attach_to=self.ego); self.actors.append(im)
        im.listen(lambda m: self.imu.update(ax=m.accelerometer.x, ay=m.accelerometer.y))
        self.hit = []
        co = world.spawn_actor(bl.find("sensor.other.collision"), carla.Transform(), attach_to=self.ego); self.actors.append(co)
        co.listen(lambda e: self.hit.append(e.other_actor.type_id))
        self.frame = {}; self.cams = {}
        self.K = {n: camera_intrinsics(VW, H, 90 if n == "driver" else 85) for n in ("driver", "chase")}
        self.use_view(state["view"])
        self.ice_poly = patch_road_polygon(cmap, self.patch, half_width_m=7.0)
        self.risk = -1.0; self.det = None
        if a.camera_risk and os.path.exists(a.model): self._init_camera_risk()
        for _ in range(5): world.tick()

    CAM_TF = {"driver": (carla.Location(x=0.3, y=-0.35, z=1.25), carla.Rotation()),
              "chase": (carla.Location(x=-11.0, z=6.0), carla.Rotation(pitch=-15))}

    def use_view(self, name):
        """보는 시점의 카메라 하나만 띄운다 (두 개를 같이 그리면 사람이 운전할 때 실시간보다 느려진다)."""
        for n, c in list(self.cams.items()):
            if n != name:
                c.stop(); c.destroy(); self.actors.remove(c); del self.cams[n]; self.frame.pop(n, None)
        if name not in self.cams:
            cb = bl.find("sensor.camera.rgb"); cb.set_attribute("image_size_x", str(VW)); cb.set_attribute("image_size_y", str(H))
            cb.set_attribute("fov", "90" if name == "driver" else "85")
            loc, rot = self.CAM_TF[name]
            c = world.spawn_actor(cb, carla.Transform(loc, rot), attach_to=self.ego); self.actors.append(c); self.cams[name] = c
            c.listen(lambda img, n=name: self.frame.__setitem__(n, img))

    def _init_camera_risk(self):
        from icepredict.sim import camera as camcfg
        from icepredict.sim.blackice import RoadNetDetector
        from icepredict.pi.fusion import RiskFuser
        from icepredict.pi.context import WeatherObs, LocationCtx, build_context
        self.camcfg = camcfg; self.rgb1 = [None]
        c = world.spawn_actor(camcfg.apply_camera_bp(bl.find("sensor.camera.rgb")), camcfg.camera_transform(carla), attach_to=self.ego)
        self.actors.append(c); c.listen(lambda im: self.rgb1.__setitem__(0, im))
        self.det = RoadNetDetector(a.model)
        ctx = build_context(WeatherObs(temp_c=a.temp, humidity=88.0, temp_trend_c_per_h=-1.0), LocationCtx(feature="bridge", hour=13))
        self.fuser = RiskFuser(ctx.weights, threshold=ctx.threshold)

    def camera_risk(self):
        if self.det is None or self.rgb1[0] is None: return -1.0
        im = self.rgb1[0]; v = np.frombuffer(im.raw_data, np.uint8).reshape(im.height, im.width, 4)[:, :, 2::-1]
        p = self.det.infer(np.ascontiguousarray(v)); return float(self.fuser.fuse(p.tolist(), spec=None, lane=None).risk)

    def destroy(self):
        for x in self.actors[::-1]:
            try: x.destroy()
            except Exception: pass
        self.actors = []


# ---------------------------------------------------------------- 화면
pygame.init()
screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN if a.fullscreen else 0)
pygame.display.set_caption("IcePredict — 2차 방어 시연")
F = lambda sz: pygame.font.Font(FONT_PATH, sz) if FONT_PATH else pygame.font.SysFont(None, sz)
f14, f18, f22, f30, f44 = F(14), F(18), F(22), F(30), F(44)
try:
    pygame.mixer.init(22050, -16, 1)
    t_ = np.arange(int(22050 * 0.25)) / 22050
    BEEP = pygame.sndarray.make_sound((np.sin(2 * np.pi * 1200 * t_) * 12000).astype(np.int16))
except Exception:
    BEEP = None

wheel = None
if not a.auto:
    pygame.joystick.init()
    for i in range(pygame.joystick.get_count()):
        j = pygame.joystick.Joystick(i); j.init()
        if "R3" in j.get_name() or wheel is None: wheel = j
pedals = PedalReader() if not a.auto else None
ffb = WheelFFB() if not a.auto else None
print(f"[demo] 휠 {wheel.get_name() if wheel else '없음'}, 페달 {'있음' if pedals and pedals.ok else '없음'}, "
      f"포스피드백 {'켬' if ffb and ffb.dev else '끔'}", flush=True)

if a.island == "local":
    env = LocalEnv(); island_name = "local (시험용 — 이 PC)"
else:
    from icepredict.pi.slip_island import EnvelopeIsland
    print(f"[demo] 2차 방어 코어 연결 대기 {a.island} ...", flush=True)
    env = EnvelopeIsland(a.island, timeout_s=0.04, wait_hello_s=60.0); island_name = "Raspberry Pi 격리 코어 3 (실시간 · 안전 전용 RTOS 대체)"
    print(f"[demo] 2차 방어 코어 연결: {env.dst}", flush=True)
pimain = PiMainLink(a.pi_main)
rec = None
if a.record:
    import cv2
    rec = cv2.VideoWriter(a.record, cv2.VideoWriter_fourcc(*"mp4v"), 25, (W, H))
os.makedirs(a.log_dir, exist_ok=True)

RT = [1.0]                  # 시뮬레이션 시간 / 실제 시간 (1 = 실시간)
state = dict(defense=a.defense == "on", cruise=a.auto, view=a.view, show_ice=a.show_ice, center=0.0, killed=False)


def run_scene():
    sc = Scene(); env.reset()
    t = 0.0; prev_v = 0.0; ev = {"defense": state["defense"], "auto": a.auto, "events": []}; banner = None; banner_t = 0.0
    mu_ice_alerted = False; result = None; t_end = None; last_mode = None; island_lost = 0; last_o = None
    tag = time.strftime("%m%d_%H%M%S") + ("_on" if state["defense"] else "_off") + ("_auto" if a.auto else "_live")
    logf = open(os.path.join(a.log_dir, f"demo_{tag}.csv"), "w"); logf.write("t,v,gap,steer,thr,brk_drv,brk_sys,mode,mu,d_need,risk,zone,inside,degraded\n")
    clock = time.perf_counter()
    while True:
        # --- 입력 이벤트
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE): return "quit", sc, ev, logf
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_r: return "restart", sc, ev, logf
                if e.key == pygame.K_d: state["defense"] = not state["defense"]; return "restart", sc, ev, logf
                if e.key == pygame.K_a: state["cruise"] = not state["cruise"]
                if e.key == pygame.K_v: state["view"] = "chase" if state["view"] == "driver" else "driver"; sc.use_view(state["view"])
                if e.key == pygame.K_i: state["show_ice"] = not state["show_ice"]
                if e.key == pygame.K_c and wheel: state["center"] = wheel.get_axis(0); print(f"[demo] 휠 영점 {state['center']:+.2f}")
                if e.key == pygame.K_k:
                    ok = pimain.kill(); state["killed"] = True; ev["events"].append({"t": round(t, 2), "event": "pi_main_kill", "sent": ok})
                    print(f"[{t:6.2f}s] 파이 메인 프로그램 KILL {'보냄' if ok else '(연결 없음)'}", flush=True)
        pimain.poll()
        if a.auto and a.kill_at is not None and t >= a.kill_at and not state["killed"]:
            ok = pimain.kill(); state["killed"] = True; ev["events"].append({"t": round(t, 2), "event": "pi_main_kill", "sent": ok})
            print(f"[{t:6.2f}s] 파이 메인 프로그램 KILL {'보냄' if ok else '(연결 없음)'}", flush=True)
        world.tick(); t += DT
        ego = sc.ego; tf = ego.get_transform(); vel = ego.get_velocity(); v = math.hypot(vel.x, vel.y)
        y = math.radians(tf.rotation.yaw); ol = sc.obst.get_location(); dx, dy = ol.x - tf.location.x, ol.y - tf.location.y
        gap = dx * math.cos(y) + dy * math.sin(y) - (ego.bounding_box.extent.x + sc.obst.bounding_box.extent.x)
        s_prog = tf.location.distance(sc.sp.location)
        zone = a.patch_ahead - a.zone_before <= s_prog <= a.patch_ahead + a.obstacle_in
        inside = sc.patch.contains(tf.location)
        if sc.det is not None and int(t / DT) % 2 == 0: sc.risk = sc.camera_risk()
        # --- 운전자 입력
        wp = cmap.get_waypoint(tf.location, project_to_road=True); nx = wp.next(6.0); tg = (nx[0] if nx else wp).transform.location
        le = (math.atan2(tg.y - tf.location.y, tg.x - tf.location.x) - y + math.pi) % (2 * math.pi) - math.pi
        if a.auto or wheel is None:
            steer = max(-0.5, min(0.5, le / 0.61086524 / sc.steer_gain)); thr_d, brk_d = 0.0, 0.0
        else:
            r0 = wheel.get_axis(0) - state["center"]; steer = max(-1.0, min(1.0, math.tan(1.1 * r0) * 0.55))
            thr_d = pedals.axis(0) if pedals and pedals.ok else 0.0; brk_d = pedals.axis(1) if pedals and pedals.ok else 0.0
        thr_acc = max(0.0, min(0.8, 0.6 * (a.target_kph / 3.6 - v) + 0.25)) if state["cruise"] else 0.0
        if state["cruise"] and brk_d > 0.05: state["cruise"] = False                  # 운전자가 밟으면 크루즈 해제
        # --- 2차 방어 (파이 격리 코어)
        o = env.step(v, sc.imu["ax"], sc.imu["ay"], gap, 0.0, sc.risk, a.temp, brk_d, zone, not state["defense"])
        if o is None:                                  # 무응답: 0.2 s 까지는 마지막 명령 유지 (와이파이 지연), 넘으면 '응답 없음'
            island_lost += 1; ev["island_miss"] = ev.get("island_miss", 0) + 1
            if last_o is not None and island_lost <= 10: o = dict(last_o, probe_by=None)
            else: o = {"brake": 0.0, "thr_cap": 1.0, "mode": "NO-ISLAND", "mu": float("nan"), "d_need": 0.0, "d_stop": 0.0,
                       "probe_by": None, "degraded": True, "mu_probe": -1}
        else:
            island_lost = 0; last_o = o
        if o["mode"] != last_mode:
            ev["events"].append({"t": round(t, 2), "event": "mode", "mode": o["mode"], "gap": round(gap, 1), "kph": round(v * 3.6, 1), "mu": round(o["mu"], 3)})
            last_mode = o["mode"]
        if o.get("probe_by"):
            ev["events"].append({"t": round(t, 2), "event": "probe", "by": o["probe_by"], "gap": round(gap, 1), "inside_ice": bool(inside)})
            if ffb: ffb.buzz(ms=150, mag=0x7000, period=30)                          # 탭 = 실차에서는 몸으로 느끼는 '툭'
        if state["defense"] and not mu_ice_alerted and o["mu"] < 0.3 and o["mode"] != "PROBE":
            mu_ice_alerted = True; banner = True; banner_t = t
            ev["events"].append({"t": round(t, 2), "event": "ice_alert", "mu": round(o["mu"], 3), "gap": round(gap, 1)})
            if ffb: ffb.buzz(ms=700, mag=0x6000, period=70)
            if BEEP: BEEP.play(loops=2)
        # --- 운전자·크루즈·시스템 합치기 (조향은 사람/차선 유지, 브레이크는 센 쪽, 가속은 시스템 상한까지)
        thr_cmd = max(thr_d, thr_acc)
        if o["mode"] == "PREBRAKE" and state["cruise"]: thr = o["thr_cap"]
        elif o["mode"] in ("PREBRAKE", "BRAKE", "HOLD", "PROBE"): thr = 0.0
        else: thr = min(thr_cmd, o["thr_cap"])
        brk = max(brk_d, o["brake"])
        if sc.hit: thr, brk = 0.0, 1.0
        ego.apply_control(carla.VehicleControl(throttle=float(thr), brake=float(brk), steer=float(steer)))
        logf.write(f"{t:.2f},{v:.3f},{gap:.2f},{steer:.3f},{thr:.3f},{brk_d:.3f},{o['brake']:.3f},{o['mode']},{o['mu']:.4f},{o['d_need']:.2f},"
                   f"{sc.risk:.3f},{int(zone)},{int(inside)},{int(o['degraded'])}\n")
        if sc.hit and result is None:
            result = f"충돌  {prev_v * 3.6:.0f} km/h"; t_end = t + 3.0; ev["collision"] = {"t": round(t, 2), "pre_kph": round(prev_v * 3.6, 1)}
        if result is None and v < 0.05 and gap < 40 and t > 5:
            result = f"정지  앞차까지 {gap:.1f} m"; t_end = t + 3.0; ev["stopped"] = {"t": round(t, 2), "gap": round(gap, 2)}
        prev_v = v
        # --- 그리기
        img = sc.frame.get(state["view"])
        if img is not None:
            arr = np.frombuffer(img.raw_data, np.uint8).reshape(img.height, img.width, 4)[:, :, :3]
            if state["show_ice"]:
                arr = draw_patch_overlay(arr.copy(), sc.ice_poly, sc.cams[state["view"]], sc.K[state["view"]], color=(255, 215, 150), alpha=0.35,
                                         label=f"BLACK ICE mu {0.82 * a.friction:.2f}")
            screen.blit(pygame.surfarray.make_surface(arr[:, :, ::-1].swapaxes(0, 1)), (0, 0))
        draw_panel(o, v, gap, zone, inside, sc, island_name, result)
        if banner and t - banner_t < 6.0:              # 정지 거리는 매 틱 새 값 (탭이 끝난 틱의 값은 바뀌기 전 μ 로 계산돼 있다)
            s_ = f44.render(f"빙판 감지  μ {o['mu']:.2f}  —  정지에 {o['d_stop']:.0f} m 필요", True, (255, 255, 255)); bx = (VW - s_.get_width()) // 2
            pygame.draw.rect(screen, (190, 30, 30), (bx - 18, 30, s_.get_width() + 36, s_.get_height() + 16)); screen.blit(s_, (bx, 38))
        if state["killed"] and o.get("degraded"):
            s_ = f22.render("파이 메인 프로그램 정지 — 2차 방어는 격리 코어에서 계속 동작", True, (255, 230, 120))
            pygame.draw.rect(screen, (0, 0, 0), (10, H - 44, s_.get_width() + 20, 36)); screen.blit(s_, (20, H - 40))
        if result:
            s_ = f44.render(result, True, (255, 90, 90) if "충돌" in result else (110, 230, 110))
            pygame.draw.rect(screen, (0, 0, 0), (VW // 2 - s_.get_width() // 2 - 20, H // 2 - 40, s_.get_width() + 40, 80))
            screen.blit(s_, (VW // 2 - s_.get_width() // 2, H // 2 - 28))
        pygame.display.flip()
        if rec is not None and int(t / DT) % 2 == 0:
            rec.write(pygame.surfarray.array3d(screen).swapaxes(0, 1)[:, :, ::-1].copy())
        if a.auto and t > a.max_time: return "quit", sc, ev, logf
        if t_end is not None and t >= t_end: return ("quit" if a.auto else "hold"), sc, ev, logf
        # 실시간 맞추기 (동기 모드 50 Hz)
        if int(t / DT) % 50 == 0:
            now_ = time.perf_counter(); RT[0] = 1.0 / max(1e-3, (now_ - getattr(run_scene, "_t50", now_ - 1.0))); run_scene._t50 = now_
            ev.setdefault("rt", []).append(round(RT[0], 3))
        clock += DT; dly = clock - time.perf_counter()
        if dly > 0: time.sleep(dly)
        else: clock = time.perf_counter()


def draw_panel(o, v, gap, zone, inside, sc, island_name, result):
    x0 = VW; pygame.draw.rect(screen, (22, 22, 24), (x0, 0, PANEL, H)); y = 18
    def put(txt, sz=f22, col=(235, 235, 235), dy=34):
        nonlocal y; screen.blit(sz.render(txt, True, col), (x0 + 20, y)); y += dy
    put("2차 방어 " + ("ON — 마찰 인식" if state["defense"] else "OFF — 마른 노면 기준"), f22, (110, 190, 255) if state["defense"] else (170, 170, 170))
    put(f"판단 위치: {island_name}", f14, (150, 150, 150), 26)
    put(f"속도  {v * 3.6:5.1f} km/h", f30, dy=46)
    mu = o["mu"]; put(f"노면 마찰 μ  {mu:.2f}" + ("  (가정)" if not state["defense"] else ("  (측정)" if mu < 0.6 else "")), f22)
    mode_k = {"CRUISE": "순항", "PROBE": "노면 확인 탭", "CAUTION": "가속 금지", "PREBRAKE": "예비 제동", "BRAKE": "전력 제동",
              "HOLD": "정지 유지", "NO-ISLAND": "2차 방어 코어 응답 없음"}.get(o["mode"], o["mode"])
    mc = {"PROBE": (255, 210, 0), "CAUTION": (255, 200, 0), "PREBRAKE": (255, 150, 0), "BRAKE": (255, 70, 70), "HOLD": (110, 220, 110),
          "NO-ISLAND": (255, 60, 60)}.get(o["mode"], (210, 210, 210))
    put(mode_k, f44, mc, 60)
    sc_ = (PANEL - 40) / 200.0
    for label, val, col in (("앞 정지 차량까지", max(gap, 0), (200, 200, 200)),
                            ("시스템이 계산한 정지 거리", o["d_need"], (230, 80, 80) if o["d_need"] >= gap else (90, 200, 90))):
        put(f"{label}  {val:.0f} m", f18, (200, 200, 200), 24)
        pygame.draw.rect(screen, col, (x0 + 20, y, int(min(val, 200) * sc_), 18)); y += 32
    put(("결빙 취약 구간 (지도)" if zone else "") + ("  ·  빙판 위" if inside and state["show_ice"] else ""), f18, (255, 220, 120), 30)
    if sc.det is not None: put(f"1차 카메라 위험도  {sc.risk:.2f}", f18, (230, 140, 80), 30)
    rtt = env.rtt_ms[-1] if getattr(env, "rtt_ms", None) else 0.0
    put(f"2차 방어 코어 왕복 {rtt:4.1f} ms   무응답 {getattr(env, 'n_timeout', 0)}" + ("" if rec is not None else f"   실시간 {RT[0]:.2f}×"), f14, (150, 150, 150), 22)
    put(f"파이 메인 프로그램: {'정지' if o.get('degraded') else '동작'}", f18, (255, 120, 120) if o.get("degraded") else (150, 220, 150), 30)
    y = H - 120
    for k in ("C 영점  D 2차 방어  A 크루즈  R 다시", "K 파이 메인 죽이기  V 시점  I 빙판 표시"):
        put(k, f14, (130, 130, 130), 22)
    put("크루즈 " + ("ON" if state["cruise"] else "OFF"), f18, (180, 180, 180), 24)


try:
    while True:
        what, sc, ev, logf = run_scene()
        logf.close(); ev["result"] = "crash" if "collision" in ev else ("stopped" if "stopped" in ev else "none")
        (pathlib.Path(a.log_dir) / f"demo_{time.strftime('%m%d_%H%M%S')}.json").write_text(json.dumps(ev, indent=1, ensure_ascii=False))
        if what == "hold":                            # 결과 화면을 띄운 채 R(다시)/D(모드)/ESC 를 기다린다
            waiting = True
            while waiting:
                for e in pygame.event.get():
                    if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE): what = "quit"; waiting = False
                    if e.type == pygame.KEYDOWN and e.key in (pygame.K_r, pygame.K_d):
                        if e.key == pygame.K_d: state["defense"] = not state["defense"]
                        what = "restart"; waiting = False
                world.tick(); time.sleep(0.02)
        sc.destroy(); state["killed"] = False; state["cruise"] = a.auto
        if what == "quit": break
finally:
    if rec is not None: rec.release()
    if ffb: ffb.close()
    world.apply_settings(orig); pygame.quit()
