#!/usr/bin/env python3
"""개발완료보고서(20쪽) 를 편집 가능한 .pptx 로 만든다.

PDF 판(build_report.py)과 같은 내용·같은 눈금(1280×720 px)을 쓰되, 여기서는 글·표·사진이
전부 PowerPoint 개체다. 발표 전에 문구를 고치거나 쪽을 덜어내려면 이 파일을 쓴다.

사용: python3 sw/report/build_pptx.py
"""
from __future__ import annotations

import pathlib

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Pt

from pptx_kit import (veil, BLUE, BLUE_BG, DARK, FAINT, FONT, GREEN, INK, LINE, MONO,
                      MUTED, NAVY, PANEL, RED, RED_BG, SLATE, WHITE, ZEBRA,
                      box, pic, px, rect, table, text)

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "2026ESWContest_모빌리티_ARTS_개발완료보고서.pptx"

W, H = 1280, 720
PAD = 56
CW = W - 2 * PAD                      # 1168
REPO = "github.com/gkgk0119gmail-arch/2026ESWContest_mobility_ARTS"

prs = Presentation()
prs.slide_width, prs.slide_height = px(W), px(H)
BLANK = prs.slide_layouts[6]

_n = [0]
SEC_COLOR = {"1차 방어": BLUE, "2차 방어": RED}


def page(section, title, lead=None, foot=None):
    """머리글·바닥글을 그리고 본문에 쓸 수 있는 (위, 아래) y 를 돌려준다."""
    _n[0] += 1
    s = prs.slides.add_slide(BLANK)
    sh = s.shapes
    rect(sh, 0, 0, W, H, fill=WHITE)

    chip_w = 20 + len(section) * 13
    rect(sh, PAD, 44, chip_w, 23, fill=SEC_COLOR.get(section, DARK), radius=11)
    text(sh, PAD, 49, chip_w, 16, section, size=10, color=WHITE, bold=True,
         align=PP_ALIGN.CENTER, spacing=1.0)
    text(sh, PAD + chip_w + 13, 40, CW - chip_w - 90, 34, title, size=21, bold=True, spacing=1.0)
    text(sh, W - PAD - 70, 48, 70, 16, f"{_n[0]} / 20", size=9.5, color=FAINT,
         bold=True, align=PP_ALIGN.RIGHT, spacing=1.0)
    rect(sh, PAD, 84, CW, 1.6, fill=LINE)

    top = 98
    if lead:
        text(sh, PAD, top, CW, 20, lead, size=11, color=SLATE, bold=True, spacing=1.3)
        top += 28
    bottom = H - 52
    if foot:
        rect(sh, PAD, bottom - 44, CW, 1, fill=LINE)
        text(sh, PAD, bottom - 36, CW, 34, foot, size=8.6, color=MUTED, spacing=1.4)
        bottom -= 54
    return s, sh, top, bottom


# ══ 1. 표지 ══════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
sh = s.shapes
rect(sh, 0, 0, W, H, fill=NAVY)
pic(sh, HERE, "assets/cover.jpg", 0, 0, W, H, fit="cover", border=False)
veil(sh, 0, 0, W, H, opacity=0.70)          # 사진을 배경으로 눌러 글이 읽히게
veil(sh, 0, 0, 690, H, opacity=0.90)        # 왼쪽은 더 어둡게 — 글 영역
text(sh, 76, 120, 560, 20, "개발완료보고서", size=11.5, color=RED, bold=True, spacing=1.0)
text(sh, 76, 152, 600, 62, "IcePredict", size=44, color=WHITE, bold=True, spacing=1.0)
text(sh, 76, 216, 580, 84, "AI 예측과 RTOS 반응의<br>이중 안전망 블랙아이스 대응 시스템",
     size=24, color=WHITE, bold=True, spacing=1.3)
rect(sh, 76, 322, 92, 5, fill=RED)
text(sh, 76, 350, 580, 24, "AI가 노면을 미리 보고, 못 보더라도 RTOS가 차량 거동으로 확실히 잡는다.",
     size=13, color=FAINT, spacing=1.4)
text(sh, 76, 424, 560, 56,
     ["제24회 임베디드SW경진대회 · 자동차/모빌리티 부문 (현대자동차)",
      "<b>팀 ARTS</b> · 이지성 · 남윤상 · 김진찬"],
     size=11.5, color=FAINT, spacing=1.6, space_after=4)
text(sh, 76, 614, 620, 48,
     ["STM32N6570-DK · Neural-ART NPU · ThreadX · Raspberry Pi 5 + AI HAT+ 2 · CARLA HIL", REPO],
     size=9.5, color=MUTED, spacing=1.6)
_n[0] = 1                                  # 표지도 한 쪽이다 — 다음 쪽이 2/20 이 되게

# ══ 2. 한 장 요약 ════════════════════════════════════════════════════════════
s, sh, T, B = page("요약", "한 장 요약",
                   lead="AI가 노면을 미리 보고, 못 보더라도 RTOS가 차량 거동으로 확실히 잡는다.")
cw, gap = (CW - 2 * 13) / 3, 13
cards = [("문제", "블랙아이스는 <b>공개 데이터에 라벨이 없다.</b> 투명한 얼음은 아스팔트와 시각적으로 "
                  "구분되지 않는다. 카메라 한 겹으로는 반드시 놓치는 경우가 남는다.", BLUE_BG, BLUE),
         ("해법", "예측과 반응을 <b>한 보드에 두 겹</b>으로 올렸다. 1차는 NPU 비전이 빙판에 닿기 전에 "
                  "세우고, 1차가 놓치면 2차가 IMU 거동으로 미끄러짐을 확정해 받는다.", RED_BG, RED),
         ("증명", "실물 STM32N6 보드가 실제 펌웨어를 돌리고 CARLA 가 센서·물리를 제공하는 <b>HIL</b> 로 "
                  "주행 108회, 실사진 69,358장을 보드에 직접 넣어 측정했다.", PANEL, FAINT)]
for i, (t, b, bg, ac) in enumerate(cards):
    box(sh, PAD + i * (cw + gap), T, cw, 122, t, b, accent=ac, fill=bg, size=10.8, title_size=13)

