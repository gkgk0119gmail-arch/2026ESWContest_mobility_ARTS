#!/usr/bin/env python3
"""실사진(RSCD)을 **대규모로** 보드에 넣어 표본별 판정을 남긴다.

기존 `rscd_in_the_loop.py` 는 클래스별 300장을 돌리고 집계만 남겼다. 그래서
- 문턱을 사후에 쓸어볼 수 없었고 (표본별 risk 가 없다)
- 69,360 장짜리 데이터셋에서 1,200 장(1.7 %)만 쓰고 있었다.

이 스크립트는 표본별 (경로, 정답, p[4], 반사도, 위험도, 경보, 추론시간) 을 JSONL 로 남긴다.
그 한 파일로 정확도·혼동행렬·문턱곡선·지연분포를 전부 다시 계산할 수 있고,
다시 돌릴 필요가 없다.

사용:
  python3 scripts/rscd_board_eval.py --n 2000                 # 클래스별 2000장
  python3 scripts/rscd_board_eval.py --n 2000 --split test_50k --seed 1
  python3 scripts/rscd_board_eval.py --report-only            # 이미 있는 JSONL 로 보고서만
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.common.rscd import parse as rscd_parse  # noqa: E402

FR_HDR, FR_VD, FR_CHUNK = "<IHHHH", "<I4fffBBHII", 1400
NPU_IN_SCALE, NPU_IN_ZP = 0.018658448, -14
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
CLS = ("normal", "wet", "black_ice", "pothole")
ICE = CLS.index("black_ice")
OUT = ROOT / "logs" / "rscd_board_samples.jsonl"

# 운영 문턱은 context.py 가 결빙 prior 로 정한다 (2026-09-21 재교정).
# 여기서 하드코딩하면 교정할 때마다 보고서가 문서와 어긋난다.
try:
    from icepredict.pi.context import WeatherObs as _W, LocationCtx as _L, build_context as _bc
    TH_OPS = _bc(_W(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                 _L(feature="bridge", hour=5)).threshold
except Exception:
    TH_OPS = 0.60
TH_LEGACY = 0.441        # 재교정 이전 값. 옛 결과와 비교할 때만 쓴다.


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
    return dict(p=(e / e.sum()).tolist(), spec=float(spec), risk=float(risk),
                alarm=bool(alarm), level=int(level), us=int(us), tot_us=int(tot))


def collect(a):
    root = ROOT / "dataset/rscd/rscd" / a.split
    by: dict[str, list] = {c: [] for c in CLS}
    for f in sorted(root.glob("*.jpg")):
        lab = rscd_parse(f.name)
        if lab:
            by[lab.cls4].append(f)
    print("데이터셋 보유량: " + ", ".join(f"{c} {len(by[c])}장" for c in CLS))

    rng = np.random.default_rng(a.seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    fid = int(time.time()) & 0xFFFF
    mode = "a" if a.append else "w"
    n_ok = n_fail = 0
    t0 = time.time()

    with open(OUT, mode) as fh:
        for cls in CLS:
            files = by[cls]
            if not files:
                print(f"  {cls}: 사진 없음")
                continue
            idx = rng.choice(len(files), min(a.n, len(files)), replace=False)
            for k, i in enumerate(idx):
                f = files[i]
                im = cv2.imread(str(f))
                if im is None:
                    continue
                fid = (fid + 1) & 0xFFFFFFFF
                r = board_infer(sock, a.ip, a.port, quantize(im), fid, a.gap_us)
                if r is None:
                    n_fail += 1
                    continue
                r.update(cls=cls, file=f.name, split=a.split)
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_ok += 1
                if (k + 1) % 250 == 0:
                    el = time.time() - t0
                    fh.flush()
                    print(f"  {cls}: {k+1}/{len(idx)}  누적 {n_ok}장  "
                          f"{el/max(n_ok,1)*1000:.0f} ms/장  경과 {el/60:.1f}분", flush=True)
    print(f"\n완료: {n_ok}장 성공, {n_fail}장 무응답 → {OUT}")


def report(a):
    rows = [json.loads(l) for l in open(OUT) if l.strip()]
    if not rows:
        raise SystemExit("표본이 없다. 먼저 수집할 것.")
    P = np.array([r["p"] for r in rows])
    risk = np.array([r["risk"] for r in rows])
    spec = np.array([r["spec"] for r in rows])
    us = np.array([r["us"] for r in rows])
    y = np.array([CLS.index(r["cls"]) for r in rows])
    pred = P.argmax(1)

    L = ["# 실사진 대규모 보드 평가 (인-더-루프)\n\n",
         f"RSCD 실사진 **{len(rows):,}장**을 STM32N6 보드에 직접 넣어 받은 판정이다. "
         "시뮬 화면이 아니라 실제 도로 사진이고, 추론·융합 모두 보드에서 돈다.\n\n",
         f"표본별 원본은 `logs/rscd_board_samples.jsonl` 에 있다 — 문턱을 바꿔 다시 계산할 때 "
         "보드를 다시 돌릴 필요가 없다.\n\n"]

    n_hyst = int(((risk < TH_LEGACY) & np.array([r["alarm"] for r in rows])).sum())
    L.append("## 1. 클래스별 성능\n\n")
    L.append(f"| 실제 클래스 | 장수 | 정답률 | 경보율(운영 문턱 {TH_OPS:.3f}) | 평균 위험도 | 평균 반사도 |\n"
             "|---|---|---|---|---|---|\n")
    for i, c in enumerate(CLS):
        m = y == i
        if not m.any():
            continue
        L.append(f"| {c} | {m.sum():,} | {(pred[m] == i).mean():.3f} | "
                 f"{(risk[m] >= TH_OPS).mean():.3f} | "
                 f"{risk[m].mean():.3f} | {spec[m].mean():.3f} |\n")
    L.append(f"\n> **경보율은 보드의 alarm 플래그가 아니라 `risk >= 문턱` 으로 계산했다.** "
             f"보드 융합기에는 히스테리시스가 있어(켜질 땐 0.441, 꺼질 땐 0.341) 연속 영상에서는 "
             f"깜빡임을 막아 주지만, 서로 무관한 **낱장 사진**을 이어 넣으면 앞 사진의 상태가 넘어온다. "
             f"실제로 이번 표본에서 `alarm=True` 인데 `risk < 0.441` 인 경우가 {n_hyst}건 "
             f"({100*n_hyst/len(rows):.1f} %) 있었다. 기존 `rscd_in_the_loop.py` 결과는 "
             f"그만큼 경보율이 부풀려져 있다.\n")

    L.append("\n## 2. 혼동 행렬 (행 = 실제, 열 = 예측)\n\n")
    L.append("| 실제 \\ 예측 | " + " | ".join(CLS) + " |\n" + "|---" * (len(CLS) + 1) + "|\n")
    for i, c in enumerate(CLS):
        m = y == i
        if not m.any():
            continue
        cells = [f"{(pred[m] == j).mean()*100:.1f}%" for j in range(len(CLS))]
        L.append(f"| **{c}** | " + " | ".join(cells) + " |\n")

    L.append("\n## 3. 문턱 곡선 — 실사진 기준\n\n")
    L.append("이 표가 운영점 결정의 근거다. 얼음은 높게, 나머지는 낮게 나와야 한다.\n\n")
    L.append("| 문턱 | " + " | ".join(f"{c} 경보율" for c in CLS) + " |\n" + "|---" * (len(CLS) + 1) + "|\n")
    for th in sorted({TH_LEGACY, 0.50, 0.55, 0.57, TH_OPS, 0.65, 0.70, 0.75}):
        cells = []
        for i in range(len(CLS)):
            m = y == i
            cells.append(f"{(risk[m] >= th).mean()*100:.1f}%" if m.any() else "-")
        mark = (" **(운영)**" if abs(th - TH_OPS) < 1e-6 else
                (" (교정 전)" if abs(th - TH_LEGACY) < 1e-6 else ""))
        L.append(f"| {th:.3f}{mark} | " + " | ".join(cells) + " |\n")

    # 운영점 추천
    ice_m, best = y == ICE, None
    for th in np.arange(0.35, 0.90, 0.005):
        bad = max((risk[y == i] >= th).mean() for i in range(len(CLS)) if i != ICE and (y == i).any())
        if bad <= 0.05:
            best = (float(th), float((risk[ice_m] >= th).mean()), float(bad))
            break
    if best:
        L.append(f"\n**얼음 외 오경보 5 % 이하를 만족하는 가장 낮은 문턱: {best[0]:.3f}** "
                 f"— 그때 얼음 경보율 {best[1]*100:.1f} %, 최악 오경보 {best[2]*100:.1f} %\n")

    L.append("\n## 4. 보드 추론 시간\n\n")
    L.append("| 지표 | 값 |\n|---|---|\n")
    for name, v in (("평균", us.mean()), ("중앙값", np.median(us)),
                    ("p99", np.percentile(us, 99)), ("최대", us.max()), ("최소", us.min())):
        L.append(f"| {name} | {v/1000:.2f} ms |\n")
    L.append(f"| 표본 | {len(us):,}회 |\n")
    L.append(f"\n편차가 {(us.max()-us.min())/1000:.2f} ms 로 "
             f"평균의 {100*(us.max()-us.min())/us.mean():.1f} % 다. "
             "NPU 추론은 입력 내용과 무관하게 **고정 비용**이라는 뜻이고, "
             "그래서 상위 제어의 주기를 설계할 수 있다.\n")

    out = ROOT / "logs/carla_demo/정리" / "05_실사진_대규모평가.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000, help="클래스별 장수")
    ap.add_argument("--split", default="vali_20k")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--ip", default="192.168.50.158")
    ap.add_argument("--port", type=int, default=5559)
    ap.add_argument("--gap-us", type=int, default=150)
    ap.add_argument("--append", action="store_true", help="기존 JSONL 에 덧붙인다")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()

    if not a.report_only:
        collect(a)
    report(a)


if __name__ == "__main__":
    main()
