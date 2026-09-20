#!/usr/bin/env python3
"""데모 영상에서 '느낌 있는' 장면을 사진으로 뽑는다: 접근 / 1차 경고 / 빙판 진입 / 2차(IMU) 발화 / 정지.
events_{tag}.json 의 시각을 기준으로 demo_{tag}_{view}.mp4 에서 프레임을 저장한다 → logs/carla_demo/photos/"""
import json, sys, cv2, pathlib
d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/mnt/ssd/icepredict/logs/carla_demo"); out = d / "photos"; out.mkdir(exist_ok=True)
n = 0
for ej in sorted(d.glob("events_*.json")):
    tag = ej.stem[len("events_"):]; ev = json.load(open(ej))["events"]
    if not ev: continue
    t = {e["event"]: e["t"] for e in ev}
    shots = {}
    first = min(e["t"] for e in ev)
    shots["approach"] = max(0.5, first - 1.5)
    for k, name in (("primary_warning", "warning"), ("patch_enter", "enter"), ("secondary_slip", "slip"), ("stopped", "stop"), ("stabilized", "stop")):
        if k in t: shots[name] = t[k] + (0.4 if name in ("warning", "slip") else 0.0)
    for view in ("split", "bev", "lidar", "lidar_sem", "front"):
        v = d / f"demo_{tag}_{view}.mp4"
        if not v.exists(): continue
        c = cv2.VideoCapture(str(v)); fps = c.get(5) or 50; nf = int(c.get(7))
        for name, ts in shots.items():
            c.set(1, min(max(int(ts * fps), 0), nf - 1)); ok, f = c.read()
            if ok:
                cv2.imwrite(str(out / f"{tag}__{name}__{view}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 92]); n += 1
        c.release()
    # 타임라인: 1인칭+조감 6장 (시작→끝 균등), 한 장으로
    v = d / f"demo_{tag}_split.mp4"
    if v.exists():
        c = cv2.VideoCapture(str(v)); fps = c.get(5) or 50; nf = int(c.get(7)); tiles = []
        for i in range(6):
            c.set(1, min(int(nf * (i + 0.5) / 6), nf - 1)); ok, f = c.read()
            if ok: tiles.append(cv2.resize(f, (640, 240)))
        c.release()
        if len(tiles) == 6:
            sheet = cv2.vconcat([cv2.hconcat(tiles[0:2]), cv2.hconcat(tiles[2:4]), cv2.hconcat(tiles[4:6])])
            cv2.imwrite(str(out / f"{tag}__timeline__split.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90]); n += 1
print(f"사진 {n}장 → {out}")