kw = (CW - 3 * 13) / 4
kpis = [("96.1", "%", "실사진 블랙아이스 6,340장 정답률<br>경보율 96.6 %", BLUE, BLUE_BG),
        ("0.3", "%", "마른 노면 19,018장 오경보율<br>젖은 노면 0.8 %", BLUE, BLUE_BG),
        ("22.6", "µs", "2차 방어 최악 응답<br>리눅스는 5,790 µs", RED, RED_BG),
        ("9/9", "", "방어를 모두 끈 기준선<br>전부 제어 상실", RED, RED_BG)]
ky = T + 134
for i, (num, unit, lab, c, bg) in enumerate(kpis):
    x = PAD + i * (kw + 13)
    rect(sh, x, ky, kw, 96, fill=bg, line=LINE, lw=0.75, radius=9)
    text(sh, x, ky + 14, kw, 42, num + (f"<b>{unit}</b>" if unit else ""), size=31, color=c,
         bold=True, align=PP_ALIGN.CENTER, spacing=1.0)
    text(sh, x + 8, ky + 58, kw - 16, 34, lab, size=9.3, color=SLATE, bold=True,
         align=PP_ALIGN.CENTER, spacing=1.35)
pic(sh, HERE, "assets/cmp_15.0.jpg", PAD, ky + 108, CW, B - (ky + 108),
    cap="<b>같은 빙판 · 같은 속도 · 같은 주변 차량.</b> 왼쪽은 방어 없음 — 제어를 잃고 25.4 km/h 로 "
        "차로를 가로질러 미끄러진다. 오른쪽은 STM32N6 RTOS 2차 방어 — 차선 안에서 정지했다",
    fit="cover", cap_h=18)

# ══ 3. 문제 ══════════════════════════════════════════════════════════════════
s, sh, T, B = page("문제", "왜 블랙아이스는 비전만으로 풀리지 않는가",
                   foot="약점을 숨기지 않고, 이중 안전망 구조의 '존재 이유'를 데이터로 뒷받침한다 — "
                        "개발계획서에 적었던 방침을 그대로 지켰다.")
pic(sh, ROOT, "sw/docs/figures/real_failure_miss.jpg", PAD, T, CW, B - T - 124,
    cap="<b>우리 보드가 실제로 놓친 실사진</b> — 전부 정답이 블랙아이스인데 '정상'으로 판정했다. "
        "위험도 0.01~0.04 라 운영 문턱(0.603)은 물론 어떤 문턱으로도 잡히지 않는다", cap_h=18)
by = B - 112
for i, (t, b, ac, bg) in enumerate([
        ("라벨이 없다", "공개 데이터셋에 <b>'블랙아이스' 라벨이 없다.</b> RSCD 의 ice 57,262장도 다져진 "
                        "눈·서리에 가까운 '얼음 노면'일 뿐, 투명한 블랙아이스를 따로 구분하지 않는다.", FAINT, PANEL),
        ("원래 어려운 문제다", "투명·검은 얼음이 아스팔트와 시각적으로 구분되지 않는다는 것은 업계 통설이다. "
                        "우리 모델도 마찬가지로, 실사진 얼음 6,340장 중 <b>215장(3.4 %)</b>이 문턱 아래에 남는다.", FAINT, PANEL),
        ("그래서 내린 결론", "1차는 블랙아이스를 <b>직접 맞히려 하지 않는다.</b> '결빙 위험 노면 확률 + 반사도 "
                        "이상 + 기상 맥락'으로 위험도를 올린다. 그리고 <b>그 전략이 실패할 때를 위해 2차가 있다.</b>", RED, RED_BG)]):
    box(sh, PAD + i * (cw + gap), by, cw, 112, t, b, accent=ac, fill=bg, size=10.5, title_size=12)

# ══ 4. 구조 ══════════════════════════════════════════════════════════════════
s, sh, T, B = page("구조", "이중 안전망 — 예측(AI)과 반응(RTOS)",
                   foot="ThreadX 우선순위는 IMU 융합 스레드가 3, NPU 프레임 스레드가 4다. 25 ms짜리 추론이 "
                        "돌고 있어도 IMU 판정이 선점한다 — 혼합 임계도(mixed-criticality) AI ECU 구조.")
pic(sh, ROOT, "sw/docs/figures/architecture.png", PAD, T, CW, B - T - 196, border=False)
table(sh, PAD, B - 186, CW,
      ["", "1차 방어 · 예측", "2차 방어 · 반응"],
      [["센서", "전방 카메라", "IMU (횡가속 · yaw rate · 종가속)"],
       ["판단", "노면 4분류 + 반사도 + 기상 맥락 → 위험도", "칼만 필터 + 자전거 모델 잔차 → 미끄러짐 확정"],
       ["시점", "빙판에 닿기 <b>전</b>", "미끄러지기 <b>시작한 뒤</b>"],
       ["성격", "똑똑하지만 틀릴 수 있다", "단순하지만 제때 반드시 실행된다"],
       ["실행", "STM32N6 NPU · 25 ms", "STM32N6 + ThreadX · 최악 22.6 µs"]],
      colw=[1, 3.1, 3.4], size=10.5, row_h=28, head_h=24)

# ══ 5. 하드웨어 ══════════════════════════════════════════════════════════════
s, sh, T, B = page("구현", "하드웨어 구성")
iw = (CW - 3 * 11) / 4
shots = [("assets/hw_n6.jpg", "<b>STM32N6570-DK</b> + AI 카메라 MB1854B<br>왼쪽은 보조배터리, 위는 NVMe SSD", HERE),
         ("hw/images/hw_pi_stack.jpg", "<b>Raspberry Pi 5 16 GB + AI HAT+ 2</b><br>KKSB 케이스 · 액티브 쿨러", ROOT),
         ("hw/images/hw_d435i_nvme.jpg", "<b>RealSense D435i</b> (RGB·뎁스·IMU)<br>NVMe 1 TB SSD 외장", ROOT),
         ("assets/hw_moza.jpg", "<b>Moza 휠 · 페달</b> — 사람이 직접 CARLA 를 운전하는 경로", HERE)]
for i, (f, c, base) in enumerate(shots):
    pic(sh, base, f, PAD + i * (iw + 11), T, iw, 324, cap=c, fit="cover", cap_h=44)
