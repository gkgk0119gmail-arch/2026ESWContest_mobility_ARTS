#!/usr/bin/env python3
"""결선 발표 자료 (8쪽) — 시연·발표 10 분용. 개발완료보고서(build_pptx.py)와 같은 디자인 시스템(pptx_kit.py)을 쓴다.

쪽마다 메시지 하나. 시연 세 장면(① RealSense 1차 · ② Moza 2차 · ③ 파이 강제 종료)은 해당 쪽 오른쪽 위 '시연' 표로 신호한다.
발표자 노트에 쪽별 시간과 말할 요지를 넣었다 (합계 10 분 = 발표 약 5 분 + 시연 약 5 분).

사용: python3 sw/report/build_present.py      → sw/report/2026ESWContest_모빌리티_ARTS_결선발표.pptx
"""
from __future__ import annotations

import pathlib

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from pptx_kit import (ABLUE, ARED, BODY, FAINT, FONT_X, HBLUE, INK, MUTED, PANEL, SKY, WHITE, autoplay, block,
                      crop_cover, hline, label, pic, px, rect, stat, table, text, veil, video)

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
A = HERE / "assets"; V = A / "video"; FIG = ROOT / "sw/docs/figures"; HW = ROOT / "hw/images"
OUT = HERE / "2026ESWContest_모빌리티_ARTS_결선발표.pptx"
W, H = 1280, 720; PAD = 64; CW = W - 2 * PAD; N = 8
REPO = "github.com/gkgk0119gmail-arch/2026ESWContest_mobility_ARTS"

prs = Presentation(); prs.slide_width, prs.slide_height = px(W), px(H); BLANK = prs.slide_layouts[6]; _n = [0]


def page(section, title, demo=None, color=HBLUE):
    _n[0] += 1
    s = prs.slides.add_slide(BLANK); sh = s.shapes
    label(sh, PAD, 36, 600, section, color=color)
    text(sh, PAD, 58, CW - (220 if demo else 0), 52, title, size=27 if demo else 28, color=INK, face=FONT_X, spacing=1.0)
    if demo:
        rect(sh, W - PAD - 190, 40, 190, 44, fill=ARED)
        text(sh, W - PAD - 190, 40, 190, 44, f"▶ 시연 {demo}", size=15, color=WHITE, bold=True, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)
    text(sh, PAD, 690, 900, 14, "팀 ARTS · IcePredict · " + REPO, size=8, color=FAINT)
    text(sh, W - PAD - 100, 689, 100, 14, f"{_n[0]} / {N}", size=9, color=FAINT, align=PP_ALIGN.RIGHT)
    return s, sh


# 1. 표지 ──────────────────────────────────────────────────────────────────────
_n[0] += 1
s = prs.slides.add_slide(BLANK); sh = s.shapes
rect(sh, 0, 0, 720, H, fill=HBLUE)
sh.add_picture(crop_cover(HW / "hw_moza_rig.jpg", 560, 720, 0.5, 0.5), px(720), px(0), px(560), px(H))
label(sh, 72, 90, 500, "제24회 임베디드SW경진대회 · 모빌리티 · 결선", color=SKY)
text(sh, 72, 140, 620, 90, "IcePredict", size=64, color=WHITE, face=FONT_X, spacing=1.0)
text(sh, 72, 240, 620, 90, "블랙아이스를 미리 보고,<br>놓치면 노면을 재서 제때 세운다", size=26, color=WHITE, bold=True, spacing=1.25)
hline(sh, 72, 560, 560, color=SKY, lw=0.5)
text(sh, 72, 580, 600, 30, "<b>팀 ARTS</b> · 남윤상 · 김진찬 · 이지성", size=13, color=WHITE)
text(sh, 72, 616, 620, 20, "STM32N6 · ThreadX · Raspberry Pi 5 · CARLA HIL · Moza", size=10, color=SKY)

