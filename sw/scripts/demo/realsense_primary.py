#!/usr/bin/env python3
"""1차 방어 시연 — RealSense D435i 로 노면(빙판 사진)을 비추면 1차 모델이 실시간으로 판정한다.

입력: RealSense RGB (기본). pyrealsense2 가 있으면 그것으로, 없으면 리눅스 UVC 장치(/dev/video*)에서 RealSense RGB 를 찾는다.
      --source cam:N 으로 장치 번호를, --source 폴더/파일 로 사진·영상을 줄 수 있다 (카메라 없이 동작 확인용).
판정: RoadNet(노면 4분류, onnx) → 위험도 융합(RiskFuser, 기온 맥락) → 경고 문턱·연속 프레임 확정 — 실제 1차 방어와 같은 계산.
     카메라 화면 가운데 정사각형을 잘라 모델에 넣는다 (차량 카메라의 도로 ROI 대신, 손에 든 카메라로 사진을 비추는 시연용).
     +/− 키로 그 크기를 비친 사진에 맞춘다. 모델은 흐림에 약하다 — 사진을 크고 선명하게, 카메라는 가까이, 초점 확인.
화면: 카메라 영상 + 자른 영역 + 노면 분류 확률 + 위험도 + '블랙아이스 감지 → 감속 경고' 배너. --record 로 녹화.
"""
from __future__ import annotations

import argparse
import glob
import os
import pathlib
import sys
import socket
import struct
import time

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "sw/src"))
from icepredict.common.protocol import ROAD_CLASSES  # noqa: E402
from icepredict.pi.fusion import RiskFuser  # noqa: E402
from icepredict.pi.context import WeatherObs, LocationCtx, build_context  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--source", default="realsense", help="realsense | cam:N | 사진 폴더 | 사진·영상 파일")
ap.add_argument("--model", default=str(ROOT / "models/roadnet.onnx"))
ap.add_argument("--temp", type=float, default=-3.0, help="기온 맥락 °C (영하면 경고 문턱이 낮아진다)")
ap.add_argument("--crop", type=float, default=0.6, help="화면 가운데 정사각형 크기 (짧은 변 대비)")
ap.add_argument("--record", default=None); ap.add_argument("--headless", action="store_true")
ap.add_argument("--seconds", type=float, default=0, help="이 시간만 돌고 끝 (0 = 계속)")
ap.add_argument("--per-image", type=float, default=2.0, help="사진 폴더 입력일 때 장당 초")
ap.add_argument("--send", default=None, help="판정을 CARLA 시연(moza_demo --primary listen:PORT)으로 보낸다: HOST:PORT")
a = ap.parse_args()

KOR = {"normal": "마른 노면", "wet": "젖은 노면", "black_ice": "블랙아이스", "pothole": "포트홀"}
ICE = ROAD_CLASSES.index("black_ice")
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1); STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)


# ---------------------------------------------------------------- 입력
class Source:
    def __init__(self, spec):
        self.kind = None; self.imgs = []; self.cap = None; self.pipe = None
        if spec == "realsense":
            try:
                import pyrealsense2 as rs
                self.pipe = rs.pipeline(); cfg = rs.config(); cfg.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
                self.pipe.start(cfg); self.kind = "pyrealsense2"; return
            except Exception as ex:
                print(f"[src] pyrealsense2 안 됨 ({ex}) → UVC 장치에서 RealSense RGB 를 찾는다", flush=True)
            for dev in sorted(glob.glob("/sys/class/video4linux/video*")):
                name = open(os.path.join(dev, "name")).read().strip()
                if "RealSense" not in name: continue
                idx = int(dev.rsplit("video", 1)[1]); cap = cv2.VideoCapture(f"/dev/video{idx}", cv2.CAP_V4L2)   # 번호로는 못 여는 경우가 있다
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV")); cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                for _ in range(15):                                    # 켜자마자는 검은 프레임이 나온다
                    ok, fr = cap.read()
                    if ok and fr is not None and fr.ndim == 3 and fr[..., 0].std() > 1: break
                if ok and fr is not None and fr.ndim == 3 and fr[..., 0].std() > 1 and not np.allclose(fr[..., 0], fr[..., 1]):
                    self.cap = cap; self.kind = f"UVC /dev/video{idx} ({name})"; return
                cap.release()
            raise SystemExit("RealSense RGB 장치를 찾지 못했다. USB 3 포트에 꽂았는지, --source cam:N 으로 직접 주기")
        if spec.startswith("cam:"):
            self.cap = cv2.VideoCapture(f"/dev/video{int(spec[4:])}", cv2.CAP_V4L2); self.kind = spec; return
        p = pathlib.Path(spec)
        if p.is_dir():
            self.imgs = sorted(str(x) for x in p.iterdir() if x.suffix.lower() in (".jpg", ".png", ".jpeg")); self.kind = f"사진 폴더 {len(self.imgs)}장"
        elif p.suffix.lower() in (".jpg", ".png", ".jpeg"):
            self.imgs = [str(p)]; self.kind = "사진 1장"
        else:
            self.cap = cv2.VideoCapture(str(p)); self.kind = f"영상 {p.name}"
        self.t0 = time.time()

    def read(self):
        if self.pipe is not None:
            f = self.pipe.wait_for_frames().get_color_frame(); return np.asanyarray(f.get_data()).copy()
        if self.cap is not None:
            ok, fr = self.cap.read(); return fr if ok else None
        i = int((time.time() - self.t0) / a.per_image) % len(self.imgs)
        im = cv2.imread(self.imgs[i]); h, w = im.shape[:2]
        # 작은 사진을 키우면 흐려져 얼음 질감이 사라진다 (RSCD 360×240 을 720 높이로 키우자 빙판 88 % → 8 %) — 큰 것만 줄인다
        return cv2.resize(im, (int(w * 720 / h), 720), interpolation=cv2.INTER_AREA) if h > 720 else im