table(sh, PAD, T + 342, CW, ["구분", "정식 명칭", "역할"],
      [["메인 보드", "<b>STM32N6570-DK</b> (MB1939-N6570-C02 + MB1860B)",
        "1차 NPU 추론(Neural-ART @ 1 GHz) + 2차 ThreadX 실시간 판정·제어. <b>두 임계도가 한 보드에 공존한다</b>"],
       ["카메라", "<b>ST AI Camera module MB1854B</b>", "1차 방어 입력. 보드에 직결되어 NPU 로 바로 들어간다"],
       ["호스트", "<b>Raspberry Pi 5 16 GB + AI HAT+ 2 (Hailo-10H)</b>", "기상 맥락 생성, ZMQ 브리지, 교차 검증용 NPU (대회 필수 보드)"],
       ["저장·전원", "<b>NVMe 1 TB SSD 외장 · 보조배터리</b>", "데이터셋·주행 로그 적재 / 콘센트 없이 같은 구성을 돌리기 위한 것"],
       ["센서·렌더", "<b>Intel RealSense D435i · RTX 5090</b>", "실측 IMU 노이즈 확보 / CARLA 0.9.16 물리·센서 제공"]],
      colw=[0.75, 2.5, 4.0], size=10.2, row_h=32, head_h=24)

# ══ 6. 계획 대비 변경 ════════════════════════════════════════════════════════
s, sh, T, B = page("구현", "개발계획서 대비 변경점과 그 이유")
table(sh, PAD, T, CW, ["항목", "개발계획서(6월)", "실제 구현", "바꾼 이유"],
      [["2차 방어 실행 환경", "Pi 5 + 리눅스", "<b>STM32N6 + ThreadX RTOS</b>",
        "리눅스 최악 깨어남 지연이 <b>5,790 µs</b>로 측정됐다. 제어 주기 20 ms의 29 %를 한 번의 지터가 "
        "먹는다. 평균이 아니라 <b>꼬리</b>가 안전 기능의 기준이다"],
       ["1차 NPU", "Hailo-10H 단독", "<b>Neural-ART(N6) 주 · Hailo 교차검증</b>",
        "Hailo Dataflow Compiler 를 확보하지 못해 int8 변환 경로를 열지 못했다. 1·2차를 한 보드에 "
        "올리는 편이 선점 구조를 증명하기에도 맞다"],
       ["검증 방법", "1/5 차량 저마찰 노면 실측", "<b>HIL (실물 보드 + CARLA) + 실사진 69,358장</b>",
        "실물 빙판을 재현 가능하게 만들기 어렵다. 대신 <b>펌웨어는 실물 보드에서 그대로 돌리고</b>, "
        "비전 성능은 시뮬 화면이 아닌 실제 도로 사진으로 측정했다"],
       ["노면 클래스", "정상 / 젖음 / 결빙 위험 / 포트홀", "동일 (유지)", "RSCD 27클래스 → 4클래스 매핑을 계획대로 적용"]],
      colw=[1.15, 1.5, 1.7, 4.2], size=9.8, row_h=50, head_h=22)
fy = T + 234
pic(sh, ROOT, "sw/docs/figures/rtos_latency.jpg", PAD, fy, (CW - 16) / 2, 206,
    cap="<b>1번 변경의 근거</b> — 같은 연산의 지연 분포. 리눅스는 꼬리가 길다", cap_h=16)
pic(sh, ROOT, "sw/docs/figures/schedule_inverted.jpg", PAD + (CW + 16) / 2, fy, (CW - 16) / 2, 206,
    cap="<b>2번 변경의 근거</b> — 우선순위를 뒤집으면 NPU 추론(25 ms)이 IMU 주기(20 ms)를 막는다", cap_h=16)
box(sh, PAD, fy + 216, CW, B - (fy + 216), "바뀌지 않은 것",
    "이중 안전망이라는 <b>구조</b>와, '블랙아이스를 직접 맞히지 않고 위험도를 올린다'는 <b>1차 전략</b>, "
    "'노면 라벨과 무관하게 차량 거동으로 확정한다'는 <b>2차 전략</b>은 계획서 그대로다. "
    "바뀐 것은 전부 <b>그 구조를 더 확실히 증명하기 위한 수단</b>이다.", size=10.5)

# ══ 7. 데이터 전략 ═══════════════════════════════════════════════════════════
s, sh, T, B = page("데이터", "데이터 전략 — 모델 헤드별 정답을 먼저 정의했다",
                   lead="데이터셋을 먼저 고르지 않았다. 각 헤드가 무엇을 정답으로 배워야 하는지를 정의한 뒤 "
                        "거기에 맞는 공개 데이터셋을 매핑했다.",
                   foot="데이터셋 그림은 각 공개 데이터셋의 논문·공식 사이트에서 가져온 설명 목적의 인용이며, "
                        "라이선스는 저장소 NOTICE 에 명시했다.")
hw_ = (CW - 16) / 2
table(sh, PAD, T, hw_, ["방어", "필요한 정답(label)", "데이터셋", "활용"],
      [["1차", "건조 / 젖음 / 눈 / 결빙 클래스", "<b>RSCD</b>", "주 학습 (사전학습)"],
       ["1차", "실측 수막 두께 (센서 GT)", "<b>RoadSaW</b>", "반사도 헤드 근거"],
       ["1차", "눈 · 비 · 안개 + 조도", "<b>AI Hub</b>", "국내 도로 도메인 검증"],
       ["2차", "실측 6축 IMU + 노면 라벨", "<b>PVS</b>", "칼만 공분산 설정"],
       ["2차", "RGB-IMU 동기화 · 악조건", "<b>ROAD</b>", "비교 기준선"],
       ["보조", "실제 폭설 주행 씬", "<b>CADC · WADS</b>", "야간 · 악천후 점검"],
       ["보조", "자동 라벨 (차선 가시성)", "<b>CARLA</b>", "합성 데이터 생성"]],
      colw=[0.6, 2.5, 1.2, 1.7], size=9.8, row_h=27, head_h=22)
