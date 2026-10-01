#!/usr/bin/env python3
"""보드가 **틀린** 실사진을 모아 보여준다 — 무엇을 고쳐야 하는지 눈으로 본다.

숫자로는 "안전에 영향 주는 오분류 1.24 %" 라고 말할 수 있지만, 그 안에 어떤 사진이
들어 있는지는 표로 안 보인다. 두 종류를 따로 모은다.

  미탐(놓침)  얼음인데 위험도가 문턱 아래 — 가장 위험한 실패
  오경보      얼음이 아닌데 위험도가 문턱 위 — 신뢰를 깎는 실패

각 사진에 정답·보드 판정·확률·반사도를 얹어 격자로 만든다.

사용: python3 scripts/analysis/failure_montage.py [--th 0.60]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.common.rscd import parse as rscd_parse  # noqa: E402

CLS = ("normal", "wet", "black_ice", "pothole")
KO = {"normal": "정상", "wet": "젖음", "black_ice": "블랙아이스", "pothole": "포트홀"}
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"
FIG = ROOT / "logs/carla_demo/figures"
SAMPLES = ROOT / "logs/rscd_board_samples.jsonl"


def ko(img, xy, text, size=18, color=(255, 255, 255)):
    from PIL import Image, ImageDraw, ImageFont
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype(FONT, size)
    except Exception:
        f = ImageFont.load_default()
    d.text(xy, text, font=f, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def find(name, dirs):
    for d in dirs:
        p = d / name
        if p.exists():
            return cv2.imread(str(p))
    return None


def sheet(rows, dirs, title, sub, accent, per=5, n=10):
    W, H = 300, 220
    tiles = []
    for r in rows[:n]:
        img = find(r["file"], dirs)
        if img is None:
            continue
        img = cv2.resize(img, (W, H))
        bar = np.full((84, W, 3), (34, 34, 38), np.uint8)
        img = np.vstack([img, bar])
        pred = CLS[int(np.argmax(r["p"]))]
        lab = rscd_parse(r["file"])
        cond = "-"
        if lab:
            cond = f"{lab.friction}/{lab.material or '-'}/{lab.uneven or '-'}"
        img = ko(img, (8, H + 3), f"정답 {KO[r['cls']]} → 보드 {KO[pred]}", 16, accent)
        img = ko(img, (8, H + 25), f"위험도 {r['risk']:.2f}  얼음확률 {r['p'][2]:.2f}  반사도 {r['spec']:.2f}",
                 13, (190, 190, 190))
        img = ko(img, (8, H + 44), cond, 12, (140, 140, 140))
        img = ko(img, (8, H + 62), r["file"][:38], 11, (105, 105, 105))
        cv2.rectangle(img, (0, 0), (W - 1, H + 83), accent, 2)
        tiles.append(img)
    if not tiles:
        return None
    lines = [np.hstack(tiles[i:i + per]) for i in range(0, len(tiles), per)
             if len(tiles[i:i + per]) == per]
    if not lines:
        return None
    body = np.vstack(lines)
    hdr = np.full((72, body.shape[1], 3), (22, 22, 26), np.uint8)
    hdr = ko(hdr, (14, 8), title, 25, accent)
    hdr = ko(hdr, (14, 42), sub, 16, (165, 165, 165))
    return np.vstack([hdr, body])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--th", type=float, default=0.60)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(SAMPLES) if l.strip()]
    dirs = [ROOT / "dataset/rscd/rscd/test_50k", ROOT / "dataset/rscd/rscd/vali_20k"]
    FIG.mkdir(parents=True, exist_ok=True)

    miss = sorted([r for r in rows if r["cls"] == "black_ice" and r["risk"] < a.th],
                  key=lambda r: r["risk"])                     # 가장 심하게 놓친 것부터
    fa = sorted([r for r in rows if r["cls"] != "black_ice" and r["risk"] >= a.th],
                key=lambda r: -r["risk"])                      # 가장 확신한 오경보부터
    ice_n = sum(1 for r in rows if r["cls"] == "black_ice")
    non_n = len(rows) - ice_n

    print(f"표본 {len(rows):,}장  ·  문턱 {a.th}")
    print(f"  미탐 {len(miss):,}건 ({100*len(miss)/max(ice_n,1):.1f} % of 얼음)")
    print(f"  오경보 {len(fa):,}건 ({100*len(fa)/max(non_n,1):.1f} % of 얼음 아님)")

    s = sheet(miss, dirs,
              f"미탐 — 얼음인데 놓친 사진  ({len(miss):,}건 / 얼음 {ice_n:,}장 중 {100*len(miss)/max(ice_n,1):.1f} %)",
              f"위험도가 낮은 순. 문턱 {a.th}. 가장 위험한 실패라 여기부터 본다.",
              (70, 70, 235))
    if s is not None:
        cv2.imwrite(str(FIG / "real_failure_miss.jpg"), s, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print("  real_failure_miss.jpg", s.shape)

    s = sheet(fa, dirs,
              f"오경보 — 얼음이 아닌데 경보  ({len(fa):,}건 / {non_n:,}장 중 {100*len(fa)/max(non_n,1):.1f} %)",
              f"위험도가 높은 순. 문턱 {a.th}. 대부분 새눈·물 고인 노면 — 실제로 미끄러운 조건이다.",
              (60, 170, 230))
    if s is not None:
        cv2.imwrite(str(FIG / "real_failure_falsealarm.jpg"), s, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print("  real_failure_falsealarm.jpg", s.shape)
    print(f"[저장] {FIG}")


if __name__ == "__main__":
    main()