# 2. 문제 ──────────────────────────────────────────────────────────────────────
s, sh = page("문제", "블랙아이스는 안 보이고, 지금 차는 마른 노면 기준으로 선다")
pic(sh, FIG, "real_failure_miss.jpg", PAD, 130, 640, 300, cap="정답이 블랙아이스인 실사진 — 카메라는 '정상'으로 봤다", cap_h=20)
stat(sh, PAD, 470, 300, 110, "3.4 %", "실사진 얼음 6,340장 중<br>어떤 문턱으로도 못 잡는 몫", color=ARED, num_size=40)
stat(sh, PAD + 330, 470, 310, 110, "38 → 105 m", "60 km/h 정지에 필요한 거리<br>마른 노면 → 블랙아이스 (μ 0.16)", color=HBLUE, num_size=40)
block(sh, PAD + 680, 130, CW - 680, 450, "지금 기술의 빈틈",
      ["<b>ABS · ESC</b> — 미끄러지는 바퀴를 잡는다. 노면 마찰을 다른 장치에 알리지 않는다.",
       "<b>긴급제동 · 간격 유지</b> — 앞차 거리로 제동 시점을 정한다. 마찰을 모르면 마른 노면 기준이라 빙판에서 늦다.",
       "<b>카메라</b> — 투명한 얼음은 아스팔트와 구분이 어렵다. 반드시 놓치는 경우가 남는다."],
      fill=PANEL, size=13, color=ARED, space_after=12)

# 3. 해법 ──────────────────────────────────────────────────────────────────────
s, sh = page("해법", "이중 방어 — 1차는 미리 보고, 2차는 재서 제때 세운다")
cw3 = (CW - 2 * 24) / 3
for i, (num, head, body, col) in enumerate([
        ("1차", "카메라가 미리 본다", "노면 4분류 + 반사도 + 기상 맥락 → 위험도. 빙판에 닿기 전에 경고·감속.", ABLUE),
        ("2차", "노면을 재서 제동 시점을 고친다", "카메라가 애매하거나 결빙 취약 구간이면 0.4 초 '노면 확인 탭'으로 마찰을 재고, 20 ms 마다 정지 거리를 다시 계산한다.", ARED),
        ("RTOS", "메인 컴퓨터가 죽어도 산다", "2차 판단은 리눅스와 분리된 안전 전용 RTOS 에서 돈다. 메인 프로그램이 멈춰도 제동 판단은 계속된다.", HBLUE)]):
    x = PAD + i * (cw3 + 24)
    rect(sh, x, 140, cw3, 300, fill=PANEL)
    text(sh, x + 24, 160, cw3 - 48, 50, num, size=34, color=col, face=FONT_X, spacing=1.0)
    text(sh, x + 24, 220, cw3 - 48, 40, head, size=17, color=INK, bold=True, spacing=1.1)
    text(sh, x + 24, 270, cw3 - 48, 160, body, size=12.5, color=BODY, spacing=1.45)
text(sh, PAD, 470, CW, 60, "조향은 운전자 몫이다. 시스템은 <b>경고 · 가속 제한 · 제동</b>만 돕는다 — 자율주행이 아니라 운전자 보조 장치다.",
     size=15, color=HBLUE, spacing=1.4)

# 4. 1차 방어 ──────────────────────────────────────────────────────────────────
s, sh = page("1차 방어", "카메라가 빙판을 미리 본다 — 실제 도로 사진 69,358장", demo="① RealSense", color=ABLUE)
sh.add_picture(str(A / "demo_res_1st.jpg"), px(PAD), px(130), px(560), px(315))
text(sh, PAD, 451, 560, 36, "RealSense 가 빙판 사진을 보면 CARLA 차가 빙판 앞에 선다 — 제동 여부는 RealSense 판정만으로 정한다", size=11, color=MUTED, spacing=1.3)
stat(sh, PAD, 500, 270, 100, "빙판 앞 정지", "블랙아이스 사진 → 감지 즉시 제동<br>(영상 7.9 m · 연습 31.6 m 앞)", color=ABLUE, num_size=30)
stat(sh, PAD + 290, 500, 270, 100, "경고 0", "마른 노면 사진 → 그대로 진행<br>(빙판에서 충돌)", color=HBLUE, num_size=30)
x2 = PAD + 600
stat(sh, x2, 140, 270, 120, "96.6 %", "블랙아이스 실사진 6,340장<br>경보율", color=ABLUE, num_size=46)
stat(sh, x2 + 290, 140, 270, 120, "0.3 %", "마른 노면 19,018장<br>오경보율 (젖은 노면 0.8 %)", color=HBLUE, num_size=46)
block(sh, x2, 300, CW - 600, 260, "어떻게",
      ["시뮬레이션 화면이 아니라 실제 도로 사진을 STM32N6 보드의 NPU 에 직접 넣어 쟀다.",
       "블랙아이스를 직접 맞히려 하지 않는다. '결빙 위험 확률 + 반사도 + 기상 맥락'으로 위험도를 올리고, 문턱(0.60)을 넘으면 경고한다."],
      fill=PANEL, size=13, color=ABLUE, space_after=10)