box(sh, PAD + hw_ + 16, T, hw_, 215, "RSCD 27클래스 → 우리 4클래스",
    ["<g><b>정상</b></g> ← 건조 (dry)　　<b>젖음</b> ← 젖음 · 물 고임 (wet · water)",
     "<r><b>결빙 위험</b></r> ← 결빙 · 녹은 눈 (ice · melted snow)　　<b>포트홀</b> ← 요철 '심함' 라벨",
     "약 100만 장(공개 서브셋 37만 장) · 27클래스 = 마찰 6 × 재질 4 × 요철 3 · 베이징 약 700 km "
     "실도로 주행(2022) · <b>240×360 패치</b>라 NPU 입력 크기에 맞는다 · 눈·얼음은 결빙 57,262 · "
     "녹은 눈 64,263 · 신설 76,730장"],
    accent=BLUE, fill=BLUE_BG, size=10.5)
dy = T + 228
dw = (CW - 2 * 12) / 3
for i, (f, c) in enumerate([("sw/docs/images/ds_rscd_classes.jpg", "RSCD 클래스별 샘플"),
                            ("sw/docs/images/ds_rscd_patch.jpg", "주행 영상에서 노면 영역만 잘라 패치로 쓴다"),
                            ("sw/docs/images/ds_rscd_camera.jpg", "차량 전방 카메라 — 20~80 km/h 주행 촬영")]):
    pic(sh, ROOT, f, PAD + i * (dw + 12), dy, dw, B - dy, cap=c, fit="cover", cap_h=16)

# ══ 8. 보조 데이터셋 ═════════════════════════════════════════════════════════
s, sh, T, B = page("데이터", "반사도 · 국내 도로 · 실측 IMU · 폭설 — 보조 데이터셋 6종",
                   foot="⚠️ RoadSaW 에는 눈·얼음이 없고, ROAD 의 클래스는 노면 종류(아스팔트/블록/비포장)라 "
                        "결빙이 없다. 한계를 알고 역할을 나눠 썼다.")
six = [("ds_roadsaw.jpg", "<b>RoadSaW</b> 12클래스 = 노면 3종 × 젖음 4단계. MARWIS 로 수막 두께 실측 → 반사도 회귀의 근거. 패치 약 72만 장"),
       ("ds_aihub.jpg", "<b>AI Hub</b> 승용 자율주행차 악천후(71626). 카메라·라이다·레이더 + 2D 분할 라벨 + 3D 박스 — 우리 라벨 형식의 본보기"),
       ("ds_pvs.jpg", "<b>PVS</b> MPU-9250 IMU 100 Hz 를 대시보드·서스펜션 3곳에. 9세트 = 차량 3 × 운전자 3 × 경로 3"),
       ("ds_road.jpg", "<b>ROAD</b> 카메라 30 fps + IMU 5개 400 Hz 동기, 약 115만 프레임. 야간·폭우·먼지 악조건"),
       ("ds_cadc.jpg", "<b>CADC</b> 눈길 실주행 5.6만 장 · 라이다 7천 스윕 · 75개 장면 (캐나다 워털루)"),
       ("ds_wads.jpg", "<b>WADS</b> 미시간 폭설 라이다. '내리는 눈 / 쌓인 눈'을 포인트별 라벨(36억 점)")]