# ---------------------------------------------------------------- 판정
import onnxruntime as ort  # noqa: E402
so = ort.SessionOptions(); so.intra_op_num_threads = 3
sess = ort.InferenceSession(a.model, so, providers=["CPUExecutionProvider"]); iname = sess.get_inputs()[0].name
ctx = build_context(WeatherObs(temp_c=a.temp, humidity=88.0, temp_trend_c_per_h=(-1.0 if a.temp <= 2 else 0.0)), LocationCtx(feature="bridge", hour=5))
fuser = RiskFuser(ctx.weights, threshold=ctx.threshold)


def infer(bgr):
    h, w = bgr.shape[:2]; s = int(min(h, w) * a.crop); y0, x0 = (h - s) // 2, (w - s) // 2
    roi = bgr[y0:y0 + s, x0:x0 + s]
    x = cv2.resize(roi, (224, 224), interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0
    lg = sess.run(None, {iname: ((x - MEAN) / STD)[None]})[0][0]; e = np.exp(lg - lg.max()); p = e / e.sum()
    r = fuser.fuse(p.tolist(), spec=None, lane=None)
    return p, r.risk, r.alarm, (x0, y0, s)


# ---------------------------------------------------------------- 화면 (한글은 PIL)
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
FP = next((f for f in ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                       "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc") if os.path.exists(f)), None)
FONT = {s: (ImageFont.truetype(FP, s) if FP else ImageFont.load_default()) for s in (18, 22, 28, 36, 48)}
PANEL = 440


