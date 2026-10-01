#!/usr/bin/env python3
"""실사진만으로 만드는 데모 영상 — 시뮬레이션이 한 프레임도 안 들어간다.

왜: 지금 발표 영상이 전부 CARLA 화면이라 "시뮬에서만 되는 것 아니냐"는 반론에 약하다.
실제 도로 사진을 순서대로 보드에 넣고, 보드가 내놓는 판정을 그대로 그려 영상으로 만들면
그 반론이 사라진다. 추론도 융합도 전부 STM32N6 에서 돈다.

구성: 정상 노면을 달리다 → 젖은 구간 → 빙판 진입 → 다시 정상.
      RSCD 실사진을 그 순서로 이어 붙이고 한 장씩 보드에 보낸다.

화면: 왼쪽 사진, 오른쪽에 클래스 확률 막대 · 위험도 게이지 · 위험도 이력 · 상태 · 보드 처리시간.

사용: python3 sw/scripts/sim/real_photo_drive.py [--fps 12] [--out logs/carla_demo/demo_실사진주행.mp4]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import socket
import struct
import sys
import time

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.common.rscd import parse as rscd_parse  # noqa: E402

FR_HDR, FR_VD, FR_CHUNK = "<IHHHH", "<I4fffBBHII", 1400
NPU_IN_SCALE, NPU_IN_ZP = 0.018658448, -14
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
CLS = ("normal", "wet", "black_ice", "pothole")
KO = {"normal": "정상", "wet": "젖음", "black_ice": "블랙아이스", "pothole": "포트홀"}
COL = {"normal": (110, 200, 110), "wet": (200, 150, 70),
       "black_ice": (70, 70, 235), "pothole": (60, 170, 230)}
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"
W, H = 1280, 720
PW, PH = 760, 560          # 사진 영역
TH = 0.60                  # 권고 운영점


def ko(img, xy, text, size=22, color=(255, 255, 255)):
    from PIL import Image, ImageDraw, ImageFont
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype(FONT, size)
    except Exception:
        f = ImageFont.load_default()
    d.text(xy, text, font=f, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def quantize(bgr):
    im = cv2.resize(bgr, (224, 224), interpolation=cv2.INTER_AREA)
    x = (im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - MEAN) / STD
    return np.clip(np.round(x / NPU_IN_SCALE) + NPU_IN_ZP, -128, 127).astype(np.int8).tobytes()


def board_infer(sock, ip, port, q, fid, gap_us=150):
    n = (len(q) + FR_CHUNK - 1) // FR_CHUNK
    for i in range(n):
        pl = q[i * FR_CHUNK:(i + 1) * FR_CHUNK]
        sock.sendto(struct.pack(FR_HDR, fid, i, n, len(pl), 0) + pl, (ip, port))
        t = time.perf_counter() + gap_us / 1e6
        while time.perf_counter() < t:
            pass
    try:
        d, _ = sock.recvfrom(128)
    except socket.timeout:
        return None
    f, l0, l1, l2, l3, spec, risk, alarm, level, _p, us, tot = struct.unpack(FR_VD, d)
    if f != fid:
        return None
    lg = np.array([l0, l1, l2, l3], dtype=np.float64)
    e = np.exp(lg - lg.max())
    return dict(p=(e / e.sum()), spec=float(spec), risk=float(risk), us=int(us))


def pick(split_dirs, want_cls, n, rng, avoid=None):
    out = []
    for d in split_dirs:
        for f in sorted(d.glob("*.jpg")):
            lab = rscd_parse(f.name)
            if lab and lab.cls4 == want_cls:
                out.append(f)
        if len(out) > 4000:
            break
    if not out:
        return []
    idx = rng.choice(len(out), min(n, len(out)), replace=False)
    return [out[i] for i in idx]


def panel(res, hist, seg_name, i, total, fired):
    p = np.full((H, W - PW, 3), (22, 22, 26), np.uint8)
    x0 = 24
    p = ko(p, (x0, 18), "STM32N6 보드 판정", 25, (235, 235, 235))
    p = ko(p, (x0, 50), f"{seg_name}   ({i+1}/{total})", 17, (150, 150, 150))

    # 클래스 확률 막대
    y = 92
    p = ko(p, (x0, y), "클래스 확률", 18, (180, 180, 180))
    y += 28
    for k, c in enumerate(CLS):
        v = float(res["p"][k])
        cv2.rectangle(p, (x0, y), (x0 + int(300 * v), y + 20), COL[c], -1)
        cv2.rectangle(p, (x0, y), (x0 + 300, y + 20), (70, 70, 70), 1)
        p = ko(p, (x0 + 310, y - 2), f"{KO[c]} {v:.2f}", 16,
               COL[c] if v > 0.4 else (160, 160, 160))
        y += 28

    # 위험도 게이지
    y += 14
    p = ko(p, (x0, y), f"융합 위험도   (문턱 {TH:.2f})", 18, (180, 180, 180))
    y += 28
    r = res["risk"]
    gw = 420
    cv2.rectangle(p, (x0, y), (x0 + gw, y + 26), (45, 45, 50), -1)
    gc = (70, 70, 235) if fired else (110, 190, 110)
    cv2.rectangle(p, (x0, y), (x0 + int(gw * min(r, 1.0)), y + 26), gc, -1)
    tx = x0 + int(gw * TH)
    cv2.line(p, (tx, y - 5), (tx, y + 31), (240, 240, 240), 2)
    p = ko(p, (x0 + gw + 12, y + 1), f"{r:.2f}", 19, gc)
    y += 46
    p = ko(p, (x0, y), f"반사도 {res['spec']:.2f}", 16, (150, 150, 150))

    # 위험도 이력
    y += 40
    p = ko(p, (x0, y), "위험도 이력", 18, (180, 180, 180))
    y += 26
    hh, hwd = 110, 460
    cv2.rectangle(p, (x0, y), (x0 + hwd, y + hh), (30, 30, 34), -1)
    ty = y + hh - int(hh * TH)
    cv2.line(p, (x0, ty), (x0 + hwd, ty), (120, 120, 120), 1, cv2.LINE_AA)
    if len(hist) > 1:
        step = hwd / max(len(hist) - 1, 1)
        pts = [(int(x0 + k * step), int(y + hh - hh * min(v, 1.0))) for k, v in enumerate(hist)]
        for k in range(1, len(pts)):
            cv2.line(p, pts[k - 1], pts[k], (235, 235, 235), 2, cv2.LINE_AA)
    y += hh + 22

    # 상태
    if fired:
        cv2.rectangle(p, (x0, y), (x0 + 460, y + 46), (40, 40, 150), -1)
        p = ko(p, (x0 + 14, y + 9), "블랙아이스 경보 — 1차 방어 발화", 22, (255, 255, 255))
    else:
        cv2.rectangle(p, (x0, y), (x0 + 460, y + 46), (35, 60, 35), -1)
        p = ko(p, (x0 + 14, y + 9), "정상 주행", 22, (200, 230, 200))
    y += 62
    p = ko(p, (x0, y), f"보드 NPU 추론 {res['us']/1000:.2f} ms   (고정 비용)", 16, (150, 150, 150))
    p = ko(p, (x0, y + 24), "추론·융합 모두 STM32N6 에서 실행", 15, (120, 120, 120))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--ip", default="192.168.50.158")
    ap.add_argument("--port", type=int, default=5559)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "logs/carla_demo" / "demo_실사진주행_split.mp4"))
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    dirs = [ROOT / "dataset/rscd/rscd/vali_20k", ROOT / "dataset/rscd/rscd/test_50k"]
    plan = [("정상 노면 주행", "normal", 26),
            ("젖은 노면 진입", "wet", 18),
            ("블랙아이스 구간", "black_ice", 34),
            ("빙판 통과 — 정상 복귀", "normal", 22)]
    seq = []
    for name, c, n in plan:
        for f in pick(dirs, c, n, rng):
            seq.append((name, c, f))
    if not seq:
        raise SystemExit("사진을 찾지 못했다")
    print(f"프레임 {len(seq)}장 준비")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    fid = int(time.time()) & 0xFFFF
    vw = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (W, H))
    hist: list[float] = []
    n_fire = 0

    for i, (name, c, f) in enumerate(seq):
        img = cv2.imread(str(f))
        if img is None:
            continue
        fid = (fid + 1) & 0xFFFFFFFF
        res = board_infer(sock, a.ip, a.port, quantize(img), fid)
        if res is None:
            print(f"  {i}: 보드 무응답 — 건너뜀")
            continue
        hist.append(res["risk"])
        if len(hist) > 90:
            hist.pop(0)
        fired = res["risk"] >= TH
        n_fire += int(fired)

        left = np.full((H, PW, 3), (16, 16, 18), np.uint8)
        ph = cv2.resize(img, (PW - 40, PH))
        left[28:28 + PH, 20:20 + PW - 40] = ph
        border = (70, 70, 235) if fired else (90, 90, 95)
        cv2.rectangle(left, (20, 28), (20 + PW - 40, 28 + PH), border, 3)
        left = ko(left, (22, 2), "RSCD 실제 도로 사진", 18, (170, 170, 170))
        left = ko(left, (22, 28 + PH + 14), f"정답 라벨: {KO[c]}", 20, (200, 200, 200))
        left = ko(left, (22, 28 + PH + 46), f"파일 {f.name[:46]}", 14, (110, 110, 110))
        left = ko(left, (22, 28 + PH + 72), "시뮬레이션 아님 — 실제 촬영본", 15, (120, 160, 120))

        frame = np.hstack([left, panel(res, hist, name, i, len(seq), fired)])
        vw.write(frame)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(seq)}", flush=True)

    vw.release()
    print(f"\n완료: {a.out}")
    print(f"경보 발화 프레임 {n_fire}/{len(seq)}")


if __name__ == "__main__":
    main()