gw = (CW - 2 * 12) / 3
gh = (B - T - 12) / 2
for i, (f, c) in enumerate(six):
    pic(sh, ROOT, f"sw/docs/images/{f}", PAD + (i % 3) * (gw + 12), T + (i // 3) * (gh + 12),
        gw, gh, cap=c, fit="cover", cap_h=30, cap_size=8.8)

# ══ 9. 모델 ══════════════════════════════════════════════════════════════════
s, sh, T, B = page("1차 방어", "모델과 int8 배포 — RoadNet")
table(sh, PAD, T, hw_, ["항목", "값"],
      [["백본", "MobileNetV3-Small (ImageNet 사전학습)"],
       ["입력", "1×3×224×224 NCHW, int8"],
       ["출력", "4클래스 로짓 + <b>반사도 헤드</b> (576→64→1)"],
       ["내보내기", "ONNX opset 13, 고정 배치 1"],
       ["양자화", "int8 PTQ (QDQ), 가중치 채널별 · 활성 대칭"],
       ["입력 양자화", "scale 0.018658448, zero-point −14"],
       ["출력 양자화", "scale 0.029451849"],
       ["프레임당 전송", "150,528 B"],
       ["NPU", "Neural-ART @ 1 GHz · <b>추론 25 ms</b>"],
       ["카메라 ROI", "차량 전방 <b>7.9 ~ 42.2 m</b> (FOV 60°, 피치 −12°)"]],
      colw=[1, 2.5], size=10.5, row_h=44, head_h=26)
box(sh, PAD + hw_ + 16, T, hw_, 248, "반사도 헤드를 따로 둔 이유",
    "블랙아이스의 단서는 '무슨 노면인가'보다 <b>'빛을 어떻게 되돌리는가'</b>에 가깝다. 분류 로짓 하나로는 "
    "젖음과 결빙이 섞인다. RoadSaW 의 실측 수막 두께를 정답으로 반사도를 <b>회귀</b>로 따로 배우게 하고, "
    "융합 단계에서 분류 확률과 더한다.", accent=BLUE, fill=BLUE_BG)
box(sh, PAD + hw_ + 16, T + 262, hw_, 248, "양자화에서 겪은 것",
    "표준 PTQ 로는 stem 층의 활성 분포가 넓어 int8 에서 정확도가 떨어졌다. <b>stem 등화(equalization)와 "
    "클리핑</b>을 적용해 회복했고, QDQ 그래프를 u8→i8 로 다시 쓰는 변환을 거쳐 Neural-ART 가 받는 "
    "형태로 맞췄다.")

# ══ 10. 1차 성능 ═════════════════════════════════════════════════════════════
s, sh, T, B = page("1차 방어", "성능 — 실제 도로 사진 69,358장을 보드에 직접 넣었다",
                   lead="시뮬레이션 화면이 아니다. RSCD 실사진을 STM32N6 에 그대로 넣고 추론·융합을 전부 "
                        "보드에서 돌려 받은 판정이다.",
                   foot="경보율은 보드의 alarm 플래그가 아니라 risk ≥ 문턱으로 계산했다. 보드 융합기의 "
                        "히스테리시스(켜짐 0.441 / 꺼짐 0.341)는 연속 영상의 깜빡임을 막지만, 서로 무관한 "
                        "낱장 사진을 이어 넣으면 앞 사진 상태가 넘어와 경보율이 부풀려지기 때문이다.")
tw = CW * 0.44
table(sh, PAD, T, tw, ["실제 노면", "장수", "정답률", "경보율 (문턱 0.603)"],
      [["<b>블랙아이스</b>", "6,340", "<rb>96.1 %</rb>", "<rb>96.6 %</rb>"],
       ["마른 노면", "19,018", "80.7 %", "<gb>0.3 %</gb>"],
       ["젖은 노면", "34,440", "84.6 %", "<gb>0.8 %</gb>"],
       ["포트홀", "9,560", "90.2 %", "<gb>0.3 %</gb>"]],
      colw=[1.3, 0.9, 0.9, 1.6], size=11, row_h=46, head_h=28)
box(sh, PAD, T + 224, tw, B - (T + 224), "이 숫자가 왜 믿을 만한가",
    ["표본이 보드 바깥에서 만들어진 것이 아니다. 실사진을 <b>STM32N6 에 그대로 넣어</b> 추론·융합을 "
     "보드에서 돌리고, 돌아온 판정을 그대로 셌다.",
     "장당 판정을 <b>logs/rscd_board_samples.jsonl</b> 에 남겨 두어, 문턱을 바꿔 다시 계산할 때 "
     "보드를 다시 돌릴 필요가 없다."], accent=BLUE, fill=BLUE_BG, size=10.3)
fw = CW - tw - 16
fh2 = (B - T - 12) / 2
pic(sh, ROOT, "sw/docs/figures/real_threshold.jpg", PAD + tw + 16, T, fw, fh2,
    cap="운영 문턱 결정 — 69,358장 전량 스윕", cap_h=16)
pic(sh, ROOT, "sw/docs/figures/real_confusion.jpg", PAD + tw + 16, T + fh2 + 12, fw, fh2,
    cap="혼동 행렬 (행 = 실제, 열 = 보드 판정)", cap_h=16)

# ══ 11·14. 시나리오 3단 ══════════════════════════════════════════════════════
def scenario(section, title, lead, foot, steps):
    s, sh, T, B = page(section, title, lead=lead, foot=foot)
    w = (CW - 2 * 14) / 3
    main_h = w * 0.75                      # 4:3
    band_h = 86
    for i, (main, band, cap) in enumerate(steps):
        x = PAD + i * (w + 14)
        pic(sh, HERE, main, x, T, w, main_h, border=True)
        pic(sh, HERE, band, x, T + main_h + 6, w, band_h, fit="cover", border=True)
        text(sh, x, T + main_h + band_h + 16, w, B - (T + main_h + band_h + 16), cap,
             size=10, color=SLATE, spacing=1.45)


scenario("1차 방어", "동작 — 카메라가 보고 빙판 앞에서 선다",
         "CARLA Town04 · 맑은 낮 · 40 km/h · 마찰 0.02 · 1차 방어 ON (보드 NPU 추론 + 융합)",
         "같은 주행선에서 날씨 11종(맑음·흐림·젖은 노면·보슬비·폭우·해질녘·밤·비 오는 밤·눈 등)을 모두 "
         "돌렸다. 인식·정지 14회 · 경고 늦음 7회 · 미인식 10회 · 오경보 1회.",
         [("assets/p1_approach_c.jpg", "assets/p1_approach_b.jpg",
           "<b>① 접근</b><br>40 km/h 주행. 위는 전방 카메라(모델 입력), 아래는 조감.<br>위험도 0.00 · DRIVE"),
          ("assets/p1_warn_c.jpg", "assets/p1_warn_b.jpg",
           "<b>② 1차 경보 <r>6.10 s</r></b><br>위험도 <b>0.443</b> → 문턱 돌파. 빙판 가장자리까지 11.4 m.<br>PRIMARY WARNING · BRAKE"),
          ("assets/p1_stop_c.jpg", "assets/p1_stop_b.jpg",
           "<b>③ 정지 <r>7.14 s</r></b><br>빙판 <b>23.6 m 앞</b>에서 멈췄다.<br>빙판에 닿지 않았다 — 예방에 성공")])

# ══ 12. 융합 ═════════════════════════════════════════════════════════════════
s, sh, T, B = page("1차 방어", "위험도 융합 — 기상·위치 맥락으로 가중치를 바꾼다",
                   foot="운영 문턱 0.603 — 손으로 고른 값이 아니라 실사진 전량 스윕에서 결정했고, "
                        "logs/rscd_board_samples.jsonl 로 문턱을 바꿔 재계산할 수 있다.")
rect(sh, PAD, T, CW, 62, fill=INK, radius=8)
text(sh, PAD, T + 19, CW, 30, "risk  =  α · p_ice   +   β · 반사도   +   γ · (1 − 차선 가시성)",
     size=19, color=WHITE, bold=True, align=PP_ALIGN.CENTER, spacing=1.0)
ty = T + 78
table(sh, PAD, ty, hw_, ["기상 · 위치 맥락", "α (분류)", "β (반사도)", "γ (차선)"],
      [["교량 · 새벽 등 결빙 위험 높음", "0.35", "<b>0.45</b>", "0.20"],
       ["일반", "<b>0.50</b>", "0.30", "0.20"],
       ["저위험", "<b>0.55</b>", "0.15", "<b>0.30</b>"]],
      colw=[2.6, 1, 1, 1], size=11, row_h=52, head_h=28)
box(sh, PAD + hw_ + 16, ty, hw_, B - ty, "설계에서 고친 두 가지",
    ["<b>① 재정규화.</b> 쓸 수 없는 신호에 0을 넣으면 안 된다. 0은 중립값이 아니라 <b>최솟값</b>이라 "
     "위험도 상한이 잘린다. 그래서 쓸 수 없는 신호는 <b>분모에서 빼고</b> 남은 신호로 다시 정규화한다.",
     "<b>② 문턱 재교정.</b> 초기 0.441 에서는 젖은 노면 오경보가 40 %였다. 실사진 69,358장 스윕으로 "
     "절벽 구간을 찾아 <b>0.603</b> 으로 올렸고 오경보가 0.8 %로 내려갔다. 폭우는 강수 게이트로 따로 분리했다."],
    accent=RED, fill=RED_BG, size=10.5)

# ══ 13. 2차 원리 ═════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "원리 — 칼만 필터 + 자전거 모델 잔차",
                   foot="C 코어(sw/fw/npu_lib/slip_core.h)는 HAL·OS 비의존이라 호스트에서도 컴파일된다. "
                        "파이썬 참조 구현과 같은 판정을 내리는지 매 커밋 검증한다.")