def render(bgr, p, risk, alarm, box, fps):
    h, w = bgr.shape[:2]; x0, y0, s = box
    view = bgr.copy(); cv2.rectangle(view, (x0, y0), (x0 + s, y0 + s), (0, 0, 255) if alarm else (255, 255, 255), 3)
    canvas = np.full((h, w + PANEL, 3), (24, 22, 22), np.uint8); canvas[:, :w] = view
    im = Image.fromarray(canvas[:, :, ::-1]); d = ImageDraw.Draw(im); X = w + 24; y = 22
    d.text((X, y), "1차 방어 — 카메라 노면 판정", font=FONT[28], fill=(140, 200, 255)); y += 46
    d.text((X, y), f"입력: RealSense D435i" if "Real" in src.kind or "pyreal" in src.kind else f"입력: {src.kind}", font=FONT[18], fill=(160, 160, 160)); y += 40
    order = np.argsort(-p)
    for i in range(len(ROAD_CLASSES)):
        k = ROAD_CLASSES[i]; v = float(p[i]); col = (255, 90, 90) if k == "black_ice" else (200, 200, 200)
        d.text((X, y), f"{KOR[k]}  {v * 100:5.1f} %", font=FONT[22], fill=col); y += 32
        d.rectangle((X, y, X + int((PANEL - 60) * v), y + 12), fill=col); y += 26
    y += 10
    d.text((X, y), f"위험도  {risk:.2f}   (경고 문턱 {fuser.threshold:.2f}, 기온 {a.temp:+.0f}°C)", font=FONT[22], fill=(255, 200, 120)); y += 36
    d.rectangle((X, y, X + PANEL - 60, y + 18), outline=(120, 120, 120)); d.rectangle((X, y, X + int((PANEL - 60) * min(1, risk)), y + 18), fill=(255, 170, 60))
    tx = X + int((PANEL - 60) * fuser.threshold); d.line((tx, y - 6, tx, y + 24), fill=(255, 255, 255), width=3); y += 44
    d.text((X, h - 64), f"판정 영역 {a.crop:.2f} (+/- 키)", font=FONT[18], fill=(130, 130, 130))
    d.text((X, h - 40), f"{fps:4.1f} fps · RoadNet onnx (CPU)", font=FONT[18], fill=(130, 130, 130))
    if alarm:
        msg = "블랙아이스 감지 — 감속하세요"
        fz = next((z for z in (48, 36, 28, 22) if d.textlength(msg, font=FONT[z]) < w - 60), 18); tw = d.textlength(msg, font=FONT[fz])
        d.rectangle(((w - tw) / 2 - 20, 24, (w + tw) / 2 + 20, 40 + fz * 1.4), fill=(190, 30, 30)); d.text(((w - tw) / 2, 32), msg, font=FONT[fz], fill=(255, 255, 255))
    else:
        top = ROAD_CLASSES[int(order[0])]
        d.text((24, 24), f"판정: {KOR[top]}", font=FONT[36], fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
    return np.array(im)[:, :, ::-1]


src = Source(a.source)
print(f"[primary] 입력 {src.kind}, 모델 {a.model}, 경고 문턱 {fuser.threshold:.3f}", flush=True)
sendq = None
if a.send:                                                   # 파이 → 데스크톱 방향 (캠퍼스망은 반대 방향을 막는다)
    h_, p_ = a.send.rsplit(":", 1); sendq = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (h_, int(p_)))
rec = None; t_start = time.time(); n = 0; fps = 0.0; t_fps = time.time(); n_alarm = 0
try:
    while True:
        fr = src.read()
        if fr is None: break
        p, risk, alarm, box = infer(fr)                         # 판정은 원래 해상도로
        if sendq:
            try: sendq[0].sendto(b"PRI1" + struct.pack("<fffB", risk, float(p[ICE]), fuser.threshold, int(bool(alarm))), sendq[1])
            except OSError: pass
            if n % 2 == 0:                                       # 카메라 화면도 작게 (CARLA 화면 왼쪽 아래에 띄운다)
                x0_, y0_, s_ = box; th = fr.copy(); cv2.rectangle(th, (x0_, y0_), (x0_ + s_, y0_ + s_), (0, 0, 255) if alarm else (255, 255, 255), 6)
                th = cv2.resize(th, (320, int(320 * fr.shape[0] / fr.shape[1])), interpolation=cv2.INTER_AREA)
                ok_, jp = cv2.imencode(".jpg", th, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok_ and jp.nbytes < 60000:
                    try: sendq[0].sendto(b"PIM1" + jp.tobytes(), sendq[1])
                    except OSError: pass
        h0 = fr.shape[0]; k_ = 720 / h0 if h0 < 600 else 1.0      # 화면만 키운다 (작은 사진에서 글자가 겹치지 않게)
        disp = cv2.resize(fr, None, fx=k_, fy=k_, interpolation=cv2.INTER_NEAREST) if k_ != 1.0 else fr
        out = render(disp, p, risk, alarm, tuple(int(v * k_) for v in box), fps); n += 1; n_alarm += alarm
        if time.time() - t_fps > 1.0: fps = n / (time.time() - t_fps); n = 0; t_fps = time.time()
        if a.record:
            if rec is None: rec = cv2.VideoWriter(a.record, cv2.VideoWriter_fourcc(*"mp4v"), 15, (out.shape[1], out.shape[0]))
            rec.write(out)
        if not a.headless:
            cv2.imshow("IcePredict 1st defense", out)
            k = cv2.waitKey(1) & 0xFF
            if k in (27, ord("q")): break
            if k in (ord("+"), ord("=")): a.crop = min(1.0, a.crop + 0.05)       # 판정 영역을 카메라에 비친 사진 크기에 맞춘다
            if k in (ord("-"), ord("_")): a.crop = max(0.2, a.crop - 0.05)
        if a.seconds and time.time() - t_start > a.seconds: break
finally:
    if rec is not None: rec.release()
    print(f"[primary] 끝 — 경고 프레임 {n_alarm}", flush=True)
