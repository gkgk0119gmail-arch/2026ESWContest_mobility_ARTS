#!/usr/bin/env python3
"""영상·사진을 주행별 폴더로 정리한다 (원본은 그대로, 하드링크). 폴더 이름 = 날씨_속도_실제상황.
상황은 파일 태그가 아니라 events_{tag}.json 의 실제 결과로 정한다 — 카메라가 인식해 멈췄는지,
경고가 늦어 진입했는지, 미인식 후 어느 쪽(STM32N6 RTOS / 호스트)이 2차 방어를 했는지, 오경보였는지.

  logs/carla_demo/정리/
    00_목록.txt                                  주행 전체 요약 (한 줄에 하나)
    A_카메라인식_정지/  B_카메라미인식_RTOS2차방어/  C_카메라경고늦음_RTOS2차방어/  D_카메라오경보/  E_기타/
      맑은낮_40kmh_카메라인식_빙판25m앞정지/
        영상_1인칭+조감.mp4  영상_조감.mp4  영상_라이다.mp4  (영상_1인칭.mp4)
        사진_1접근_조감.jpg ... 사진_5정지_라이다.jpg
        정보.txt                                 이벤트 시각, 판정 주체, 인자
같은 주행을 다시 찍으면 새 파일로 바꾼다(최신 우선).      사용: organize_media.py [carla_demo 폴더]"""
import json, sys, os, pathlib, re, shutil

D = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2] / "logs/carla_demo")
OUT = D / "정리"; OUT.mkdir(parents=True, exist_ok=True)
for old in ("영상", "사진"):                       # 예전 평면 정리본 정리
    if (OUT / old).is_dir(): shutil.rmtree(OUT / old)
if (OUT / "목록.txt").exists(): (OUT / "목록.txt").unlink()

WEATHER = {"ClearNoon": "맑은낮", "CloudyNoon": "흐린낮", "WetNoon": "젖은노면_낮", "WetCloudyNoon": "젖은흐린낮",
           "SoftRainNoon": "보슬비_낮", "MidRainyNoon": "비_낮", "HardRainNoon": "폭우_낮",
           "ClearSunset": "맑은해질녘", "CloudySunset": "흐린해질녘", "WetSunset": "젖은해질녘", "WetCloudySunset": "젖은흐린해질녘",
           "SoftRainSunset": "보슬비_해질녘", "MidRainSunset": "비_해질녘", "HardRainSunset": "폭우_해질녘",
           "ClearNight": "맑은밤", "CloudyNight": "흐린밤", "WetNight": "젖은밤", "WetCloudyNight": "젖은흐린밤",
           "SoftRainNight": "보슬비_밤", "MidRainyNight": "비오는밤", "HardRainNight": "폭우_밤", "winter": "겨울흐림", "Snow": "눈"}
VIEW = {"split": "1인칭+조감", "bev": "조감", "lidar": "라이다", "lidar_sem": "라이다_클래스색", "front": "1인칭"}
SCENE = {"approach": "1접근", "warning": "2카메라경고", "enter": "3빙판진입", "slip": "4미끄러짐감지", "stop": "5정지", "timeline": "0타임라인"}
GROUP = {"인식정지": "A_1차방어_카메라인식_정지", "미인식": "B_2차방어_카메라미인식_IMU개입", "경고늦음": "C_2차방어_카메라경고늦음_IMU개입",
         "오경보": "D_카메라오경보", "기준선": "F_방어없음_기준선",
         # 1차를 **켜고도** 못 본 주행. 일부러 끈 주행(B)과 섞이면 도메인 갭이라는 가장 중요한
         # 음성 결과가 정리본에서 사라진다 (RSCD 질감 주행 2건이 그렇게 묻혀 있었다).
         "미탐지": "H_1차켜짐_그래도못봄_도메인갭",
         "실패": "E_기타", "시간초과": "E_기타", "기타": "E_기타"}