lw_ = CW * 0.58
box(sh, PAD, T, lw_, 196, "판단 절차",
    ["1. IMU 50 Hz 에서 횡가속 a_y · yaw rate · 종가속을 받는다",
     "2. <b>2상태 칼만 필터</b>(값 · 변화율)로 잡음을 거른다 — Q = 2000/3000, R = 0.16/0.0005",
     "3. <b>자전거 모델</b>로 '이 속도·이 조향이면 나와야 할 yaw rate'를 계산한다 "
     "(고속 언더스티어 보정 1/(1+(v/v_ch)²), v_ch = 17 m/s)",
     "4. 실측과 모델의 <b>잔차</b>가 타원 밖이면 미끄러짐 — 3샘플(60 ms) 연속이면 확정"],
    accent=RED, fill=RED_BG, size=10.3)
table(sh, PAD, T + 208, lw_, ["항목", "값", "왜 이 값인가"],
      [["축거 L", "2.7 m", "자전거 모델"],
       ["조향 지연 τ", "0.06 s", "급조향 시 '모델이 즉답한다'는 가정이 <b>오탐</b>을 만들었다"],
       ["횡가속 임계", "0.30 g", ""],
       ["yaw 오차 임계", "0.35 rad/s", ""],
       ["판정 규칙", "<b>타원</b>", "직사각형은 모서리에서 저속 지연이 생겼다"],
       ["저마찰 트리거", "제동 ≥ 0.3 이 0.3 s 지속 + 감속 < 1.2 m/s²", "필터 지연 구간의 오탐 방지"]],
      colw=[1.1, 2.2, 2.8], size=9.8, row_h=31, head_h=22)
pic(sh, ROOT, "sw/docs/figures/rule_boundary.jpg", PAD + lw_ + 16, T, CW - lw_ - 16, B - T,
    cap="판정 경계 — 직사각형(점선) 대 타원(실선). 모서리에 걸리던 저속 구간이 타원에서 사라진다", cap_h=32)

# ══ 14. 2차 동작 ═════════════════════════════════════════════════════════════
scenario("2차 방어", "동작 — 카메라가 놓쳐도 보드가 받는다",
         "CARLA Town04 · 맑은 낮 · 40 km/h · 마찰 0.08 · 1차 방어 OFF · 2차 판정 주체 = STM32N6 보드",
         "2차 방어 발동 62회(보드 판정 53 · 호스트 9). 진입→확정 평균 2.41 s, 확정→정지 평균 3.78 s. "
         "비상 제어 모드 분포 — 차선유지 29 · 정지 37 · 최대제동 20 · 우회피 11 · 좌회피 11.",
         [("assets/p2_enter_c.jpg", "assets/p2_enter_b.jpg",
           "<b>① 빙판 진입 <r>5.84 s</r></b><br>1차를 끈 상태(미인식 가정). 38.7 km/h 로 그대로 들어간다.<br>"
           "위는 조감(차선 위치), 아래는 전방 카메라. 주변 차량 8대"),
          ("assets/p2_slip_c.jpg", "assets/p2_slip_b.jpg",
           "<b>② 미끄러짐 확정 <r>7.62 s</r></b><br><b>STM32N6 RTOS</b> 가 판정. a_y = −0.364 g, yaw 오차 −0.282<br>"
           "진입 1.78 s 만에 확정 → hard_stop"),
          ("assets/p2_stop_c.jpg", "assets/p2_stop_b.jpg",
           "<b>③ 차선 유지하며 정지 <r>11.68 s</r></b><br>왼쪽 차로가 비어(gap 999 m) evade_left 로 전환 후 정지.<br>"
           "차선 안에서 멈췄다 — 가로 미끄러짐도 충돌도 없다")])

# ══ 15. RTOS ═════════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "왜 리눅스가 아니라 RTOS 인가 — 평균이 아니라 꼬리",
                   lead="같은 연산을 Pi 5 리눅스가 평균 146배 빠르게 한다. 그런데도 RTOS 보드를 쓴다. "
                        "안전 기능의 기준은 평균이 아니라 최악이기 때문이다.",
                   foot="우선순위를 뒤집으면(NPU가 IMU보다 높으면) 25 ms 추론이 20 ms 주기를 막아 스케줄 "
                        "자체가 불가능해진다 — 근거: sw/docs/evidence/10_스케줄가능성_분석.md")
table(sh, PAD, T, hw_, ["플랫폼", "표본", "중앙값", "최악"],
      [["Pi 5 + Linux · 유휴", "20,000", "69 µs", "<rb>5,790 µs</rb>"],
       ["Pi 5 + Linux · 부하", "20,000", "68 µs", "<rb>5,429 µs</rb>"],
       ["STM32N6 + ThreadX (응답 전체)", "49,405", "12.4 µs", "<gb>22.6 µs</gb>"]],
      colw=[2.6, 1, 1, 1.2], size=10.5, row_h=32, head_h=24)
box(sh, PAD, T + 134, hw_, B - (T + 134), "이것이 왜 치명적인가",
    "제어 주기는 20 ms 다. 리눅스의 최악 지터 5,790 µs 는 <b>한 주기의 29 %</b>를 한 번에 먹는다. "
    "40 km/h 에서 5.8 ms 는 6.4 cm 지만, 미끄러짐이 시작된 뒤의 제어 루프에서는 그 한 번이 "
    "<b>차선 유지와 제어 상실을 가른다.</b>", accent=RED, fill=RED_BG)
rh = (B - T - 12) / 2
pic(sh, ROOT, "sw/docs/figures/latency_cdf.jpg", PAD + hw_ + 16, T, hw_, rh,
    cap="지연 분포 CDF — 리눅스는 꼬리가 길다", cap_h=16)
pic(sh, ROOT, "sw/docs/figures/schedule_rtos.jpg", PAD + hw_ + 16, T + rh + 12, hw_, rh,
    cap="RM 스케줄 — IMU(우선순위 3)가 NPU(4)를 선점한다", cap_h=16)