# 5. 2차 방어 ──────────────────────────────────────────────────────────────────
s, sh = page("2차 방어", "놓치면 — 노면을 재서 '언제부터 서야 하나'를 다시 계산한다", demo="② Moza", color=ARED)
steps = [("노면 확인 탭", "카메라가 애매하거나 영하의 결빙 취약 구간이면 브레이크를 0.4 초 살짝. 빙판은 감속이 거기서 막힌다 — 그 값이 마찰이다."),
         ("표 조회", "CARLA 에서 미리 잰 마찰별 감속 표로 정지 거리를 계산한다. 무거운 계산은 오프라인, 보드는 표만 본다."),
         ("20 ms 마다 판단", "앞 물체 거리와 비교해 경고 → 가속 금지 → 예비 제동 → 전력 제동. 남은 거리에 맞춰 앞차 4 m 뒤에 선다.")]
for i, (hd, bd) in enumerate(steps):
    y = 140 + i * 150
    text(sh, PAD, y, 90, 60, f"0{i + 1}", size=40, color=ARED, face=FONT_X, spacing=1.0, wrap=False)
    text(sh, PAD + 80, y + 4, 520, 30, hd, size=18, color=INK, bold=True)
    text(sh, PAD + 80, y + 40, 520, 100, bd, size=13, color=BODY, spacing=1.45)
pic(sh, FIG, "env_table.png", PAD + 640, 130, CW - 640, 300, cap="제동을 시작해야 하는 거리 (m) — 지금 차는 맨 아랫줄만 쓴다", cap_h=20)
table(sh, PAD + 640, 462, CW - 640, ["시연: 같은 지점(앞차 65 m)에서 브레이크 + 회피", "그때 속도", "결과"],
      [["2차 방어 OFF — 지금 차", "66 km/h", "<rb>17 km/h 충돌</rb>"],
       ["<b>2차 방어 ON — 미리 감속</b>", "40 km/h", "<gb>34 m 앞 정지</gb>"]],
      [2.3, 0.9, 1.1], size=12, row_h=36, head_h=30, first_col_color=ARED)
text(sh, PAD + 640, 580, CW - 640, 40, "<b>다른 것은 제동을 시작하는 지점이다.</b> 회피 조향은 비교를 위해 매번 같게 자동, 브레이크는 사람.", size=12, color=HBLUE, spacing=1.3)

# 6. 결과 ──────────────────────────────────────────────────────────────────────
s, sh = page("결과", "같은 코드, 마찰만 다르다 — 정지 2 / 9 → 8 / 9")
vw = 430; vh = round(vw * 1080 / 1280)
mv = video(sh, V / "v_envelope.mp4", V / "v_envelope.jpg", PAD, 128, vw, vh)
text(sh, PAD, 128 + vh + 4, vw, 34, "위: 마른 노면 기준 → 30 km/h 충돌 · 아래: 탭으로 마찰 측정 → 4.1 m 앞 정지", size=10, color=MUTED, spacing=1.3)
x2 = PAD + vw + 30; w2 = CW - vw - 30
stat(sh, x2, 140, (w2 - 20) / 2, 130, "2 / 9", "마른 노면 기준 — 정지<br>나머지 7회 충돌 (8 ~ 61 km/h)", color=ARED, num_size=54)
stat(sh, x2 + (w2 + 20) / 2, 140, (w2 - 20) / 2, 130, "8 / 9", "마찰 인식 — 정지<br>7회는 목표대로 앞차 4 m 앞", color=ABLUE, num_size=54)
pic(sh, FIG, "env_sweep.png", x2, 300, w2, 300, cap="마찰 3종 × 속도 3종 · CARLA 0.9.16 · 간격 유지 크루즈 주행 중 빙판 위 정지 차량", cap_h=20)
autoplay(s, [mv])