def situation(evs, args):
    kinds = {e["event"]: e for e in evs}
    warmup = float(args.get("warmup", 3.0))
    pw, pe, ss, st = kinds.get("primary_warning"), kinds.get("patch_enter"), kinds.get("secondary_slip"), kinds.get("stopped")
    who = None
    if ss: who = "RTOS" if ss.get("decided_by") == "stm32n6" else "호스트"
    if args.get("no_secondary"):
        col = kinds.get("collision")
        if col: return f"방어없음_빙판진입_{col.get('with','장애물')}충돌_{col.get('speed_kph',0):.0f}kmh", "기준선", None
        if "spin" in kinds: return "방어없음_빙판진입_스핀_제어상실", "기준선", None
        if "lane_departure" in kinds: return "방어없음_빙판진입_차선이탈", "기준선", None
        return "방어없음_빙판진입_" + ("정지" if st else "통과"), "기준선", None
    if pw and pw["t"] < warmup and st and not pe:
        return "카메라오경보_출발직후정지", "오경보", who
    if pw and float(pw.get("dist_to_edge_m", 0)) > 32.0 and st and not pe:      # 카메라 ROI(8~30m) 밖 — 빙판을 볼 수 없는 거리
        return f"카메라오경보_빙판{float(pw['dist_to_edge_m']):.0f}m밖에서정지", "오경보", who
    if pw and st and st.get("stopped_before_patch") and not ss:
        return f"카메라인식_빙판{st['dist_to_patch_m']:.0f}m앞정지", "인식정지", who
    if pw and ss:
        return f"카메라경고늦음_빙판진입_{who}2차방어", "경고늦음", who
    if not pw and ss:
        if args.get("disable_primary"):
            return f"1차끔_빙판진입_{who}2차방어", "미인식", who
        # 1차가 켜져 있었는데 한 번도 발화하지 않았다 — 가정이 아니라 실제 미탐지다.
        return f"1차켜짐_카메라가못봄_빙판진입_{who}2차방어", "미탐지", who
    if pw and pe and st and not ss:
        return "카메라경고늦음_빙판진입_제동만으로정지", "경고늦음", who
    if pe and not ss and not st:
        return "빙판진입_방어실패", "실패", who
    if "timeout" in kinds:
        return "시간초과", "시간초과", who
    return "기타", "기타", who

def place(src, dst):
    """하드링크(같은 파일시스템). 이미 같은 파일이면 건너뛰고, 다른(새) 파일이면 교체한다."""
    if dst.exists():
        try:
            if os.path.samefile(src, dst): return False
        except OSError: pass
        dst.unlink()
    try: os.link(src, dst)
    except OSError: shutil.copy2(src, dst)
    return True

rows, n_v, n_p = [], 0, 0
for ej in sorted(D.glob("events_*.json")):
    tag = ej.stem[len("events_"):]
    try: doc = json.load(open(ej))
    except Exception: continue
    args = doc.get("args", {}); evs = doc.get("events", [])
    if not evs: continue
    w = WEATHER.get(args.get("weather", ""), args.get("weather", "") or "날씨미상")
    kph = int(float(args.get("target_kph", 0)))
    sit, short, who = situation(evs, args)
    fusion = args.get("fusion", "local")
    name = f"{w}_{kph}kmh_{sit}"
    if int(args.get("traffic", 0) or 0) > 0: name += "_주변차량"
    got = sum((D / f"demo_{tag}_{view}.mp4").exists() for view in VIEW)
    if got == 0: continue                    # 영상이 지워진 옛 기록은 정리본에서 뺀다
    folder = OUT / GROUP[short] / name; folder.mkdir(parents=True, exist_ok=True)
    got = 0
    for view, vk in VIEW.items():
        src = D / f"demo_{tag}_{view}.mp4"
        if src.exists():
            got += 1
            if place(src, folder / f"영상_{vk}.mp4"): n_v += 1
    for ph in sorted((D / "photos").glob(f"{tag}__*__*.jpg")):
        m = re.match(rf"{re.escape(tag)}__(\w+)__(\w+)\.jpg", ph.name)
        if not m: continue
        scene, view = m.groups()
        if place(ph, folder / f"사진_{SCENE.get(scene, scene)}_{VIEW.get(view, view)}.jpg"): n_p += 1
    info = [f"주행: {name}", f"원본 태그: {tag}", f"날씨: {args.get('weather')}  목표 속도: {kph} km/h  마찰: {args.get('friction')}",
            f"1차 방어(카메라): {'꺼짐(미인식 가정)' if args.get('disable_primary') else ('보드 NPU 추론+융합' if fusion == 'n6npu' else '호스트')}",
            f"2차 방어(IMU): {'STM32N6 RTOS' if who == 'RTOS' else ('호스트' if who else '발동 안 함')}", "", "이벤트:"]
    info += [f"  {e['t']:>6.2f}s  {e['event']}  " + " ".join(f"{k}={v}" for k, v in e.items() if k not in ('t', 'event')) for e in evs]
    (folder / "정보.txt").write_text("\n".join(info) + "\n")
    rows.append((GROUP[short], name, tag, "; ".join(f"{e['t']}s {e['event']}" for e in evs), got))

