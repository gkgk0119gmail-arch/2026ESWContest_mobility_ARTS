#!/usr/bin/env python3
"""실사진 보드 판정 결과로 발표용 그림을 만든다 (CARLA 화면이 아니다).

왜: 지금 발표 자료가 전부 시뮬 화면이라 "시뮬에서만 되는 것 아니냐"는 반론에 약하다.
실제 도로 사진을 보드에 넣어 받은 판정을 그대로 보여주면 그 반론이 사라진다.

만드는 것 (`logs/carla_demo/figures/` 에 저장):
  real_verdict_grid.jpg   실사진 12장 + 보드 판정(클래스·위험도·경보) 오버레이
  real_confusion.jpg      혼동 행렬
  real_threshold.jpg      문턱별 클래스 경보율 곡선 — 운영점 결정 근거
  rtos_latency.jpg        보드(ThreadX) vs 파이(리눅스) 지연 분포 — RTOS 논거

입력: logs/rscd_board_samples.jsonl (scripts/rscd_board_eval.py 가 만든다)
      /tmp/bench_slip_*.txt        (scripts/rtos_latency_bench.py 가 만든다)
"""
from __future__ import annotations

import json
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIG = ROOT / "logs/carla_demo/figures"
SAMPLES = ROOT / "logs/rscd_board_samples.jsonl"
CLS = ("normal", "wet", "black_ice", "pothole")
# 운영 문턱은 context.py 가 정한다 (2026-09-21 재교정: 데모 기본 조건에서 0.603)
try:
    from icepredict.pi.context import WeatherObs as _W, LocationCtx as _L, build_context as _bc
    TH_OPS = _bc(_W(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                 _L(feature="bridge", hour=5)).threshold
except Exception:
    TH_OPS = 0.60
KO = {"normal": "정상", "wet": "젖음", "black_ice": "블랙아이스", "pothole": "포트홀"}
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"


def kotext(img, xy, text, size=20, color=(255, 255, 255)):
    from PIL import Image, ImageDraw, ImageFont
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype(FONT, size)
    except Exception:
        f = ImageFont.load_default()
    d.text(xy, text, font=f, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def load():
    if not SAMPLES.exists():
        raise SystemExit(f"{SAMPLES} 가 없다. 먼저 scripts/rscd_board_eval.py 를 돌릴 것.")
    return [json.loads(l) for l in open(SAMPLES) if l.strip()]


# ---------- 1. 실사진 판정 격자 ----------
def grid(rows, split_dirs):
    """클래스마다 3장씩, 보드가 맞힌 것 위주로 고른다 (발표용이므로 대표 장면)."""
    W, H = 420, 300
    tiles = []
    for c in CLS:
        cand = [r for r in rows if r["cls"] == c and CLS[int(np.argmax(r["p"]))] == c]
        cand.sort(key=lambda r: -r["p"][CLS.index(c)])
        pick = cand[:3] if len(cand) >= 3 else [r for r in rows if r["cls"] == c][:3]
        for r in pick:
            img = None
            for d in split_dirs:
                p = d / r["file"]
                if p.exists():
                    img = cv2.imread(str(p))
                    break
            if img is None:
                continue
            img = cv2.resize(img, (W, H))
            pred = CLS[int(np.argmax(r["p"]))]
            ok = pred == r["cls"]
            bar = np.full((78, W, 3), (40, 40, 40), np.uint8)
            img = np.vstack([img, bar])
            col = (80, 220, 80) if ok else (60, 60, 230)
            img = kotext(img, (10, H + 4), f"정답 {KO[r['cls']]}  →  보드 {KO[pred]}", 19, col)
            # 보드 alarm 플래그는 히스테리시스가 있어 낱장 사진에는 맞지 않는다 → 위험도로 판정
            fired = r["risk"] >= TH_OPS
            al = "경보" if fired else "정상"
            acol = (60, 60, 230) if fired else (200, 200, 200)
            img = kotext(img, (10, H + 30), f"위험도 {r['risk']:.2f}   반사도 {r['spec']:.2f}   {al}", 17, acol)
            img = kotext(img, (10, H + 52), f"보드 추론 {r['us']/1000:.1f} ms", 15, (170, 170, 170))
            cv2.rectangle(img, (0, 0), (W - 1, H + 77), col, 2)
            tiles.append(img)
    if not tiles:
        return None
    per = 4
    rows_img = [np.hstack(tiles[i:i + per]) for i in range(0, len(tiles) - len(tiles) % per, per)]
    if not rows_img:
        return None
    sheet = np.vstack(rows_img)
    hdr = np.full((64, sheet.shape[1], 3), (25, 25, 25), np.uint8)
    hdr = kotext(hdr, (16, 8), "실제 도로 사진을 STM32N6 보드가 직접 판정한 결과", 26, (255, 255, 255))
    hdr = kotext(hdr, (16, 40), "CARLA 화면이 아니라 RSCD 실사진이다. 추론·융합 모두 보드에서 돈다.",
                 17, (170, 170, 170))
    return np.vstack([hdr, sheet])


# ---------- 2·3·4. 그래프 ----------
def charts(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    try:
        font_manager.fontManager.addfont(FONT)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False

    P = np.array([r["p"] for r in rows])
    y = np.array([CLS.index(r["cls"]) for r in rows])
    pred = P.argmax(1)
    risk = np.array([r["risk"] for r in rows])

    # 혼동 행렬
    M = np.zeros((4, 4))
    for i in range(4):
        m = y == i
        if m.any():
            for j in range(4):
                M[i, j] = (pred[m] == j).mean() * 100
    fig, ax = plt.subplots(figsize=(6.2, 5.2), dpi=150)
    im = ax.imshow(M, cmap="Blues", vmin=0, vmax=100)
    ax.set_xticks(range(4), [KO[c] for c in CLS])
    ax.set_yticks(range(4), [KO[c] for c in CLS])
    ax.set_xlabel("보드 판정"); ax.set_ylabel("실제")
    ax.set_title(f"실사진 {len(rows):,}장 — 보드 혼동 행렬 (%)")
    for i in range(4):
        for j in range(4):
            ax.text(j, i, f"{M[i,j]:.1f}", ha="center", va="center",
                    color="white" if M[i, j] > 55 else "black", fontsize=11)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout(); fig.savefig(FIG / "real_confusion.jpg", pil_kwargs={"quality": 92}); plt.close(fig)

    # 문턱 곡선
    th = np.arange(0.30, 0.90, 0.01)
    fig, ax = plt.subplots(figsize=(7.4, 4.6), dpi=150)
    colors = {"normal": "#4C9F70", "wet": "#3E7CB1", "black_ice": "#D64550", "pothole": "#E8A33D"}
    for i, c in enumerate(CLS):
        m = y == i
        if not m.any():
            continue
        ax.plot(th, [(risk[m] >= t).mean() * 100 for t in th], label=KO[c],
                color=colors[c], lw=2.2 if c == "black_ice" else 1.6)
    ax.axvline(TH_OPS, color="#D64550", ls="--", lw=1.6)
    ax.text(TH_OPS + 0.005, 92, f"운영 문턱 {TH_OPS:.2f}", fontsize=9, color="#D64550")
    ax.axvline(0.441, color="#999", ls=":", lw=1.0)
    ax.text(0.30, 92, "교정 전 0.441", fontsize=8.5, color="#999")
    ax.axvspan(0.575, 0.75, color="#7ED957", alpha=0.15)
    ax.text(0.60, 84, "안전 구간 (절벽 위)", fontsize=9, color="#3a7a2a")
    ax.set_xlabel("경보 문턱"); ax.set_ylabel("경보율 (%)")
    ax.set_title(f"실사진 {len(rows):,}장 — 문턱별 클래스 경보율")
    ax.grid(alpha=0.25); ax.legend(loc="center right"); ax.set_ylim(-3, 103)
    fig.tight_layout(); fig.savefig(FIG / "real_threshold.jpg", pil_kwargs={"quality": 92}); plt.close(fig)

    # RTOS 지연 분포
    b = ROOT / "logs/board_wcet.jsonl"
    lin = pathlib.Path("/tmp/bench_slip_load.txt")
    if b.exists() and lin.exists():
        br = [json.loads(l) for l in open(b) if l.strip()]
        busy = [r for r in br if r.get("npu_busy")] or br
        bmean = float(np.mean([r["avg_us"] for r in busy]))
        bmax = float(max(r["max_us"] for r in busy))
        v = np.loadtxt(lin) / 1000.0
        fig, ax = plt.subplots(figsize=(7.6, 4.4), dpi=150)
        ax.hist(v, bins=np.logspace(np.log10(max(v.min(), 0.01)), np.log10(v.max() * 1.1), 70),
                color="#3E7CB1", alpha=0.75, label="Pi 5 + 리눅스 (부하 중)")
        ax.axvline(bmean, color="#D64550", lw=2, label=f"보드 평균 {bmean:.1f} µs")
        ax.axvline(bmax, color="#D64550", lw=2, ls="--", label=f"보드 최악 {bmax:.1f} µs")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("같은 C 코드의 연산 지연 (µs, 로그축)"); ax.set_ylabel("빈도 (로그축)")
        ax.set_title("같은 slip_core.h — ThreadX 는 유계, 리눅스는 꼬리가 길다")
        ax.grid(alpha=0.25, which="both"); ax.legend()
        fig.tight_layout(); fig.savefig(FIG / "rtos_latency.jpg", pil_kwargs={"quality": 92}); plt.close(fig)
        print("  rtos_latency.jpg")


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    rows = load()
    print(f"표본 {len(rows):,}장")
    dirs = [ROOT / "dataset/rscd/rscd/test_50k", ROOT / "dataset/rscd/rscd/vali_20k"]
    g = grid(rows, dirs)
    if g is not None:
        cv2.imwrite(str(FIG / "real_verdict_grid.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print("  real_verdict_grid.jpg", g.shape)
    charts(rows)
    print("  real_confusion.jpg / real_threshold.jpg")
    print(f"[저장] {FIG}")


if __name__ == "__main__":
    main()