# 7. 안전 전용 RTOS ───────────────────────────────────────────────────────────────────
s, sh = page("안전 전용 RTOS", "메인 컴퓨터가 멈춰도 2차 방어는 산다", demo="③ 강제 종료", color=HBLUE)
vw = 560; vh = round(vw * 720 / 1280)
mv = video(sh, V / "v_island.mp4", V / "v_island.jpg", PAD, 128, vw, vh)
text(sh, PAD, 128 + vh + 4, vw, 20, "빙판 확인 직후 파이 메인 프로그램을 실제로 강제 종료 → 격리 코어의 2차 방어가 혼자 4.1 m 앞에 세운다", size=10, color=MUTED)
stat(sh, PAD, 470, 290, 110, "53 → 39 km/h", "곡선 빙판에서 파이 정지 시 충돌 속도<br>2차가 파이 → 2차가 안전 전용 RTOS", color=ABLUE, num_size=27)
stat(sh, PAD + 310, 470, 250, 110, "0.12 s", "메인 심장박동 끊김 감지<br>→ 축소 모드로 계속 판단", color=HBLUE, num_size=34)
x2 = PAD + vw + 30
table(sh, x2, 140, CW - vw - 30, ["전 코어 부하 · 20 ms × 6,000회", "최악 지연", "마감 놓침"],
      [["일반 리눅스 프로그램", "<rb>206 ms</rb>", "<rb>211</rb>"],
       ["<b>파이 격리 코어 (실시간 · RTOS 대체)</b>", "<gb>0.06 ms</gb>", "<gb>0</gb>"]],
      [2.6, 1, 1], size=12, row_h=40, head_h=30, first_col_color=HBLUE)
block(sh, x2, 290, CW - vw - 30, 290, "정직하게",
      ["설계는 STM32N6 + ThreadX 다. 예전에 보드에서 잰 2차 방어 최악 응답은 22.6 µs.",
       "이번 확장은 보드가 없어 <b>같은 C 코드</b>를 라즈베리파이 격리 코어에서 돌렸다. 리눅스 커널 자체가 멈추면 같이 멈춘다 — 그래서 실차는 별도 MCU 다."],
      fill=PANEL, size=12, color=HBLUE, space_after=10)
autoplay(s, [mv])

# 8. 정리 ──────────────────────────────────────────────────────────────────────
s, sh = page("정리", "미리 보고, 놓치면 재서, 죽어도 판단한다")
for i, (head, body, col) in enumerate([("미리 본다", "실사진 블랙아이스 경보율 96.6 %, 마른 노면 오경보 0.3 %", ABLUE),
                                       ("재서 제때 선다", "노면 확인 탭 + 마찰 인식 제동 시점 — CARLA 정지 2/9 → 8/9", ARED),
                                       ("죽어도 판단한다", "메인 컴퓨터가 멈춰도 2차 방어는 계속 — 충돌 53 → 39 km/h", HBLUE)]):
    y = 140 + i * 110
    rect(sh, PAD, y, 8, 86, fill=col)
    text(sh, PAD + 28, y + 4, 520, 34, head, size=22, color=INK, bold=True)
    text(sh, PAD + 28, y + 44, 900, 40, body, size=14, color=BODY)
block(sh, PAD, 480, 700, 150, "남은 것",
      ["· 마찰 측정 탭을 운전자가 느낀다 — 앞축 구동·뒷축 제동으로 느끼지 않게 재는 방법이 다음 단계",
       "· 결과는 CARLA 시뮬레이션 · 이번 확장의 실시간 측정은 파이 격리 코어 대체 · 1차 카메라 경보 거리는 해상도가 한계"],
      fill=PANEL, size=11.5, color=HBLUE, space_after=6)
rect(sh, PAD + 730, 480, CW - 730, 150, fill=HBLUE)
label(sh, PAD + 750, 494, CW - 770, "소스 코드 · 근거 문서 · 영상", color=SKY)
text(sh, PAD + 750, 520, CW - 770, 60, REPO, size=12, color=WHITE, bold=True, spacing=1.2)