(OUT / "00_읽어보기.txt").write_text("""IcePredict 데모 영상 정리본 — 폴더 안내
A_1차방어_카메라인식_정지      카메라(보드 NPU)가 빙판을 미리 보고 정지한 주행
B_2차방어_카메라미인식_IMU개입  카메라가 못 봤다고 **가정**(1차 끔) → 빙판 진입 → IMU 미끄러짐 감지(STM32N6 RTOS)가 개입
H_1차켜짐_그래도못봄_도메인갭    1차를 켜고 돌렸는데 실제로 한 번도 발화하지 않은 주행 — RSCD 질감 노면 등. 도메인 갭의 증거라 B와 반드시 구분한다
C_2차방어_카메라경고늦음_IMU개입 카메라가 봤지만 늦어(60 km/h·악천후) 진입 → 2차 방어 개입
D_카메라오경보                  빙판이 안 보이는 거리/정지 상태에서 경고 — 1차 방어의 한계 (밤·폭우)
F_방어없음_기준선               1차·2차 모두 없을 때 — 같은 조건에서 충돌/통과 결과 (RTOS 필요성의 근거)
G_비교_방어없음_vs_RTOS         왼쪽 방어 없음 / 오른쪽 RTOS 2차 방어 나란히 (같은 시각)
각 주행 폴더: 영상_1인칭+조감 / 영상_조감 / 영상_라이다(높이색 3D 점군+3D 박스) / 영상_라이다_클래스색(시맨틱) / 사진_*(접근·경고·진입·미끄러짐·정지) / 정보.txt
이름 규칙: 날씨_속도_실제결과[_주변차량].  결과는 이벤트 기록으로 자동 판정. 같은 주행을 다시 찍으면 최신본으로 바뀐다.
00_집계.md: 1차 인식률·오경보, 2차 발동·판정 주체·지연 표.
00_분석문서_목록.md: 번호 붙은 근거 문서 18편이 각각 무엇을 답하는지 + 읽는 순서 + 주의사항. 먼저 여기를 볼 것.
../figures/: 발표용 그림 — group_*(그룹 대표 모음), weather_*(날씨별 같은 순간 격자: 1차 경고 / 2차 미끄러짐 감지 / 기준선),
             lidar_gallery(시맨틱 라이다), triplet_*(카메라·2D 분할 라벨·라이다 3종, AI Hub 형식)
각 주행 폴더의 사진_0타임라인_1인칭+조감.jpg 는 주행 전체를 6장으로 요약한 한 장짜리 그림.
""")
with open(OUT / "00_목록.txt", "w") as f:
    f.write("그룹 | 주행 폴더 | 원본 태그 | 영상 수 | 이벤트\n")
    for g, n, t, e, got in sorted(rows): f.write(f"{g} | {n} | {t} | {got} | {e}\n")
print(f"정리: 주행 {len(rows)}건, 영상 {n_v}개·사진 {n_p}장 새로 연결 → {OUT}")