# ══ 16. 기준선 ═══════════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "방어가 없으면 — 기준선 9건 전부 제어를 잃었다",
                   lead="1차·2차를 모두 끈 채 같은 빙판·같은 속도·같은 주행선으로 들어갔다.",
                   foot="'스핀'은 차선 대비 방향 오차가 86°를 넘은 순간으로 정의했다. 실측값은 −88.4°에서 "
                        "−99.2° 사이로, 차체가 역방향을 본 것이 아니라 차로를 가로질러 돌아간 상태다. "
                        "주행별 이벤트는 sw/docs/data/events/events_*_nodefense*.json 에 그대로 있다.")
lw2 = CW * 0.58
pic(sh, HERE, "assets/base_spin_c.jpg", PAD, T, lw2, B - T - 158,
    cap="<b>차선 이탈 → 가로 미끄러짐</b>　12.02 s 이탈(횡오프셋 −1.45 m, 방향 오차 −41°) → 14.82 s 에 −99°. "
        "차체가 차로를 가로질러 90° 가까이 돌아 있다", cap_h=36)
pic(sh, HERE, "assets/base_wall_front.jpg", PAD, B - 142, lw2, 142, fit="cover",
    cap="<b>방호벽 충돌 순간의 전방 카메라</b>　11.42 s 이탈 → 12.22 s 에 −98° → 12.32 s 에 12.0 km/h 로 "
        "벽에 충돌. 화면이 방호벽으로 가득 찼다", cap_h=28)
rw = CW - lw2 - 16
box(sh, PAD + lw2 + 16, T, rw, 162, "결과",
    "<b>9건 전부</b> 차선을 벗어났고, 차체가 진행 방향과 <b>88~99° 어긋난 채</b> 가로로 미끄러졌다."
    "<br>그중 <b>5건</b>은 12~17 km/h 로 방호벽에 부딪혔다."
    "<br>주변 차량이 있던 <b>4건</b>은 충돌 전에 미끄러짐으로 끝났다.")
box(sh, PAD + lw2 + 16, T + 174, rw, B - (T + 174), "기준선의 가정",
    ["제어를 잃은 뒤에는 <b>운전자 입력을 모형화하지 않는다.</b> 비교 대상은 '우리 시스템이 개입하느냐'이지 "
     "'운전자가 얼마나 잘 대처하느냐'가 아니기 때문이다.",
     "그래서 차선 이탈이 확정되면 자율주행을 떼고 관성에 맡기며, 정지하거나 충돌하거나 "
     "4초가 지나면 주행을 끝낸다.",
     "실제 운전자는 제동을 시도하므로, 기준선의 결과는 <b>'아무 보조도 없을 때의 물리적 귀결'</b>로 "
     "읽어야 한다."], size=10.3)

# ══ 17. 직접 비교 ════════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "같은 조건 직접 비교 — 방어 없음 vs STM32N6 RTOS 2차 방어",
                   lead="같은 날씨 · 같은 주행선 · 같은 빙판 · 같은 주변 차량 8대. 차이는 2차 방어 하나뿐이다.",
                   foot="날씨 4종(맑은 낮 · 밤 · 젖은 노면 · 눈)에서 같은 비교를 만들었다. 전체 영상은 저장소 "
                        "sw/media/ 와 logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/ 에 있다.")
pic(sh, HERE, "assets/cmp_15.0.jpg", PAD, T, CW, B - T - 124, border=False)
box(sh, PAD, B - 114, hw_, 114, "왼쪽 · 방어 없음",
    "15.0 s · <b>25.4 km/h</b> · 12.0 s 에 차선을 이탈해 제어를 잃었고, 차체가 진행 방향과 어긋난 채 "
    "차로를 가로질러 미끄러지는 중이다. 뒤따르던 차량이 그대로 접근하고 있다.", size=10.3)
box(sh, PAD + hw_ + 16, B - 114, hw_, 114, "오른쪽 · STM32N6 RTOS 2차 방어",
    "13.2 s · <b>0.0 km/h</b> · <mono>IMU SLIP (lat_acc) a_y −0.36 g, yaw −0.28 → STOPPED</mono><br>"
    "차선 안에서, 빙판을 벗어난 지점에 정지했다.", accent=RED, fill=RED_BG, size=10.3)

# ══ 18. 센서 시각화 ══════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "센서 시각화와 자동 라벨 — 무엇을 보고 판단했는지 남긴다",
                   foot="주행 108회 전체의 이벤트 JSON 을 저장소에 함께 올렸다. CARLA 도 보드도 없이 "
                        "sw/scripts/analysis/summarize_runs.py 만으로 집계표를 재생성할 수 있다 — 손으로 적은 "
                        "숫자가 아니라는 증명이다.")
pic(sh, ROOT, "sw/docs/figures/weather_1차_카메라경고.jpg", PAD, T, hw_, B - T,
    cap="<b>날씨 11종에서 1차 경보가 난 같은 순간</b> — 맑음 · 흐림 · 젖은 노면 · 보슬비 · 폭우 · 해질녘 · "
        "밤 · 비 오는 밤 · 눈. 같은 주행선, 같은 빙판에서 날씨만 바꿔 돌렸다", cap_h=30)
rh2 = (B - T - 12) / 2
pic(sh, HERE, "assets/lidar_sem.jpg", PAD + hw_ + 16, T, hw_, rh2,
    cap="<b>시맨틱 라이다 64채널</b> — 청록이 빙판 구간, 초록 상자가 차량 3D 박스", cap_h=16)
pic(sh, ROOT, "sw/docs/figures/triplet_rscdtex_visual_00320.jpg", PAD + hw_ + 16, T + rh2 + 12, hw_, rh2,
    cap="<b>카메라 · 2D 분할 라벨 · 라이다</b> — 도로/차선/빙판/차량 라벨을 자동 생성한다 (AI Hub 형식 참고)", cap_h=16)

