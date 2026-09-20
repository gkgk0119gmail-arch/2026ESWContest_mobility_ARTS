#!/usr/bin/env python3
"""두 주행을 나란히 붙인 비교 영상: 왼쪽 '방어 없음(기준선)', 오른쪽 'RTOS 2차 방어'. 같은 시각 프레임을 맞춘다.
사용: compare_videos.py <태그A(기준선)> <태그B(RTOS)> [view=bev] [out.mp4]"""
import sys, cv2, numpy as np, pathlib
D = pathlib.Path("/mnt/ssd/icepredict/logs/carla_demo")
a, b = sys.argv[1], sys.argv[2]; view = sys.argv[3] if len(sys.argv) > 3 else "bev"
out = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else D / f"compare_{a}_vs_{b}_{view}.mp4"
ca, cb = cv2.VideoCapture(str(D / f"demo_{a}_{view}.mp4")), cv2.VideoCapture(str(D / f"demo_{b}_{view}.mp4"))
fps = ca.get(5) or 50; n = int(max(ca.get(7), cb.get(7))); w, h = int(ca.get(3)), int(ca.get(4))
vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (2 * w + 8, h + 40))
la, lb = None, None
for i in range(n):
    oka, fa = ca.read(); okb, fb = cb.read()
    la = fa if oka else la; lb = fb if okb else lb          # 먼저 끝난 쪽은 마지막 프레임 유지
    if la is None or lb is None: continue
    canvas = np.full((h + 40, 2 * w + 8, 3), 20, np.uint8)
    canvas[40:, :w] = la; canvas[40:, w + 8:] = lb
    cv2.putText(canvas, "WITHOUT RTOS 2nd defense (baseline)", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (60, 60, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, "WITH STM32N6 RTOS 2nd defense", (w + 20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (80, 220, 80), 2, cv2.LINE_AA)
    vw.write(canvas)
vw.release(); print("저장:", out, n, "frames")