NOTES = {
    1: "[0:00–0:20] IcePredict 입니다. 블랙아이스를 카메라로 미리 보고, 놓치면 노면을 직접 재서 제때 세우는 운전자 보조 장치입니다.",
    2: "[0:20–1:10] 블랙아이스는 투명해서 카메라가 놓치는 경우가 반드시 남습니다. 실사진 얼음의 3.4 % 는 어떤 문턱으로도 못 잡았습니다. 그리고 지금 차의 긴급제동·간격 유지는 마찰을 모르면 마른 노면 기준으로 제동 시점을 잡습니다. 60 km/h 에서 마른 노면은 38 m 면 서지만 블랙아이스는 105 m 가 필요합니다.",
    3: "[1:10–1:50] 그래서 두 겹으로 막습니다. 1차는 카메라로 미리 보고, 2차는 놓쳤을 때 노면을 재서 제동 시점을 고치고, 그 판단은 메인 컴퓨터와 분리된 안전 전용 RTOS에서 돕니다. 조향은 운전자 몫입니다.",
    4: "[1:50–3:10] (시연 ① — venue_desktop.sh first) 실사진 69,358장을 보드에 넣어 블랙아이스 96.6 % 를 경보했고 마른 노면 오경보는 0.3 % 입니다. 지금 차가 80 km/h 로 달립니다. 화면 아래 '빙판까지 ○ m'가 150 m 이상일 때 RealSense 에 블랙아이스 사진을 비추면 — 경고, 빙판 앞 정지. (시간이 되면) 마른 노면 사진으로 한 판 더: 경고 없이 그대로 빙판에 들어가 충돌. 제동 여부는 RealSense 판정 하나로만 정합니다.",
    5: "[3:10–5:40] (시연 ② — venue_desktop.sh assist, D 로 OFF/ON) 카메라가 놓치면 2차 방어가 브레이크를 0.4 초 살짝 밟아 마찰을 잽니다. 빙판은 감속이 거기서 막히고 그 값이 마찰입니다. 그 마찰로 정지 거리를 다시 계산해 20 ms 마다 제동 시점을 정합니다. 먼저 말씀드립니다 — 같은 조건에서 비교하려고 회피 조향은 매번 똑같이 자동으로 넣었고, 브레이크는 제가 직접 밟습니다. 우리 시스템은 조향에 개입하지 않고 제동 시점만 바꿉니다. OFF: 빨간 선에서 밟고 피해도 빙판에서 미끄러져 충돌(연습 17 km/h). ON: 탭 → 진동·경고 → 미리 감속 → 같은 지점에서 밟고 피하면 정지(연습 34 m 앞).",
    6: "[5:30–6:30] 같은 C 코드에 마찰 값만 다르게 넣은 CARLA 비교입니다. 마른 노면 기준은 9회 중 2회만 섰고, 마찰 인식은 8회 섰으며 그중 7회는 목표대로 앞차 4 m 앞이었습니다.",
    7: "[6:30–8:30] (시연 ③ — ② 의 ON 주행 중 빙판 경고 뒤 K 키) 2차 판단은 안전 전용 RTOS에서 돕니다. 곡선 빙판에서 파이를 멈추면 2차가 파이에 있을 때 53 km/h, 안전 전용 RTOS에 있을 때 39 km/h 로 충돌했습니다. 지금 주행 중에 파이 메인 프로그램을 실제로 죽여 보겠습니다. 정직하게 말씀드리면, 이번 확장은 보드가 없어 같은 C 코드를 파이 격리 코어에서 돌렸습니다.",
    8: "[8:30–10:00] 미리 보고, 놓치면 재서, 죽어도 판단합니다. 남은 과제는 운전자가 못 느끼게 마찰을 재는 방법과 보드 실측입니다. 소스와 근거 문서는 저장소에 모두 있습니다. 감사합니다.",
}
for i, sl in enumerate(prs.slides, 1):
    sl.notes_slide.notes_text_frame.text = NOTES[i]
assert _n[0] == N, _n[0]
prs.save(OUT); print(f"저장: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {N}쪽)")