# ══ 19. 한계 ═════════════════════════════════════════════════════════════════
s, sh, T, B = page("한계", "한계와 실패 사례 — 숨기지 않고 남긴 것")
fails = [("assets/p1_approach_c.jpg", "① 경보 거리는 기하 문제다",
          "모델이 요구하는 최소 80 px 과 ROI 해상도 91 px 이 경보 거리를 정한다. 알고리즘이 아니라 "
          "<b>해상도</b>를 올리는 것이 먼저다."),
         ("assets/lim60_c.jpg", "② 2차는 예방이 아니다",
          "60 km/h. 1차가 경고했지만 제동이 늦어 진입했고, 2차가 0.26 s 만에 개입했는데도 "
          "<b>39.2 km/h 로 앞차와 충돌</b>했다."),
         ("assets/falsealarm_c.jpg", "③ 오경보도 난다",
          "밤. 빙판이 <b>41 m 밖</b>(ROI 7.9~42.2 m 바깥)에 있는데 어두운 노면을 얼음으로 보아 "
          "위험도 0.52 로 경보가 났다."),
         ("assets/domaingap_c.jpg", "④ 질감 도메인 갭",
          "RSCD 실사진 질감을 CARLA 노면에 입히면, 같은 모델이 <b>그 얼음에 반응하지 않는다.</b> "
          "재수집·재학습이 다음 과제다.")]
fw2 = (CW - 3 * 12) / 4
fh = B - T - 128
for i, (f, t, b) in enumerate(fails):
    x = PAD + i * (fw2 + 12)
    rect(sh, x, T, fw2, fh, fill=PANEL, line=LINE, lw=0.75, radius=8)
    pic(sh, HERE, f, x + 10, T + 10, fw2 - 20, (fw2 - 20) * 0.72, fit="cover")
    ty2 = T + 10 + (fw2 - 20) * 0.72 + 10
    text(sh, x + 10, ty2, fw2 - 20, 20, t, size=11.5, bold=True, spacing=1.1)
    text(sh, x + 10, ty2 + 24, fw2 - 20, T + fh - ty2 - 30, b, size=9.8, color=SLATE, spacing=1.45)
box(sh, PAD, B - 116, CW, 116, "그 밖에 남은 것",
    ["· <b>Hailo-10H 교차 검증</b>은 Dataflow Compiler 미확보로 열지 못했다. 1차는 STM32N6 Neural-ART 단독 결과다.",
     "· <b>D435i 실측 IMU 노이즈</b>는 PVS 공개 데이터로 대체했다. 실물 연결 측정은 남은 과제다.",
     "· <b>기준선은 제어 상실 후 운전자 입력을 모형화하지 않는다.</b> '아무 보조도 없을 때의 물리적 귀결'로 읽어야 한다.",
     "· 젖은 노면 경보율 0.8 %는 더 낮출 여지가 있다. β(반사도 가중치)와 문턱의 재조정이 필요하다."],
    size=10)

# ══ 20. 결론 ═════════════════════════════════════════════════════════════════
s, sh, T, B = page("결론", "결론 · 재현성 · 향후 계획",
                   foot="팀 ARTS · 이지성 · 남윤상 · 김진찬 — 제24회 임베디드SW경진대회 "
                        "자동차/모빌리티(현대자동차) 부문")
box(sh, PAD, T, hw_, 136, "무엇을 만들었나",
    "블랙아이스 라벨이 없다는 <b>데이터의 한계</b>에서 출발해, 카메라 한 겹에 기대지 않는 <b>이중 안전망</b>을 "
    "STM32N6 한 보드 위에 올렸다. 1차는 빙판에 닿기 전에 세우고, 1차가 틀려도 2차가 최악 22.6 µs 안에 "
    "반드시 받는다.", accent=BLUE, fill=BLUE_BG, size=10.3)
box(sh, PAD, T + 150, hw_, 194, "재현", "모든 숫자는 실측에서 <b>자동 생성</b>된다.", size=10.3)
rect(sh, PAD + 15, T + 214, hw_ - 30, 112, fill=INK, radius=6)
text(sh, PAD + 26, T + 226, hw_ - 52, 92,
     ["<mono>python3 sw/scripts/analysis/summarize_runs.py</mono>　# 집계표 재생성",
      "<mono>python3 -m pytest -q</mono>　# 단위 테스트 19개",
      "<mono>python3 sw/fw/npu_lib/test_slip_core.py</mono>　# C 코어 ↔ 파이썬 동치"],
     size=8.6, color=WHITE, spacing=1.5)
text(sh, PAD, T + 356, hw_, 20, "근거 문서 19건 · 결과 그림 29장 · 주행 이벤트 108건을 저장소에 함께 올렸다.",
     size=10, color=SLATE, spacing=1.3)
box(sh, PAD + hw_ + 16, T, hw_, 180, "향후 계획",
    ["1. <b>경보 거리 확대</b> — ROI 해상도를 올려 1차가 보는 거리를 늘린다. 2차 의존도를 낮추는 가장 직접적인 길이다",
     "2. <b>실사진 질감 재학습</b> — RSCD 질감을 CARLA 노면에 입혀 재수집하고 도메인 갭을 닫는다",
     "3. <b>Hailo-10H 교차 검증</b> — Dataflow Compiler 확보 후 동일 모델을 두 NPU 에서 비교",
     "4. <b>D435i 실측 IMU</b> — 실물 노이즈로 칼만 공분산을 재조정"],
    accent=RED, fill=RED_BG, size=10.3)
rect(sh, PAD + hw_ + 16, T + 192, hw_, 124, fill=INK, radius=8)
text(sh, PAD + hw_ + 32, T + 204, hw_ - 32, 16, "소스 코드", size=9.5, color=FAINT, bold=True, spacing=1.0)
text(sh, PAD + hw_ + 32, T + 224, hw_ - 32, 44,
     "github.com/gkgk0119gmail-arch/<br>2026ESWContest_mobility_ARTS",
     size=13, color=WHITE, bold=True, spacing=1.35)
text(sh, PAD + hw_ + 32, T + 284, hw_ - 32, 18,
     "MIT 라이선스 · 영상·그림·이벤트 데이터 포함 · README 에서 바로 재생된다",
     size=8.8, color=FAINT, spacing=1.2)
pic(sh, HERE, "assets/cmp_15.0.jpg", PAD, B - 118, CW, 118, fit="cover", border=False)

prs.save(OUT)
print(f"{OUT}  — {len(prs.slides.__iter__.__self__._sldIdLst)}쪽  "
      f"({OUT.stat().st_size // 1024} KB)")
