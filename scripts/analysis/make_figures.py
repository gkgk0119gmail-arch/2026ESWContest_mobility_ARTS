#!/usr/bin/env python3
"""발표용 정지 그림 생성 (logs/carla_demo/figures/):
  1) AI Hub 형식 3종: 카메라 원본 | 2D 분할 라벨 | 라이다(클래스색·3D 박스)  — --export-labels 로 내보낸 프레임 사용
  2) 각 그룹 대표 장면 모음(사진 폴더에서)"""
import cv2, numpy as np, pathlib, re, json
from PIL import Image, ImageDraw, ImageFont
_FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"
def put_ko(img, text, xy, size=18, color=(255, 255, 255)):
    """cv2 Hershey 폰트에는 한글이 없다(???로 찍힘) → PIL + 나눔 폰트로 그린다. color 는 BGR."""
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)); d = ImageDraw.Draw(pil)
    try: f = ImageFont.truetype(_FONT, size)
    except Exception: f = ImageFont.load_default()
    d.text(xy, text, font=f, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
D = pathlib.Path(__file__).resolve().parents[2] / "logs/carla_demo"; F = D / "figures"; F.mkdir(exist_ok=True)
PAL = {0: (0, 0, 0), 1: (200, 60, 200), 2: (255, 255, 255), 3: (255, 255, 80), 4: (255, 120, 40)}
def label_vis(lab):
    out = np.zeros((*lab.shape, 3), np.uint8)
    for k, c in PAL.items(): out[lab == k] = c
    return out
n = 0
for lab_dir in sorted(D.parent.glob("labels_*")):
    labs = sorted(lab_dir.glob("*_label.png"))
    for lp in labs[::max(1, len(labs) // 4)][:4]:
        stem = lp.name[:-len("_label.png")]; img = cv2.imread(str(lab_dir / f"{stem}.jpg")); lab = cv2.imread(str(lp), 0)
        if img is None or lab is None: continue
        tag = re.sub(r"_\d{5}$", "", stem); m = re.search(r"_(\d{5})$", stem); step = int(m.group(1)) if m else 0
        ld = None
        for view in ("lidar_sem", "lidar"):
            vp = D / f"demo_{tag}_{view}.mp4"
            if vp.exists():
                c = cv2.VideoCapture(str(vp)); c.set(1, step); ok, fr = c.read(); c.release()
                if ok: ld = fr; break
        panels = [img, label_vis(lab)] + ([ld] if ld is not None else [])
        h = min(p.shape[0] for p in panels); panels = [cv2.resize(p, (int(p.shape[1] * h / p.shape[0]), h)) for p in panels]
        strip = np.hstack(panels); bar = np.full((34, strip.shape[1], 3), 255, np.uint8)
        x = 8
        for t, p in zip(("카메라 (CARLA, 실제 사진 텍스처 노면)", "2D 분할 라벨 (도로 / 차선 / 빙판 / 차량)", "시맨틱 라이다 + 3D 박스"), panels):
            bar = put_ko(bar, t, (x, 6), 20, (30, 30, 30)); x += p.shape[1]
        cv2.imwrite(str(F / f"triplet_{stem}.jpg"), np.vstack([strip, bar]), [cv2.IMWRITE_JPEG_QUALITY, 92]); n += 1
# 그룹 대표 모음: 정리/<그룹>/<주행>/사진_4미끄러짐감지_1인칭+조감.jpg 등
for g in sorted((D / "정리").glob("*_*/")):
    tiles = []
    runs = sorted([r for r in g.glob("*/")], key=lambda r: r.stat().st_mtime, reverse=True)[:6]   # 최신 주행 우선
    for run in runs:
        for pref in ("사진_4미끄러짐감지_1인칭+조감", "사진_2카메라경고_1인칭+조감", "사진_5정지_1인칭+조감", "사진_3빙판진입_1인칭+조감"):
            p = run / f"{pref}.jpg"
            if p.exists():
                im = cv2.imread(str(p)); im = cv2.resize(im, (960, 360))
                cv2.rectangle(im, (0, 0), (960, 28), (0, 0, 0), -1); im = put_ko(im, run.name[:60], (6, 4), 18)
                tiles.append(im); break
    if tiles:
        rows = [np.hstack(tiles[i:i + 2]) if i + 1 < len(tiles) else np.hstack([tiles[i], np.zeros_like(tiles[i])]) for i in range(0, len(tiles), 2)]
        cv2.imwrite(str(F / f"group_{g.name}.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88]); n += 1
# 날씨 격자: 같은 순간을 날씨별로 (사진 파일명: {tag}__{scene}__{view}.jpg)
WEATHER_KO = {"ClearNoon": "맑은낮", "CloudyNoon": "흐린낮", "WetNoon": "젖은노면", "SoftRainNoon": "보슬비", "HardRainNoon": "폭우",
              "ClearSunset": "맑은해질녘", "WetCloudySunset": "젖은흐린해질녘", "HardRainSunset": "폭우해질녘", "ClearNight": "맑은밤", "MidRainyNight": "비오는밤", "Snow": "눈"}
P = D / "photos"
def grid(title, items, cols=3, size=(640, 240)):
    tiles = []
    for cap, path in items:
        im = cv2.imread(str(path))
        if im is None: continue
        im = cv2.resize(im, size); cv2.rectangle(im, (0, 0), (size[0], 26), (0, 0, 0), -1); tiles.append(put_ko(im, cap, (6, 3), 17))
    if not tiles: return None
    while len(tiles) % cols: tiles.append(np.zeros_like(tiles[0]))
    rows = [cv2.hconcat(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    sheet = cv2.vconcat(rows); bar = np.full((36, sheet.shape[1], 3), 255, np.uint8); bar = put_ko(bar, title, (8, 6), 22, (30, 30, 30))
    return cv2.vconcat([bar, sheet])
for name, scen, scene, view in (("weather_1차_카메라경고", "detect", "warning", "split"), ("weather_2차_미끄러짐감지_주변차량", "miss_rtos_traffic", "slip", "split"),
                                ("weather_2차_미끄러짐감지", "miss_rtos", "slip", "split"), ("weather_기준선_방어없음", "nodefense_traffic", "timeline", "split")):
    items = []
    for w, ko in WEATHER_KO.items():
        ph = P / f"{w}_{scen}__{scene}__{view}.jpg"
        if ph.exists(): items.append((ko, ph))
    g = grid(f"{name.replace('_', ' ')} — 날씨별 같은 순간", items)
    if g is not None: cv2.imwrite(str(F / f"{name}.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 90]); n += 1
# 라이다 갤러리 (시맨틱 라이다, 미끄러짐 순간)
items = [(WEATHER_KO.get(w, w), P / f"{w}_miss_rtos_traffic__slip__lidar_sem.jpg") for w in WEATHER_KO if (P / f"{w}_miss_rtos_traffic__slip__lidar_sem.jpg").exists()]
g = grid("시맨틱 라이다 — 미끄러짐 감지 순간 (클래스색 · 3D 박스)", items, cols=3, size=(640, 480))
if g is not None: cv2.imwrite(str(F / "lidar_gallery.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 90]); n += 1
print(f"그림 {n}장 → {F}")
