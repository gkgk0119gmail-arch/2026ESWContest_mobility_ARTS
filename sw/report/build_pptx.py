#!/usr/bin/env python3
"""개발완료보고서(20쪽) 를 편집 가능한 .pptx 로 만든다.

디자인은 현대자동차 브랜드 스타일가이드를 따른다(pptx_kit.py 머리글 참고).
주행 영상은 PowerPoint 가 재생하는 H.264 로 임베드되어 발표 모드에서 슬라이드가 열리면 자동 재생된다.
PDF(제출용)에는 영상 자리에 포스터 프레임이 들어간다.

사용: python3 sw/report/build_pptx.py
      bash    sw/report/to_pdf.sh
"""
from __future__ import annotations

import pathlib

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from pptx_kit import (ABLUE, ARED, BODY, FAINT, FONT_X, GREEN, HBLUE, INK, LINE, MONO, MUTED, PANEL, SKY,
                      WHITE, autoplay, block, crop_box, crop_cover, hline, label, pic, px, rect, stat, table,
                      text, veil, video)

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
A = HERE / "assets"
V = A / "video"
FIG = ROOT / "sw/docs/figures"
DS = ROOT / "sw/docs/images"
HW = ROOT / "hw/images"
OUT = HERE / "2026ESWContest_모빌리티_ARTS_개발완료보고서.pptx"

# ── PDF 용 빌드 (REPORT_PDF=1): PDF 에서는 영상이 재생되지 않으므로 영상 자리에 시간 순서의 장면 캡처 4컷을 넣는다 ──
import os as _os, subprocess as _sp, tempfile as _tf
PDF_MODE = _os.environ.get("REPORT_PDF") == "1"
if PDF_MODE:
    OUT = pathlib.Path(_tf.gettempdir()) / "IcePredict_report_pdfbuild.pptx"
    _CAP_DIR = pathlib.Path(_tf.mkdtemp(prefix="filmstrip_"))
    _video_real, _text_real = video, text

    def _filmstrip(mp4, w, h, k=4):
        """영상에서 k 장면을 뽑아 상자 비율에 가장 크게 들어가는 격자로 붙이고, 장면마다 번호·시각을 단다."""
        from PIL import Image, ImageDraw, ImageFont
        dur = float(_sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                            capture_output=True, text=True).stdout.strip())
        ts = [dur * f for f in (0.12, 0.40, 0.68, 0.94)][:k]
        frames = []
        for i, t_ in enumerate(ts):
            f = _CAP_DIR / f"{pathlib.Path(mp4).stem}_{i}.jpg"
            _sp.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t_:.2f}", "-i", str(mp4), "-frames:v", "1", str(f)], check=True)
            frames.append(Image.open(f).convert("RGB"))
        fw, fh = frames[0].size; S = 3                                      # 해상도 배율
        best = max(((r, k // r) for r in (1, 2, 4) if k % r == 0),
                   key=lambda rc: min(w / rc[1] / fw, h / rc[0] / fh))
        rows, cols = best; gap = 4
        cw_, ch_ = (w * S - gap * S * (cols - 1)) / cols, (h * S - gap * S * (rows - 1)) / rows
        sc = min(cw_ / fw, ch_ / fh); tw_, th_ = int(fw * sc), int(fh * sc)
        canvas = Image.new("RGB", (int(w * S), int(h * S)), (16, 20, 24)); d = ImageDraw.Draw(canvas)
        fnt = ImageFont.truetype(str(HERE / "fonts/Pretendard-Bold.otf"), int(13 * S)) if (HERE / "fonts/Pretendard-Bold.otf").exists() else None
        for i, im in enumerate(frames):
            r_, c_ = divmod(i, cols)
            x0 = int(c_ * (cw_ + gap * S) + (cw_ - tw_) / 2); y0 = int(r_ * (ch_ + gap * S) + (ch_ - th_) / 2)
            canvas.paste(im.resize((tw_, th_), Image.LANCZOS), (x0, y0))
            lab = f"{i + 1}  {ts[i]:.1f} s"
            d.rectangle((x0, y0, x0 + int(70 * S), y0 + int(20 * S)), fill=(0, 44, 95))
            d.text((x0 + int(6 * S), y0 + int(2 * S)), lab, font=fnt, fill=(255, 255, 255))
        out = _CAP_DIR / f"{pathlib.Path(mp4).stem}_strip.jpg"; canvas.save(out, quality=90)
        return str(out)

    def video(sh_tree, mp4, poster, x, y, w, h):
        return sh_tree.add_picture(_filmstrip(mp4, w, h), px(x), px(y), px(w), px(h))

    def _pdfcap(b):
        if isinstance(b, list): return [_pdfcap(z) for z in b]
        if not isinstance(b, str): return b
        if b.strip() == "▶ 발표 모드에서 자동 재생": return "■ 장면 캡처 4컷"
        b = b.replace("▶ 발표 모드에서 자동 재생", "■ 주요 장면 캡처 4컷 (영상은 저장소에서 재생)").replace("▶ 발표 모드 자동 재생", "■ 주요 장면 캡처 4컷")
        b = b.replace(". 발표 모드에서 자동 재생", ". 주요 장면 캡처 4컷").replace("▶ ", "■ ")
        return b

    def text(sh_tree, x, y, w, h, body, *a, **kw):
        return _text_real(sh_tree, x, y, w, h, _pdfcap(body), *a, **kw)

    def autoplay(slide, movies, loop=False):
        return None

W, H = 1280, 720
PAD = 56
CW = W - 2 * PAD                      # 1168
N_PAGES = 25
REPO = "github.com/gkgk0119gmail-arch/2026ESWContest_mobility_ARTS"
SEC_COLOR = {"1차 방어": ABLUE, "2차 방어": ARED}

prs = Presentation()
prs.slide_width, prs.slide_height = px(W), px(H)
BLANK = prs.slide_layouts[6]
_n = [0]


def page(section, title, lead=None, foot=None):
    """머리글(섹션 머리표 · 제목 · 쪽수)과 바닥글을 그리고, 본문에 쓸 (slide, shapes, 위 y, 아래 y) 를 돌려준다."""
    _n[0] += 1
    s = prs.slides.add_slide(BLANK)
    sh = s.shapes
    label(sh, PAD, 34, 600, section, color=SEC_COLOR.get(section, HBLUE))
    text(sh, W - PAD - 200, 33, 200, 16, f"{_n[0]:02d} / {N_PAGES}", size=9, color=FAINT, align=PP_ALIGN.RIGHT)
    text(sh, PAD, 52, CW, 40, title, size=24, color=INK, face=FONT_X, spacing=1.0)
    top = 108
    if lead:
        text(sh, PAD, 94, CW, 22, lead, size=11.5, color=BODY, spacing=1.2)
        top = 128
    bottom = 668
    if foot:
        bottom = 652
        text(sh, PAD, 660, CW, 32, foot, size=8.5, color=MUTED, spacing=1.3)
    text(sh, PAD, 698, 800, 14,
         "팀 ARTS · 제24회 임베디드SW경진대회 자동차/모빌리티(현대자동차) · " + REPO, size=8, color=FAINT)
    text(sh, W - PAD - 160, 697, 160, 14, "IcePredict", size=9, color=HBLUE, bold=True, align=PP_ALIGN.RIGHT)
    return s, sh, top, bottom


def still16(src, x, y, w, sh, num=None, color=HBLUE, cap=None, cap_h=0):
    """640×480 주행 장면을 16:9 로 잘라 붙이고, 왼쪽 위에 번호 표를 단다."""
    h = round(w * 9 / 16)
    sh.add_picture(crop_box(src, (0, 0.125, 1, 0.875)), px(x), px(y), px(w), px(h))
    if num:
        rect(sh, x, y, 30, 20, fill=color)
        text(sh, x, y + 1, 30, 18, num, size=9, color=WHITE, bold=True, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)
    if cap:
        text(sh, x, y + h + 5, w, cap_h, cap, size=9, color=MUTED, spacing=1.3)
    return h


def scene(section, title, lead, foot, clip, steps, stills):
    """동작 슬라이드: 왼쪽 큰 영상(자동 재생) + 그 아래 장면 3컷, 오른쪽 단계 설명."""
    s, sh, T, B = page(section, title, lead, foot)
    col = SEC_COLOR[section]
    vw, vh = 848, 318                                   # 1280×480 비율
    mv = video(sh, V / f"{clip}.mp4", V / f"{clip}.jpg", PAD, T, vw, vh)
    text(sh, PAD, T + vh + 4, vw, 14, "▶ 발표 모드에서 자동 재생 · 왼쪽 전방 카메라(NPU 입력) · 오른쪽 조감",
         size=8, color=FAINT)
    sw_ = (vw - 2 * 13) / 3
    sy = T + vh + 30
    for i, src in enumerate(stills):
        still16(src, PAD + i * (sw_ + 13), sy, sw_, sh, num=f"0{i + 1}", color=col)
    # 오른쪽 단계
    rx, rw = PAD + vw + 16, CW - vw - 16
    rect(sh, rx, T, rw, B - T, fill=PANEL)
    y = T + 18
    for i, (head, body) in enumerate(steps):
        text(sh, rx + 18, y, 44, 30, f"0{i + 1}", size=20, color=col, face=FONT_X, spacing=1.0, wrap=False)
        text(sh, rx + 58, y + 3, rw - 76, 22, head, size=11, color=INK, bold=True, spacing=1.0)
        text(sh, rx + 58, y + 26, rw - 76, 110, body, size=9.5, color=BODY, spacing=1.4)
        y += (B - T - 36) / 3
    autoplay(s, [mv])
    return s


# ═════════════════════════════════════════════════════════════════════════════
# 1. 표지 — 왼쪽 Hyundai Blue 면, 오른쪽 사람이 CARLA 를 운전하는 사진
# ═════════════════════════════════════════════════════════════════════════════
_n[0] += 1
s = prs.slides.add_slide(BLANK)
sh = s.shapes
rect(sh, 0, 0, 720, H, fill=HBLUE)
sh.add_picture(crop_cover(HW / "hw_moza_rig.jpg", 560, 720, 0.5, 0.5), px(720), px(0), px(560), px(H))
veil(sh, 720, 664, 560, 56, HBLUE, 0.82)
text(sh, 738, 672, 528, 40, "Moza 휠·페달로 사람이 CARLA 를 직접 운전하는 HIL 시연. 운전자가 바뀌어도 보드는 같은 판정을 한다.",
     size=9, color=WHITE, spacing=1.35)
label(sh, 72, 76, 500, "개발완료보고서 · 2026", color=SKY)
text(sh, 72, 128, 620, 90, "IcePredict", size=60, color=WHITE, face=FONT_X, spacing=1.0)
text(sh, 72, 222, 620, 90, "AI 예측과 RTOS 반응의<br>이중 안전망 블랙아이스 대응 시스템", size=23, color=WHITE,
     bold=True, spacing=1.25)
text(sh, 72, 322, 600, 24, "AI가 노면을 미리 보고, 못 보더라도 RTOS가 차량 거동으로 확실히 잡는다.", size=12.5,
     color=SKY, spacing=1.3)
hline(sh, 72, 548, 560, color=SKY, lw=0.5)
text(sh, 72, 566, 600, 44, ["제24회 임베디드SW경진대회 · 자동차/모빌리티 부문 (현대자동차)",
                           "<b>팀 ARTS</b> · 이지성 · 남윤상 · 김진찬"], size=11.5, color=WHITE, spacing=1.45)
text(sh, 72, 630, 620, 40, ["STM32N6570-DK · Neural-ART NPU · ThreadX · Raspberry Pi 5 + AI HAT+ 2 · CARLA HIL",
                           REPO], size=9, color=SKY, spacing=1.45)

# ═════════════════════════════════════════════════════════════════════════════
# 2. 한 장 요약
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("요약", "한 장 요약", lead="AI가 노면을 미리 보고, 못 보더라도 RTOS가 차량 거동으로 확실히 잡는다.")
cols = [("문제", "블랙아이스는 공개 데이터에 라벨이 없다. 투명한 얼음은 아스팔트와 시각적으로 구분되지 않는다. 카메라 한 겹으로는 반드시 놓치는 경우가 남는다."),
        ("해법", "예측과 반응을 한 보드에 두 겹으로 올렸다. 1차는 NPU 비전이 빙판에 닿기 전에 세우고, 1차가 놓치면 2차가 IMU 거동으로 미끄러짐을 확정해 받는다."),
        ("증명", "실물 STM32N6 보드가 실제 펌웨어를 돌리고 CARLA 가 센서·물리를 제공하는 HIL 로 주행 108회, 실사진 69,358장을 보드에 직접 넣어 측정했다.")]
cw3 = (CW - 2 * 32) / 3
for i, (t, b) in enumerate(cols):
    block(sh, PAD + i * (cw3 + 32), T, cw3, 100, t, b, size=10.5)
by = T + 112
rect(sh, PAD, by, CW, 104, fill=HBLUE)
for i, (num, cap) in enumerate([("96.1%", "실사진 블랙아이스 6,340장 정답률<br>경보율 96.6 %"),
                                ("0.3%", "마른 노면 19,018장 오경보율<br>젖은 노면 0.8 %"),
                                ("22.6 µs", "2차 방어 최악 응답<br>리눅스는 5,790 µs"),
                                ("9/9", "방어를 모두 끈 기준선<br>전부 제어 상실")]):
    x = PAD + 24 + i * (CW / 4)
    text(sh, x, by + 14, CW / 4 - 30, 46, num, size=32, color=WHITE, face=FONT_X, spacing=1.0)
    text(sh, x, by + 62, CW / 4 - 30, 36, cap, size=9, color=SKY, spacing=1.3)
vy = by + 118
vw_ = 760
vh_ = round(vw_ * 520 / 1288)
mv = video(sh, V / "v_compare_bev.mp4", V / "v_compare_bev.jpg", PAD, vy, vw_, vh_)
text(sh, PAD, vy + vh_ + 4, vw_, 14, "▶ 발표 모드에서 자동 재생 · 조감 비교 16 s", size=8, color=FAINT)
rx = PAD + vw_ + 24
text(sh, rx, vy, CW - vw_ - 24, 40, "같은 빙판 · 같은 속도<br>같은 주변 차량 8대", size=13, color=HBLUE, bold=True, spacing=1.2)
stat(sh, rx, vy + 52, 180, 70, "25.4 km/h", "왼쪽 · 방어 없음<br>제어를 잃고 차로를 가로질러 미끄러진다", color=ARED, num_size=22, cap_size=8.5)
stat(sh, rx + 196, vy + 52, 180, 70, "0.0 km/h", "오른쪽 · STM32N6 RTOS 2차 방어<br>차선 안에서 정지했다", color=ABLUE, num_size=22, cap_size=8.5)
text(sh, rx, vy + 136, CW - vw_ - 24, 160,
     "실물 STM32N6 보드가 ThreadX 위에서 IMU 거동으로 미끄러짐을 확정하고 제동·조향을 내린다. 차이는 2차 방어 하나뿐이다.",
     size=10, color=BODY, spacing=1.45)
autoplay(s, [mv])

# ═════════════════════════════════════════════════════════════════════════════
# 3. 문제
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("문제", "왜 블랙아이스는 비전만으로 풀리지 않는가",
                   foot="약점을 숨기지 않고, 이중 안전망 구조의 '존재 이유'를 데이터로 뒷받침한다 — 개발계획서에 적었던 방침을 그대로 지켰다.")
fw = 760
fh = round(fw * 680 / 1500)
sh.add_picture(str(FIG / "real_failure_miss.jpg"), px(PAD), px(T), px(fw), px(fh))
text(sh, PAD, T + fh + 6, fw, 34,
     "우리 보드가 실제로 놓친 실사진 — 전부 정답이 블랙아이스인데 '정상'으로 판정했다. 위험도 0.01~0.04 라 운영 문턱(0.603)은 물론 어떤 문턱으로도 잡히지 않는다",
     size=9, color=MUTED, spacing=1.3)
sy = T + fh + 56
stat(sh, PAD, sy, 240, 90, "3.4 %", "실사진 얼음 6,340장 중 215장이<br>어떤 문턱으로도 잡히지 않는다", color=ARED, num_size=30)
stat(sh, PAD + 260, sy, 240, 90, "0 장", "공개 데이터셋의 '블랙아이스' 라벨<br>RSCD ice 57,262장도 다져진 눈·서리에 가깝다", color=HBLUE, num_size=30)
stat(sh, PAD + 520, sy, 240, 90, "2 겹", "그래서 예측(AI)과 반응(RTOS)을<br>한 보드에 두 겹으로 올렸다", color=ABLUE, num_size=30)
rx, rw = PAD + fw + 32, CW - fw - 32
blocks = [("라벨이 없다", "공개 데이터셋에 '블랙아이스' 라벨이 없다. RSCD 의 ice 57,262장도 다져진 눈·서리에 가까운 '얼음 노면'일 뿐, 투명한 블랙아이스를 따로 구분하지 않는다."),
          ("원래 어려운 문제다", "투명·검은 얼음이 아스팔트와 시각적으로 구분되지 않는다는 것은 업계 통설이다. 우리 모델도 마찬가지로, 실사진 얼음 6,340장 중 215장(3.4 %)이 문턱 아래에 남는다."),
          ("그래서 내린 결론", "1차는 블랙아이스를 직접 맞히려 하지 않는다. '결빙 위험 노면 확률 + 반사도 이상 + 기상 맥락'으로 위험도를 올린다. 그리고 그 전략이 실패할 때를 위해 2차가 있다.")]
for (t_, b_), (y_, h_, f_) in zip(blocks, [(T, 128, None), (T + 142, 128, None), (T + 292, 176, PANEL)]):
    block(sh, rx, y_, rw, h_, t_, b_, size=10, fill=f_, color=HBLUE)

# ═════════════════════════════════════════════════════════════════════════════
# 4. 구조
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("구조", "이중 안전망 — 예측(AI)과 반응(RTOS)",
                   foot="ThreadX 우선순위는 IMU 융합 스레드가 3, NPU 프레임 스레드가 4다. 25 ms짜리 추론이 돌고 있어도 IMU 판정이 선점한다 — 혼합 임계도(mixed-criticality) AI ECU 구조.")
fw = 720
fh = round(fw * 948 / 2087)
sh.add_picture(str(FIG / "architecture.png"), px(PAD), px(T), px(fw), px(fh))
rx = PAD + fw + 40
stat(sh, rx, T + 6, 300, 92, "25 ms", "1차 방어 · STM32N6 Neural-ART NPU<br>노면 4분류 + 반사도 추론 한 프레임", color=ABLUE, num_size=30)
stat(sh, rx, T + 120, 300, 92, "22.6 µs", "2차 방어 · ThreadX 최악 응답<br>IMU 샘플 → 미끄러짐 판정 → 제어 출력", color=ARED, num_size=30)
stat(sh, rx, T + 234, 300, 110, "3 › 4", "ThreadX 우선순위 — IMU 융합 3, NPU 프레임 4<br>추론이 돌고 있어도 IMU 판정이 선점한다", color=HBLUE, num_size=30)
ty = T + fh + 22
table(sh, PAD, ty, CW, ["", "1차 방어 · 예측", "2차 방어 · 반응"],
      [["센서", "전방 카메라", "IMU (횡가속 · yaw rate · 종가속)"],
       ["판단", "노면 4분류 + 반사도 + 기상 맥락 → 위험도", "칼만 필터 + 자전거 모델 잔차 → 미끄러짐 확정"],
       ["시점", "빙판에 닿기 전", "미끄러지기 시작한 뒤"],
       ["성격", "똑똑하지만 틀릴 수 있다", "단순하지만 제때 반드시 실행된다"],
       ["실행", "STM32N6 NPU · 25 ms", "STM32N6 + ThreadX · 최악 22.6 µs"]],
      [1, 4.2, 4.2], size=10, row_h=25, head_h=24, head_colors=[HBLUE, ABLUE, ARED], first_col_color=HBLUE)

# ═════════════════════════════════════════════════════════════════════════════
# 5. 하드웨어
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("구현", "하드웨어 구성")
ph = 330
shots = [(HW / "hw_n6_bench.jpg", 420, 0.62, "<b>STM32N6570-DK</b> + AI 카메라 MB1854B — 왼쪽은 보조배터리, 위는 NVMe SSD"),
         (HW / "hw_pi_stack.jpg", 236, 0.5, "<b>Raspberry Pi 5 16 GB + AI HAT+ 2</b><br>KKSB 케이스 · 액티브 쿨러"),
         (HW / "hw_d435i_nvme.jpg", 236, 0.5, "<b>RealSense D435i</b> (RGB·뎁스·IMU)<br>NVMe 1 TB SSD 외장"),
         (HW / "hw_moza_rig.jpg", 236, 0.45, "<b>Moza 휠 · 페달</b> — 사람이 직접<br>CARLA 를 운전하는 경로")]
x = PAD
for p, w_, cy, cap in shots:
    sh.add_picture(crop_cover(p, w_, ph, 0.5, cy), px(x), px(T), px(w_), px(ph))
    text(sh, x, T + ph + 6, w_, 32, cap, size=9, color=MUTED, spacing=1.3)
    x += w_ + 12
ty = T + ph + 50
table(sh, PAD, ty, CW, ["구분", "정식 명칭", "역할"],
      [["메인 보드", "STM32N6570-DK (MB1939-N6570-C02 + MB1860B)", "1차 NPU 추론(Neural-ART @ 1 GHz) + 2차 ThreadX 실시간 판정·제어. 두 임계도가 한 보드에 공존한다"],
       ["카메라", "ST AI Camera module MB1854B", "1차 방어 입력. 보드에 직결되어 NPU 로 바로 들어간다"],
       ["호스트", "Raspberry Pi 5 16 GB + AI HAT+ 2 (Hailo-10H)", "기상 맥락 생성, ZMQ 브리지, 교차 검증용 NPU (대회 필수 보드)"],
       ["저장·전원", "NVMe 1 TB SSD 외장 · 보조배터리", "데이터셋·주행 로그 적재 / 콘센트 없이 같은 구성을 돌리기 위한 것"],
       ["센서·렌더", "Intel RealSense D435i · RTX 5090", "실측 IMU 노이즈 확보 / CARLA 0.9.16 물리·센서 제공"]],
      [1.1, 3.4, 5.2], size=9.5, row_h=28, head_h=24, first_col_color=HBLUE)

# ═════════════════════════════════════════════════════════════════════════════
# 6. 변경점
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("구현", "개발계획서 대비 변경점과 그 이유")
table(sh, PAD, T, CW, ["항목", "개발계획서(6월)", "실제 구현", "바꾼 이유"],
      [["2차 방어 실행 환경", "Pi 5 + 리눅스", "<b>STM32N6 + ThreadX RTOS</b>", "리눅스 최악 깨어남 지연이 <b>5,790 µs</b>로 측정됐다. 제어 주기 20 ms의 29 %를 한 번의 지터가 먹는다. 평균이 아니라 꼬리가 안전 기능의 기준이다"],
       ["1차 NPU", "Hailo-10H 단독", "<b>Neural-ART(N6) 주</b> · Hailo 교차검증", "Hailo Dataflow Compiler 를 확보하지 못해 int8 변환 경로를 열지 못했다. 1·2차를 한 보드에 올리는 편이 선점 구조를 증명하기에도 맞다"],
       ["검증 방법", "1/5 차량 저마찰 노면 실측", "<b>HIL</b> (실물 보드 + CARLA) + 실사진 69,358장", "실물 빙판을 재현 가능하게 만들기 어렵다. 대신 펌웨어는 실물 보드에서 그대로 돌리고, 비전 성능은 시뮬 화면이 아닌 실제 도로 사진으로 측정했다"],
       ["노면 클래스", "정상 / 젖음 / 결빙 위험 / 포트홀", "동일 (유지)", "RSCD 27클래스 → 4클래스 매핑을 계획대로 적용"]],
      [1.5, 1.7, 2.1, 5.2], size=9.5, row_h=56, head_h=24, first_col_color=HBLUE)
fy = T + 24 + 4 * 56 + 18
fh = 200
pic(sh, FIG, "rtos_latency.jpg", PAD, fy, 346, fh + 30, cap="1번 변경의 근거 — 같은 연산의 지연 분포. 리눅스는 꼬리가 길다", cap_h=26)
pic(sh, FIG, "schedule_inverted.jpg", PAD + 362, fy, 534, fh + 30,
    cap="2번 변경의 근거 — 우선순위를 뒤집으면 NPU 추론(25 ms)이 IMU 주기(20 ms)를 막는다", cap_h=26)
block(sh, PAD + 912, fy, CW - 912, fh + 30, "바뀌지 않은 것",
      "이중 안전망이라는 구조와, '블랙아이스를 직접 맞히지 않고 위험도를 올린다'는 1차 전략, '노면 라벨과 무관하게 차량 거동으로 확정한다'는 2차 전략은 계획서 그대로다. 바뀐 것은 전부 그 구조를 더 확실히 증명하기 위한 수단이다.",
      fill=PANEL, size=9.5)

# ═════════════════════════════════════════════════════════════════════════════
# 7. 데이터 전략
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("데이터", "데이터 전략 — 모델 헤드별 정답을 먼저 정의했다",
                   lead="데이터셋을 먼저 고르지 않았다. 각 헤드가 무엇을 정답으로 배워야 하는지를 정의한 뒤 거기에 맞는 공개 데이터셋을 매핑했다.",
                   foot="데이터셋 그림은 각 공개 데이터셋의 논문·공식 사이트에서 가져온 설명 목적의 인용이며, 라이선스는 저장소 NOTICE 에 명시했다.")
lw = 600
table(sh, PAD, T, lw, ["방어", "필요한 정답(label)", "데이터셋", "활용"],
      [["1차", "건조 / 젖음 / 눈 / 결빙 클래스", "<b>RSCD</b>", "주 학습 (사전학습)"],
       ["1차", "실측 수막 두께 (센서 GT)", "<b>RoadSaW</b>", "반사도 헤드 근거"],
       ["1차", "눈 · 비 · 안개 + 조도", "<b>AI Hub</b>", "국내 도로 도메인 검증"],
       ["2차", "실측 6축 IMU + 노면 라벨", "<b>PVS</b>", "칼만 공분산 설정"],
       ["2차", "RGB-IMU 동기화 · 악조건", "<b>ROAD</b>", "비교 기준선"],
       ["보조", "실제 폭설 주행 씬", "<b>CADC · WADS</b>", "야간 · 악천후 점검"],
       ["보조", "자동 라벨 (차선 가시성)", "<b>CARLA</b>", "합성 데이터 생성"]],
      [0.7, 2.5, 1.4, 1.8], size=9.5, row_h=24, head_h=24, first_col_color=HBLUE)
th = 24 + 7 * 24
block(sh, PAD + lw + 24, T, CW - lw - 24, th, "RSCD 27클래스 → 우리 4클래스",
      ["<b>정상</b> ← 건조 (dry)　　<b>젖음</b> ← 젖음 · 물 고임 (wet · water)",
       "<b>결빙 위험</b> ← 결빙 · 녹은 눈 (ice · melted snow)　　<b>포트홀</b> ← 요철 '심함' 라벨",
       "약 100만 장(공개 서브셋 37만 장) · 27클래스 = 마찰 6 × 재질 4 × 요철 3 · 베이징 약 700 km 실도로 주행(2022) · 240×360 패치라 NPU 입력 크기에 맞는다 · 눈·얼음은 결빙 57,262 · 녹은 눈 64,263 · 신설 76,730장"],
      fill=PANEL, size=9.5, space_after=6)
gy = T + th + 16
gw = (CW - 2 * 12) / 3
gh = B - gy - 26
for i, (f, c) in enumerate([("ds_rscd_classes.jpg", "RSCD 클래스별 샘플"),
                            ("ds_rscd_patch.jpg", "주행 영상에서 노면 영역만 잘라 패치로 쓴다"),
                            ("ds_rscd_camera.jpg", "차량 전방 카메라 — 20~80 km/h 주행 촬영")]):
    x = PAD + i * (gw + 12)
    rect(sh, x, gy, gw, gh, fill=PANEL)
    pic(sh, DS, f, x + 10, gy + 10, gw - 20, gh - 20)
    text(sh, x, gy + gh + 5, gw, 18, c, size=9, color=MUTED)

# ═════════════════════════════════════════════════════════════════════════════
# 8. 보조 데이터셋 6종
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("데이터", "반사도 · 국내 도로 · 실측 IMU · 폭설 — 보조 데이터셋 6종",
                   foot="⚠ RoadSaW 에는 눈·얼음이 없고, ROAD 의 클래스는 노면 종류(아스팔트/블록/비포장)라 결빙이 없다. 한계를 알고 역할을 나눠 썼다.")
six = [("ds_roadsaw.jpg", "<b>RoadSaW</b> 12클래스 = 노면 3종 × 젖음 4단계. MARWIS 로 수막 두께 실측 → 반사도 회귀의 근거. 패치 약 72만 장"),
       ("ds_aihub.jpg", "<b>AI Hub</b> 승용 자율주행차 악천후(71626). 카메라·라이다·레이더 + 2D 분할 라벨 + 3D 박스 — 우리 라벨 형식의 본보기"),
       ("ds_pvs.jpg", "<b>PVS</b> MPU-9250 IMU 100 Hz 를 대시보드·서스펜션 3곳에. 9세트 = 차량 3 × 운전자 3 × 경로 3"),
       ("ds_road.jpg", "<b>ROAD</b> 카메라 30 fps + IMU 5개 400 Hz 동기, 약 115만 프레임. 야간·폭우·먼지 악조건"),
       ("ds_cadc.jpg", "<b>CADC</b> 눈길 실주행 5.6만 장 · 라이다 7천 스윕 · 75개 장면 (캐나다 워털루)"),
       ("ds_wads.jpg", "<b>WADS</b> 미시간 폭설 라이다. '내리는 눈 / 쌓인 눈'을 포인트별 라벨(36억 점)")]
gw = (CW - 2 * 12) / 3
gh = (B - T - 14) / 2
ih = gh - 46
for i, (f, c) in enumerate(six):
    x = PAD + (i % 3) * (gw + 12)
    y = T + (i // 3) * (gh + 14)
    rect(sh, x, y, gw, ih, fill=PANEL)
    pic(sh, DS, f, x + 10, y + 10, gw - 20, ih - 20)
    text(sh, x, y + ih + 6, gw, 40, c, size=9, color=MUTED, spacing=1.3)

# ═════════════════════════════════════════════════════════════════════════════
# 9. 모델과 int8 배포
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("1차 방어", "모델과 int8 배포 — RoadNet")
lw = 560
table(sh, PAD, T, lw, ["항목", "값"],
      [["백본", "MobileNetV3-Small (ImageNet 사전학습)"],
       ["입력", "1×3×224×224 NCHW, int8"],
       ["출력", "4클래스 로짓 + 반사도 헤드 (576→64→1)"],
       ["내보내기", "ONNX opset 13, 고정 배치 1"],
       ["양자화", "int8 PTQ (QDQ), 가중치 채널별 · 활성 대칭"],
       ["입력 양자화", "scale 0.018658448, zero-point −14"],
       ["출력 양자화", "scale 0.029451849"],
       ["프레임당 전송", "150,528 B"],
       ["NPU", "Neural-ART @ 1 GHz · 추론 <b>25 ms</b>"],
       ["카메라 ROI", "차량 전방 <b>7.9 ~ 42.2 m</b> (FOV 60°, 피치 −12°)"]],
      [1, 2.8], size=9.5, row_h=25, head_h=24, first_col_color=HBLUE)
py_ = T + 24 + 10 * 25 + 16
ph_ = B - py_ - 26
sh.add_picture(crop_cover(HW / "hw_n6_bench.jpg", lw, ph_, 0.5, 0.60), px(PAD), px(py_), px(lw), px(ph_))
text(sh, PAD, py_ + ph_ + 5, lw, 18, "이 모델이 실제로 돌아가는 보드 — STM32N6570-DK 위의 AI 카메라 MB1854B 가 NPU 입력을 바로 공급한다",
     size=9, color=MUTED)
rx, rw = PAD + lw + 28, CW - lw - 28
block(sh, rx, T, rw, 150, "반사도 헤드를 따로 둔 이유",
      "블랙아이스의 단서는 '무슨 노면인가'보다 '빛을 어떻게 되돌리는가'에 가깝다. 분류 로짓 하나로는 젖음과 결빙이 섞인다. RoadSaW 의 실측 수막 두께를 정답으로 반사도를 회귀로 따로 배우게 하고, 융합 단계에서 분류 확률과 더한다.",
      size=10, color=ABLUE)
block(sh, rx, T + 160, rw, 150, "양자화에서 겪은 것",
      "표준 PTQ 로는 stem 층의 활성 분포가 넓어 int8 에서 정확도가 떨어졌다. stem 등화(equalization)와 클리핑을 적용해 회복했고, QDQ 그래프를 u8→i8 로 다시 쓰는 변환을 거쳐 Neural-ART 가 받는 형태로 맞췄다.",
      size=10, color=ABLUE)
# 배포 경로 4단계
py2 = T + 330
label(sh, rx, py2, rw, "배포 경로", color=HBLUE)
steps4 = [("학습", "PyTorch · RSCD 패치<br>MobileNetV3-Small"), ("내보내기", "ONNX opset 13<br>고정 배치 1"),
          ("양자화", "int8 PTQ (QDQ)<br>stem 등화 + 클리핑"), ("보드", "u8→i8 변환<br>Neural-ART @ 1 GHz")]
sw4 = (rw - 3 * 10) / 4
for i, (h_, b_) in enumerate(steps4):
    x = rx + i * (sw4 + 10)
    rect(sh, x, py2 + 26, sw4, B - py2 - 26, fill=PANEL)
    text(sh, x + 12, py2 + 38, sw4 - 24, 30, f"0{i + 1}", size=20, color=ABLUE, face=FONT_X, spacing=1.0)
    text(sh, x + 12, py2 + 72, sw4 - 24, 22, h_, size=11, color=INK, bold=True)
    text(sh, x + 12, py2 + 96, sw4 - 24, 60, b_, size=9, color=BODY, spacing=1.4)

# ═════════════════════════════════════════════════════════════════════════════
# 10. 성능
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("1차 방어", "성능 — 실제 도로 사진 69,358장을 보드에 직접 넣었다",
                   lead="시뮬레이션 화면이 아니다. RSCD 실사진을 STM32N6 에 그대로 넣고 추론·융합을 전부 보드에서 돌려 받은 판정이다.",
                   foot="경보율은 보드의 alarm 플래그가 아니라 risk ≥ 문턱으로 계산했다. 보드 융합기의 히스테리시스(켜짐 0.441 / 꺼짐 0.341)는 연속 영상의 깜빡임을 막지만, 서로 무관한 낱장 사진을 이어 넣으면 앞 사진 상태가 넘어와 경보율이 부풀려지기 때문이다.")
lw = 560
table(sh, PAD, T, lw, ["실제 노면", "장수", "정답률", "경보율 (문턱 0.603)"],
      [["블랙아이스", "6,340", "<b>96.1 %</b>", "<rb>96.6 %</rb>"],
       ["마른 노면", "19,018", "80.7 %", "<gb>0.3 %</gb>"],
       ["젖은 노면", "34,440", "84.6 %", "<gb>0.8 %</gb>"],
       ["포트홀", "9,560", "90.2 %", "<gb>0.3 %</gb>"]],
      [1.4, 1, 1, 1.6], size=10, row_h=28, head_h=24, first_col_color=HBLUE)
sy = T + 24 + 4 * 28 + 18
stat(sh, PAD, sy, 180, 80, "96.6 %", "블랙아이스 6,340장<br>경보율", color=ARED, num_size=28)
stat(sh, PAD + 190, sy, 180, 80, "0.3 %", "마른 노면 19,018장<br>오경보율", color=HBLUE, num_size=28)
stat(sh, PAD + 380, sy, 180, 80, "0.8 %", "젖은 노면 34,440장<br>오경보율", color=HBLUE, num_size=28)
cy_ = sy + 96
pic(sh, FIG, "real_confusion.jpg", PAD, cy_, lw, B - cy_, cap="혼동 행렬 (행 = 실제, 열 = 보드 판정)", cap_h=18)
rx, rw = PAD + lw + 28, CW - lw - 28
fh = round(500 * 690 / 1110)
pic(sh, FIG, "real_threshold.jpg", rx, T, rw, fh + 24, cap="운영 문턱 결정 — 69,358장 전량 스윕. 절벽 구간을 찾아 0.603 으로 정했다", cap_h=18)
block(sh, rx, T + fh + 40, rw, B - (T + fh + 40), "이 숫자가 왜 믿을 만한가",
      ["표본이 보드 바깥에서 만들어진 것이 아니다. 실사진을 STM32N6 에 그대로 넣어 추론·융합을 보드에서 돌리고, 돌아온 판정을 그대로 셌다.",
       "장당 판정을 <mono>logs/rscd_board_samples.jsonl</mono> 에 남겨 두어, 문턱을 바꿔 다시 계산할 때 보드를 다시 돌릴 필요가 없다."],
      fill=PANEL, size=9.5, color=ABLUE)

# ═════════════════════════════════════════════════════════════════════════════
# 11. 1차 동작 (영상)
# ═════════════════════════════════════════════════════════════════════════════
scene("1차 방어", "동작 — 카메라가 보고 빙판 앞에서 선다",
      "CARLA Town04 · 맑은 낮 · 40 km/h · 마찰 0.02 · 1차 방어 ON (보드 NPU 추론 + 융합)",
      "같은 주행선에서 날씨 11종(맑음·흐림·젖은 노면·보슬비·폭우·해질녘·밤·비 오는 밤·눈 등)을 모두 돌렸다. 인식·정지 14회 · 경고 늦음 7회 · 미인식 10회 · 오경보 1회.",
      "v_primary",
      [("접근", "40 km/h 주행. 왼쪽은 전방 카메라(모델 입력), 오른쪽은 조감.<br>위험도 0.00 · <b>DRIVE</b>"),
       ("1차 경보 <a>6.10 s</a>", "위험도 0.443 → 문턱 돌파. 빙판 가장자리까지 11.4 m.<br><b>PRIMARY WARNING · BRAKE</b>"),
       ("정지 <a>7.14 s</a>", "빙판 23.6 m 앞에서 멈췄다.<br>빙판에 닿지 않았다 — <b>예방에 성공</b>")],
      [A / "p1_approach_c.jpg", A / "p1_warn_c.jpg", A / "p1_stop_c.jpg"])

# ═════════════════════════════════════════════════════════════════════════════
# 12. 위험도 융합
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("1차 방어", "위험도 융합 — 기상·위치 맥락으로 가중치를 바꾼다",
                   foot="운영 문턱 0.603 — 손으로 고른 값이 아니라 실사진 전량 스윕에서 결정했고, logs/rscd_board_samples.jsonl 로 문턱을 바꿔 재계산할 수 있다.")
rect(sh, PAD, T, CW, 56, fill=HBLUE)
text(sh, PAD, T, CW, 56, "risk  =  <x>α</x> · p_ice   +   <x>β</x> · 반사도   +   <x>γ</x> · (1 − 차선 가시성)", size=17,
     color=WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, spacing=1.0)
ty = T + 72
lw = 560
table(sh, PAD, ty, lw, ["기상 · 위치 맥락", "α (분류)", "β (반사도)", "γ (차선)"],
      [["교량 · 새벽 등 결빙 위험 높음", "0.35", "<b>0.45</b>", "0.20"],
       ["일반", "<b>0.50</b>", "0.30", "0.20"],
       ["저위험", "<b>0.55</b>", "0.15", "0.30"]],
      [2.2, 1, 1, 1], size=10, row_h=28, head_h=24, first_col_color=HBLUE)
block(sh, PAD + lw + 28, ty, CW - lw - 28, 24 + 3 * 28 + 60, "설계에서 고친 두 가지",
      ["<b>① 재정규화.</b> 쓸 수 없는 신호에 0을 넣으면 안 된다. 0은 중립값이 아니라 최솟값이라 위험도 상한이 잘린다. 그래서 쓸 수 없는 신호는 분모에서 빼고 남은 신호로 다시 정규화한다.",
       "<b>② 문턱 재교정.</b> 초기 0.441 에서는 젖은 노면 오경보가 40 %였다. 실사진 69,358장 스윕으로 절벽 구간을 찾아 0.603 으로 올렸고 오경보가 0.8 %로 내려갔다. 폭우는 강수 게이트로 따로 분리했다."],
      size=9.5, color=ABLUE)
wy = ty + 24 + 3 * 28 + 24
label(sh, PAD, wy, 600, "맥락 계층이 보는 입력 — 같은 빙판 · 같은 주행선, 날씨만 바꿨다", color=HBLUE)
wy += 26
ww = (CW - 3 * 12) / 4
wh = B - wy
for i, (f, c) in enumerate([("w_clear.jpg", "맑은 낮"), ("w_rainnight.jpg", "비 오는 밤"), ("w_snow.jpg", "눈"), ("w_heavyrain.jpg", "폭우 낮")]):
    x = PAD + i * (ww + 12)
    sh.add_picture(crop_cover(crop_box(A / f, (0, 0.085, 1, 0.885)), ww, wh, 0.5, 0.5), px(x), px(wy), px(ww), px(wh))
    veil(sh, x, wy + wh - 26, 112, 26, HBLUE, 0.85)
    text(sh, x + 10, wy + wh - 26, 102, 26, c, size=9, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)

# ═════════════════════════════════════════════════════════════════════════════
# 13. 2차 원리
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "원리 — 칼만 필터 + 자전거 모델 잔차",
                   foot="C 코어(sw/fw/npu_lib/slip_core.h)는 HAL·OS 비의존이라 호스트에서도 컴파일된다. 파이썬 참조 구현과 같은 판정을 내리는지 매 커밋 검증한다.")
lw = 680
block(sh, PAD, T, lw, 172, "판단 절차",
      ["<rb>1</rb>  IMU 50 Hz 에서 횡가속 a_y · yaw rate · 종가속을 받는다",
       "<rb>2</rb>  2상태 칼만 필터(값 · 변화율)로 잡음을 거른다 — Q = 2000/3000, R = 0.16/0.0005",
       "<rb>3</rb>  자전거 모델로 '이 속도·이 조향이면 나와야 할 yaw rate'를 계산한다 (고속 언더스티어 보정 1/(1+(v/v_ch)²), v_ch = 17 m/s)",
       "<rb>4</rb>  실측과 모델의 잔차가 <b>타원 밖</b>이면 미끄러짐 — 3샘플(60 ms) 연속이면 확정"],
      fill=PANEL, size=9.5, color=ARED, space_after=3)
ty = T + 186
table(sh, PAD, ty, lw, ["항목", "값", "왜 이 값인가"],
      [["축거 L", "2.7 m", "자전거 모델"],
       ["조향 지연 τ", "<b>0.06 s</b>", "급조향 시 '모델이 즉답한다'는 가정이 오탐을 만들었다"],
       ["횡가속 임계", "0.30 g", ""],
       ["yaw 오차 임계", "0.35 rad/s", ""],
       ["판정 규칙", "<b>타원</b>", "직사각형은 모서리에서 저속 지연이 생겼다"],
       ["저마찰 트리거", "제동 ≥ 0.3 이 0.3 s 지속 + 감속 < 1.2 m/s²", "필터 지연 구간의 오탐 방지"]],
      [1.4, 2.6, 4], size=9.5, row_h=27, head_h=24, first_col_color=HBLUE)
sy = ty + 24 + 6 * 27 + 40
stat(sh, PAD, sy, 210, 70, "50 Hz", "IMU 입력 주기", color=ARED, num_size=24)
stat(sh, PAD + 230, sy, 210, 70, "60 ms", "3샘플 연속이어야 확정", color=ARED, num_size=24)
stat(sh, PAD + 460, sy, 210, 70, "22.6 µs", "샘플 → 판정 → 제어, 최악", color=ARED, num_size=24)
rx, rw = PAD + lw + 28, CW - lw - 28
pic(sh, FIG, "rule_boundary.jpg", rx, T, rw, B - T,
    cap="판정 경계 — 직사각형(점선) 대 타원(실선). 모서리에 걸리던 저속 구간이 타원에서 사라진다", cap_h=34)

# ═════════════════════════════════════════════════════════════════════════════
# 14. 2차 동작 (영상)
# ═════════════════════════════════════════════════════════════════════════════
scene("2차 방어", "동작 — 카메라가 놓쳐도 보드가 받는다",
      "CARLA Town04 · 맑은 낮 · 40 km/h · 마찰 0.08 · 1차 방어 OFF · 2차 판정 주체 = STM32N6 보드",
      "2차 방어 발동 62회(보드 판정 53 · 호스트 9). 진입→확정 평균 2.41 s, 확정→정지 평균 3.78 s. 비상 제어 모드 분포 — 차선유지 29 · 정지 37 · 최대제동 20 · 우회피 11 · 좌회피 11.",
      "v_secondary",
      [("빙판 진입 <r>5.84 s</r>", "1차를 끈 상태(미인식 가정). 38.7 km/h 로 그대로 들어간다. 왼쪽은 전방 카메라, 오른쪽은 조감(차선 위치). 주변 차량 8대"),
       ("미끄러짐 확정 <r>7.62 s</r>", "<b>STM32N6 RTOS</b> 가 판정. a_y = −0.364 g, yaw 오차 −0.282<br>진입 1.78 s 만에 확정 → <b>hard_stop</b>"),
       ("차선 유지하며 정지 <r>11.68 s</r>", "왼쪽 차로가 비어(gap 999 m) evade_left 로 전환 후 정지.<br>차선 안에서 멈췄다 — 가로 미끄러짐도 충돌도 없다")],
      [A / "p2_enter_c.jpg", A / "p2_slip_c.jpg", A / "p2_stop_c.jpg"])

# ═════════════════════════════════════════════════════════════════════════════
# 15. RTOS — 꼬리
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "왜 리눅스가 아니라 RTOS 인가 — 평균이 아니라 꼬리",
                   lead="같은 연산을 Pi 5 리눅스가 평균 146배 빠르게 한다. 그런데도 RTOS 보드를 쓴다. 안전 기능의 기준은 평균이 아니라 최악이기 때문이다.",
                   foot="우선순위를 뒤집으면(NPU가 IMU보다 높으면) 25 ms 추론이 20 ms 주기를 막아 스케줄 자체가 불가능해진다 — 근거: sw/docs/evidence/10_스케줄가능성_분석.md")
lw = 560
table(sh, PAD, T, lw, ["플랫폼", "표본", "중앙값", "최악"],
      [["Pi 5 + Linux · 유휴", "20,000", "69 µs", "<rb>5,790 µs</rb>"],
       ["Pi 5 + Linux · 부하", "20,000", "68 µs", "<rb>5,429 µs</rb>"],
       ["STM32N6 + ThreadX (응답 전체)", "49,405", "12.4 µs", "<gb>22.6 µs</gb>"]],
      [2.4, 1, 1, 1.2], size=10, row_h=28, head_h=24, first_col_color=HBLUE)
sy = T + 24 + 3 * 28 + 18
stat(sh, PAD, sy, 180, 84, "5,790 µs", "Pi 5 리눅스<br>최악 깨어남 지연", color=ARED, num_size=26)
stat(sh, PAD + 190, sy, 180, 84, "22.6 µs", "STM32N6 + ThreadX<br>최악 응답 전체", color=ABLUE, num_size=26)
stat(sh, PAD + 380, sy, 180, 84, "29 %", "20 ms 제어 주기 중<br>지터 한 번이 먹는 몫", color=HBLUE, num_size=26)
block(sh, PAD, sy + 104, lw, 150, "이것이 왜 치명적인가",
      "제어 주기는 20 ms 다. 리눅스의 최악 지터 5,790 µs 는 한 주기의 29 %를 한 번에 먹는다. 40 km/h 에서 5.8 ms 는 6.4 cm 지만, 미끄러짐이 시작된 뒤의 제어 루프에서는 그 한 번이 차선 유지와 제어 상실을 가른다.",
      fill=PANEL, size=10, color=ARED)
rx, rw = PAD + lw + 28, CW - lw - 28
rh = (B - T - 16) / 2
pic(sh, FIG, "latency_cdf.jpg", rx, T, rw, rh, cap="지연 분포 CDF — 리눅스는 꼬리가 길다", cap_h=18)
pic(sh, FIG, "schedule_rtos.jpg", rx, T + rh + 16, rw, rh, cap="RM 스케줄 — IMU(우선순위 3)가 NPU(4)를 선점한다", cap_h=18)

# ═════════════════════════════════════════════════════════════════════════════
# 16. 기준선 (영상)
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "방어가 없으면 — 기준선 9건 전부 제어를 잃었다",
                   lead="1차·2차를 모두 끈 채 같은 빙판·같은 속도·같은 주행선으로 들어갔다.",
                   foot="'스핀'은 차선 대비 방향 오차가 86°를 넘은 순간으로 정의했다. 실측값은 −88.4°에서 −99.2° 사이로, 차체가 역방향을 본 것이 아니라 차로를 가로질러 돌아간 상태다. 주행별 이벤트는 sw/docs/data/events/events_*_nodefense*.json 에 그대로 있다.")
lw = 640
vh_ = round(lw * 480 / 1280)
mv = video(sh, V / "v_nodefense.mp4", V / "v_nodefense.jpg", PAD, T, lw, vh_)
text(sh, PAD, T + vh_ + 5, lw, 30,
     "▶ 방호벽 충돌 주행 — 11.42 s 이탈 → 12.22 s 에 −98° → 12.32 s 에 42 km/h 로 벽에 충돌 (화면 글씨 12 km/h 는 충격 뒤 속도). 발표 모드에서 자동 재생",
     size=9, color=MUTED, spacing=1.3)
sy = T + vh_ + 44
sw_ = (lw - 12) / 2
h1 = still16(A / "base_spin_c.jpg", PAD, sy, sw_, sh, cap="차선 이탈 → 가로 미끄러짐 — 12.02 s 이탈(횡오프셋 −1.45 m, 방향 오차 −41°) → 14.82 s 에 −99°. 차로를 가로질러 90° 가까이 돌아 있다", cap_h=44)
still16(A / "base_wall_front.jpg", PAD + sw_ + 12, sy, sw_, sh, cap="방호벽 충돌 순간의 전방 카메라 — 화면이 방호벽으로 가득 찼다", cap_h=44)
rx, rw = PAD + lw + 28, CW - lw - 28
stat(sh, rx, T, 230, 80, "9 / 9", "전부 차선을 벗어났고, 차체가 진행<br>방향과 88~99° 어긋난 채 미끄러졌다", color=ARED, num_size=28)
stat(sh, rx + 250, T, 230, 80, "5 건", "42~52 km/h 로 방호벽에 부딪혔다 (충돌 직전).<br>주변 차량이 있던 4건은 충돌 전에 미끄러짐으로 끝났다", color=ARED, num_size=28)
block(sh, rx, T + 118, rw, 250, "기준선의 가정",
      ["제어를 잃은 뒤에는 운전자 입력을 모형화하지 않는다. 비교 대상은 '우리 시스템이 개입하느냐'이지 '운전자가 얼마나 잘 대처하느냐'가 아니기 때문이다.",
       "그래서 차선 이탈이 확정되면 자율주행을 떼고 관성에 맡기며, 정지하거나 충돌하거나 4초가 지나면 주행을 끝낸다.",
       "실제 운전자는 제동을 시도하므로, 기준선의 결과는 '아무 보조도 없을 때의 물리적 귀결'로 읽어야 한다."],
      fill=PANEL, size=9.5, color=HBLUE)
autoplay(s, [mv])

# ═════════════════════════════════════════════════════════════════════════════
# 17. 직접 비교 (영상)
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "같은 조건 직접 비교 — 방어 없음 vs STM32N6 RTOS 2차 방어",
                   lead="같은 날씨 · 같은 주행선 · 같은 빙판 · 같은 주변 차량 8대. 차이는 2차 방어 하나뿐이다.",
                   foot="날씨 4종(맑은 낮 · 밤 · 젖은 노면 · 눈)에서 같은 비교를 만들었다. 전체 영상은 저장소 sw/media/ 와 logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/ 에 있다.")
vh_ = round(CW * 388 / 1920)
mv = video(sh, V / "v_compare.mp4", V / "v_compare.jpg", PAD, T, CW, vh_)
text(sh, PAD, T + vh_ + 4, CW, 14, "▶ 발표 모드에서 자동 재생 · 각각 전방 카메라(NPU 입력) + 조감 · 16 s", size=8, color=FAINT)
cy_ = T + vh_ + 30
hw2 = (CW - 48) / 2
label(sh, PAD, cy_, hw2, "왼쪽 · 방어 없음", color=ARED)
stat(sh, PAD, cy_ + 24, hw2, 76, "25.4 km/h", "15.0 s · 12.0 s 에 차선을 이탈해 제어를 잃었다", color=ARED, num_size=30)
text(sh, PAD, cy_ + 104, hw2, 40,
     "차체가 진행 방향과 어긋난 채 차로를 가로질러 미끄러지는 중이다. 뒤따르던 차량이 그대로 접근하고 있다.",
     size=10, color=BODY, spacing=1.45)
x2 = PAD + hw2 + 48
label(sh, x2, cy_, hw2, "오른쪽 · STM32N6 RTOS 2차 방어", color=ABLUE)
stat(sh, x2, cy_ + 24, hw2, 76, "0.0 km/h", "13.2 s · IMU SLIP (lat_acc) a_y −0.36 g, yaw −0.28 → STOPPED", color=ABLUE, num_size=30)
text(sh, x2, cy_ + 104, hw2, 40, "차선 안에서, 빙판을 벗어난 지점에 정지했다. 미끄러짐 확정부터 정지까지 보드가 제동·조향을 냈다.",
     size=10, color=BODY, spacing=1.45)
gy = cy_ + 150
label(sh, PAD, gy, CW, "날씨 4종에서 같은 비교 — 각 쌍의 마지막 장면 (왼쪽 방어 없음 · 오른쪽 STM32N6 RTOS 2차 방어)", color=HBLUE)
gy += 24
gh = B - gy
gw = round(gh * 1288 / 520)
gx0 = PAD + (CW - (4 * gw + 3 * 12)) / 2
for i, (f, c) in enumerate([("c_clearnoon.jpg", "맑은 낮"), ("c_clearnight.jpg", "맑은 밤"), ("c_wetnoon.jpg", "젖은 노면"), ("c_snow.jpg", "눈")]):
    x = gx0 + i * (gw + 12)
    sh.add_picture(str(A / f), px(x), px(gy), px(gw), px(gh))
    veil(sh, x, gy + gh - 22, 86, 22, HBLUE, 0.85)
    text(sh, x + 8, gy + gh - 22, 78, 22, c, size=8.5, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
autoplay(s, [mv])

# ═════════════════════════════════════════════════════════════════════════════
# 18. 2차 방어 확장 — '어떻게'가 아니라 '언제'
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "2차 방어 확장 — '어떻게 밟나'가 아니라 '언제 밟나'",
                   lead="미끄러진 뒤의 제어를 더 다듬는 실험을 먼저 했다. 결과를 정한 것은 제어가 아니라 제동을 시작한 시점이었다.",
                   foot="근거: sw/docs/evidence/18_마찰별_게인스케줄링.md · 19_안전섬_연구계획.md. CARLA 0.9.16 시뮬레이션 결과다.")
lw = 600
fh_ = 196
pic(sh, FIG, "gs_curve_impact.png", PAD, T, lw, fh_, cap="① 곡선 빙판 — 마찰별 제어표 후보 78개가 고정 게인과 충돌 직전 속도 중앙값 0.2 km/h 안", cap_h=18)
pic(sh, FIG, "si_noevade.png", PAD, T + fh_ + 10, lw, fh_, cap="② 직선 빙판 위 정지 차량 — 판단 방식을 바꿔도, 2차 방어를 꺼도 결과가 같다. 운전자 제동 시점이 정했다", cap_h=18)
rx, rw = PAD + lw + 28, CW - lw - 28
block(sh, rx, T, rw, 168, "찾은 것",
      ["· 빙판에서는 누가 밟든 감속이 마찰 한계(μg)에서 막힌다. 펄스 제동은 실차에서 ABS 가 이미 하는 일이다.",
       "· 그래서 '브레이크를 어떻게 밟나'를 다듬어도 이길 여지가 없었다. 60 km/h 에서 9 s 전에 밟으면 전부 충돌, 13 s 전이면 섰다.",
       "· 바꿀 수 있는 것은 <b>언제부터 밟기 시작하느냐</b>다."],
      fill=PANEL, size=9.5, color=ARED, space_after=4)
table(sh, rx, T + 182, rw, ["", "하는 일", "못 하는 것"],
      [["ABS · ESC", "미끄러지는 바퀴를 잡고 차체 회전을 막는다", "마찰을 다른 장치에 알리지 않는다"],
       ["긴급제동 · 간격 유지", "앞차 거리로 제동 시점을 정한다", "마찰을 모르면 마른 노면 기준이라 빙판에서 늦다"],
       ["노면 마찰 추정 (일부 양산차)", "바퀴 신호로 미끄러움을 추정해 경고·공유", "제동 시점까지 바로 잇는 경우는 드물다"],
       ["<b>IcePredict 2차 방어</b>", "<b>마찰을 직접 재서 제동 시작 시점을 노면에 맞춘다</b>", "탭을 운전자가 느낀다 (다음 쪽)"]],
      [1.5, 2.6, 2.6], size=9, row_h=38, head_h=22, first_col_color=HBLUE)

# ═════════════════════════════════════════════════════════════════════════════
# 19. 노면 확인 탭 + 마찰 인식 제동 시점
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "노면 확인 탭과 마찰 인식 제동 시점 (SLIP_ENVELOPE)",
                   lead="빙판은 살짝만 밟아도 감속이 거기서 막힌다. 그 막힌 값이 곧 노면 마찰이다. 무거운 계산은 CARLA 에서 미리 끝내고 보드는 표만 본다.",
                   foot="플래그 SLIP_ENVELOPE(기본 0)를 끄면 출력이 작업 전과 비트 단위로 같다. 단위 시험 11개 · C = 파이썬 = 파이 안전 섬 출력 일치. 근거: 20_마찰인식_제동시점.md")
lw = 560
th_ = 290
pic(sh, FIG, "env_table.png", PAD, T, lw, th_, cap="CARLA 실측 감속으로 계산한 '제동을 시작해야 하는 거리' — 60 km/h 마른 노면 38 m, 블랙아이스 105 m", cap_h=18)
pic(sh, FIG, "env_probe_timeline.png", PAD, T + th_ + 10, lw, B - T - th_ - 10, cap="대표 주행 — 결빙 취약 구간 첫 탭은 마른 노면 '이상 없음', 다음 탭이 빙판 μ 0.15", cap_h=18)
rx, rw = PAD + lw + 28, CW - lw - 28
steps_ = [("언제 확인하나", "① 1차 카메라 위험도가 회색 구간(0.3 ~ 0.6)에 0.2 s → 카메라가 본 지점(19 m 앞)에 도착했을 때<br>② 영하 근처(3 °C 이하) 결빙 취약 구간 안 → 2.5 s 마다 최대 3번, 빙판을 찾으면 멈춤<br>안전 조건: 30 km/h 이상 · 직진 · 운전자 제동 없음 · 앞차 여유"),
          ("노면 확인 탭", "가속 페달을 0.2 s 떼고 브레이크 0.3 을 0.4 s. 마지막 0.25 s 평균 감속을 표로 되돌려 μ 를 잰다.<br>마른 노면이면 60 ms 만에 끝낸다 (속도 손실 13 → 5 km/h). 측정이 이상하면 한 번 다시 잰다"),
          ("20 ms 마다 판단", "정지 거리 = 0.3 s × v + v² / (2 × 0.9 × 실측 감속) + 4 m 를 앞 물체 거리와 비교해<br>순항 · 가속 금지 · 예비 제동 · 전력 제동 · 정지 유지 중 하나. 예비 제동은 남은 거리에 맞춰 서는 감속을 실측 감속으로 따라간다")]
y = T
hh = (B - T - 2 * 10) / 3
for i, (hd, bd) in enumerate(steps_):
    rect(sh, rx, y, rw, hh, fill=PANEL)
    text(sh, rx + 16, y + 12, 44, 30, f"0{i + 1}", size=20, color=ARED, face=FONT_X, spacing=1.0, wrap=False)
    text(sh, rx + 58, y + 15, rw - 74, 22, hd, size=11, color=INK, bold=True, spacing=1.0)
    text(sh, rx + 58, y + 40, rw - 74, hh - 46, bd, size=9.3, color=BODY, spacing=1.4)
    y += hh + 10

# ═════════════════════════════════════════════════════════════════════════════
# 20. CARLA 비교 — 정지 2/9 → 8/9 (영상)
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "CARLA 비교 — 같은 C 코드, 마찰만 다르다 · 정지 2 / 9 → 8 / 9",
                   lead="간격 유지 크루즈(ACC) 주행 중 빙판 위 정지 차량. 비교 대상은 마른 노면 μ 를 가정하고, 2차 방어는 탭으로 잰 μ 를 쓴다.",
                   foot="CARLA 결함 — 마찰 트리거 안에 다른 차가 있으면 자차가 트리거 밖에서도 그 마찰을 받는다. 정지 차량을 트리거 밖에 두고 모든 실험을 다시 했고, 주행마다 출발 가속으로 마른 출발을 확인했다.")
vw_ = 380
vh_ = round(vw_ * 1080 / 1280)
mv = video(sh, V / "v_envelope.mp4", V / "v_envelope.jpg", PAD, T, vw_, vh_)
text(sh, PAD, T + vh_ + 4, vw_, 28, "▶ 발표 모드 자동 재생 · 위: 마른 노면 기준 → 30 km/h 충돌 · 아래: 탭으로 마찰 측정 → 4.1 m 앞 정지",
     size=8, color=FAINT, spacing=1.3)
mx = PAD + vw_ + 24
mw = CW - vw_ - 24
pic(sh, FIG, "env_sweep.png", mx, T, mw, 268, cap="마찰 3종 × 속도 3종 — 충돌 직전 속도 (0 = 정지)", cap_h=16)
sy = T + 282
sw3 = (mw - 2 * 14) / 3
stat(sh, mx, sy, sw3, 78, "2 / 9", "마른 노면 기준 정지<br>나머지 7회 8 ~ 61 km/h 충돌", color=ARED, num_size=28)
stat(sh, mx + sw3 + 14, sy, sw3, 78, "8 / 9", "마찰 인식 정지 — 7회 4.1 ~ 4.2 m 앞<br>(목표 여유 4 m)", color=ABLUE, num_size=28)
stat(sh, mx + 2 * (sw3 + 14), sy, sw3, 78, "295 m", "마찰 인식이 충돌한 1회 (μ 0.10 · 80 km/h)<br>필요 거리 > 150 m — 물리적으로 불가", color=HBLUE, num_size=28)
table(sh, mx, sy + 96, mw, ["탭 판단 시험 (1차 모델이 실제로 판정)", "결과"],
      [["빙판 + 결빙 취약 구간 · 맑은 낮 · 밤 · 젖은 날씨", "3 / 3 정지 — 첫 탭 마른 노면, 다음 탭에서 빙판"],
       ["빙판 · 구간 정보 없음", "카메라가 놓쳐 탭 없음 → 30 km/h 충돌"],
       ["빙판 없음 · 구간 안 · 영하", "탭 3번 모두 '이상 없음' — 잘못된 제동 0"]],
      [2.4, 2.6], size=9, row_h=24, head_h=22, first_col_color=HBLUE)
autoplay(s, [mv])

# ═════════════════════════════════════════════════════════════════════════════
# 21. RTOS 안전 섬 — 메인 컴퓨터가 멈춰도 (영상)
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("2차 방어", "RTOS 안전 섬 — 메인 컴퓨터(리눅스)가 멈춰도 2차 방어는 산다",
                   lead="STM32 의 몫은 '더 좋은 제어'가 아니라 고장 격리다. 그 값은 2차 방어가 결과를 바꾸는 장면에서 보인다.",
                   foot="STM32N6 보드가 없어 같은 C 코드를 라즈베리파이 5 격리 코어(코어 3 · SCHED_FIFO · mlockall)에서 돌려 측정했다. 리눅스 커널 자체가 멈추면 같이 멈춘다 — 실차 설계는 별도 MCU 다.")
lw = 620
pic(sh, FIG, "si_fault.png", PAD, T, lw, 280, cap="곡선 빙판에서 파이를 죽였다 — 2차 방어가 파이에 있으면 방어 없음과 같고, STM32 에 있으면 고장 없음과 같다", cap_h=18)
sy = T + 294
stat(sh, PAD, sy, 196, 80, "53.1 km/h", "2차 방어가 파이 · 파이 정지<br>= 방어 없음 53.1", color=ARED, num_size=26)
stat(sh, PAD + 212, sy, 196, 80, "39.4 km/h", "2차 방어가 안전 섬 · 파이 정지<br>≈ 고장 없음 38.9 (μ 0.12 · 60 km/h)", color=ABLUE, num_size=26)
stat(sh, PAD + 424, sy, 196, 80, "0.12 s", "파이 심장박동 끊김 감지 →<br>축소 모드 (카메라·지도 입력 버림)", color=HBLUE, num_size=26)
table(sh, PAD, sy + 96, lw, ["전 코어 부하 · 20 ms × 6,000회", "평균 지연", "최악 지연", "마감 놓침"],
      [["일반 프로그램", "2.6 ms", "<rb>206 ms</rb>", "<rb>211</rb>"],
       ["실시간 우선순위 + 코어 고정", "0.04 ms", "0.23 ms", "0"],
       ["<b>안전 섬 (코어 격리)</b>", "0.018 ms", "<gb>0.06 ms</gb>", "<gb>0</gb>"]],
      [2.6, 1, 1, 1], size=9.5, row_h=24, head_h=22, first_col_color=HBLUE)
rx, rw = PAD + lw + 24, CW - lw - 24
vh_ = round(rw * 720 / 1280)
mv = video(sh, V / "v_island.mp4", V / "v_island.jpg", rx, T, rw, vh_)
text(sh, rx, T + vh_ + 4, rw, 28, "▶ 빙판을 확인한 직후 파이 메인 프로그램을 실제로 강제 종료(SIGKILL) — 안전 섬 혼자 4.1 m 앞에 세운다",
     size=8.5, color=MUTED, spacing=1.3)
block(sh, rx, T + vh_ + 44, rw, B - T - vh_ - 44, "판단이 일어나는 곳",
      ["· 데스크톱(CARLA·Moza)이 IMU·거리를 보내고, 파이 안전 섬이 20 ms 마다 탭·정지 거리·제동을 답한다.",
       "· 캠퍼스 와이파이 왕복 중앙 4.8 ms · 99 % 9.4 ms. 0.2 s 까지의 무응답은 마지막 명령을 유지한다.",
       "· 섬의 판단 = 보드 C 코드 = 파이썬 참조 (900 샘플 비트 동일)."],
      fill=PANEL, size=9.3, color=HBLUE, space_after=4)
autoplay(s, [mv])

# ═════════════════════════════════════════════════════════════════════════════
# 22. 시연 구성 — 사람이 직접 운전한다
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("시연", "시연 구성 — 사람이 직접 운전하고, 판단은 안전 섬이 한다",
                   foot="실행 절차·현장 체크리스트: sw/docs/evidence/21_시연_절차.md · 프로그램: sw/scripts/demo/moza_demo.py · realsense_primary.py · pi_demo_up.sh")
ph = 300
pw3 = (CW - 2 * 12) / 3
for i, (img, cap) in enumerate([(crop_cover(A / "demo_primary.jpg", pw3, ph, 0.22, 0.5), "<b>① 1차 방어</b> — RealSense 로 빙판 사진을 비추면 모델이 실시간으로 '블랙아이스 감지 — 감속하세요'"),
                                (crop_cover(HW / "hw_moza_rig.jpg", pw3, ph, 0.5, 0.35), "<b>② 2차 방어</b> — Moza 휠·페달로 직접 운전. 2차 방어 OFF 면 평소처럼 밟아도 미끄러져 충돌, ON 이면 탭 → 경고 → 필요한 순간 자동 제동"),
                                (crop_cover(A / "demo_island.jpg", pw3, ph, 0.5, 0.5), "<b>③ 안전 섬</b> — 주행 중 파이 메인 프로그램을 실제로 죽여도 격리 코어의 2차 방어가 계속 판단한다")]):
    x = PAD + i * (pw3 + 12)
    sh.add_picture(img, px(x), px(T), px(pw3), px(ph))
    text(sh, x, T + ph + 6, pw3, 48, cap, size=9.5, color=BODY, spacing=1.35)
ty = T + ph + 64
table(sh, PAD, ty, CW, ["장비", "하는 일"],
      [["데스크톱 (RTX 3090 · CARLA 0.9.16)", "장면·물리, Moza 입력, 화면. 사람이 운전해도 실시간의 0.996 배로 돈다"],
       ["Moza R3 휠 + SRP 페달", "중앙 복귀 스프링, 탭 때 짧은 충격, 빙판 확정 때 진동 — 실차에서 몸으로 느끼는 '툭'을 대신한다"],
       ["라즈베리파이 5", "안전 섬(격리 코어)에서 2차 방어 판단 + 메인 프로그램 대역(심장박동). 파이가 먼저 연결해 망 제약을 피한다"],
       ["RealSense D435i", "1차 방어 카메라 시연 — 같은 모델·같은 위험도 융합(문턱 0.60)"]],
      [2.2, 6.4], size=9.5, row_h=26, head_h=22, first_col_color=HBLUE)

# ═════════════════════════════════════════════════════════════════════════════
# 18. 센서 시각화 (라이다 영상)
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("검증", "센서 시각화와 자동 라벨 — 무엇을 보고 판단했는지 남긴다",
                   foot="주행 108회 전체의 이벤트 JSON 을 저장소에 함께 올렸다. CARLA 도 보드도 없이 sw/scripts/analysis/summarize_runs.py 만으로 집계표를 재생성할 수 있다 — 손으로 적은 숫자가 아니라는 증명이다.")
lw = 640
fh = round(lw * 996 / 1920)
sh.add_picture(str(FIG / "weather_1차_카메라경고.jpg"), px(PAD), px(T), px(lw), px(fh))
text(sh, PAD, T + fh + 6, lw, 34,
     "날씨 11종에서 1차 경보가 난 같은 순간 — 맑음 · 흐림 · 젖은 노면 · 보슬비 · 폭우 · 해질녘 · 밤 · 비 오는 밤 · 눈. 같은 주행선, 같은 빙판에서 날씨만 바꿔 돌렸다",
     size=9, color=MUTED, spacing=1.3)
rx, rw = PAD + lw + 28, CW - lw - 28
vw2 = 440
vh2 = round(vw2 * 480 / 640)
mv = video(sh, V / "v_lidar.mp4", V / "v_lidar.jpg", rx + (rw - vw2) / 2, T, vw2, vh2)
text(sh, rx, T + vh2 + 6, rw, 34, "▶ 시맨틱 라이다 64채널 — 청록이 빙판 구간, 초록 상자가 차량 3D 박스. 발표 모드에서 자동 재생",
     size=9, color=MUTED, spacing=1.3)
ty = max(T + fh + 50, T + vh2 + 50)
th_ = B - ty
tw_ = round(th_ * 1920 / 514)
sh.add_picture(str(FIG / "triplet_rscdtex_visual_00320.jpg"), px(PAD), px(ty), px(tw_), px(th_))
tx = PAD + tw_ + 24
text(sh, tx, ty, CW - tw_ - 24, 40, "카메라 · 2D 분할 라벨 · 라이다 — 도로/차선/빙판/차량 라벨을 자동 생성한다 (AI Hub 형식 참고)",
     size=10, color=BODY, spacing=1.4)
stat(sh, tx, ty + 46, 170, 60, "108", "주행 이벤트 JSON", color=HBLUE, num_size=24)
stat(sh, tx + 180, ty + 46, 170, 60, "23", "근거 문서", color=HBLUE, num_size=24)
stat(sh, tx + 360, ty + 46, 170, 60, "41", "결과 그림", color=HBLUE, num_size=24)
autoplay(s, [mv])

# ═════════════════════════════════════════════════════════════════════════════
# 19. 한계 (60 km/h 영상)
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("한계", "한계와 실패 사례 — 숨기지 않고 남긴 것")
cw4 = (CW - 3 * 12) / 4
ih4 = round(cw4 * 9 / 16)
fails = [(("still", A / "p1_approach_c.jpg"), "경보 거리는 기하 문제다",
          "모델이 요구하는 최소 80 px 과 ROI 해상도 91 px 이 경보 거리를 정한다. 알고리즘이 아니라 해상도를 올리는 것이 먼저다."),
         (("video", "v_limit60_cam"), "2차는 예방이 아니다",
          "60 km/h. 1차가 경고했지만 제동이 늦어 진입했고, 2차가 0.26 s 만에 개입했는데도 39.2 km/h 로 앞차와 충돌했다."),
         (("still", A / "falsealarm_c.jpg"), "오경보도 난다",
          "밤. 빙판이 41 m 밖(ROI 7.9~42.2 m 바깥)에 있는데 어두운 노면을 얼음으로 보아 위험도 0.52 로 경보가 났다."),
         (("still", A / "domaingap_c.jpg"), "질감 도메인 갭",
          "RSCD 실사진 질감을 CARLA 노면에 입히면, 같은 모델이 그 얼음에 반응하지 않는다. 재수집·재학습이 다음 과제다.")]
movies = []
for i, ((kind, src), t, b) in enumerate(fails):
    x = PAD + i * (cw4 + 12)
    if kind == "video":
        movies.append(video(sh, V / f"{src}.mp4", V / f"{src}.jpg", x, T, cw4, ih4))
    else:
        still16(src, x, T, cw4, sh)
    text(sh, x, T + ih4 + 12, 44, 30, f"0{i + 1}", size=20, color=ARED, face=FONT_X, spacing=1.0, wrap=False)
    text(sh, x + 44, T + ih4 + 15, cw4 - 44, 24, t, size=11.5, color=INK, bold=True, spacing=1.0)
    text(sh, x, T + ih4 + 48, cw4, 90, b, size=9.5, color=BODY, spacing=1.45)
veil(sh, PAD + cw4 + 12, T + ih4 - 22, 132, 22, HBLUE, 0.85)
text(sh, PAD + cw4 + 20, T + ih4 - 22, 124, 22, "▶ 발표 모드에서 자동 재생", size=8, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
ry = T + ih4 + 160
block(sh, PAD, ry, CW, 128, "그 밖에 남은 것",
      ["· Hailo-10H 교차 검증은 Dataflow Compiler 미확보로 열지 못했다. 1차는 STM32N6 Neural-ART 단독 결과다.　· D435i 실측 IMU 노이즈는 PVS 공개 데이터로 대체했다. 실물 연결 측정은 남은 과제다.",
       "· 기준선은 제어 상실 후 운전자 입력을 모형화하지 않는다. '아무 보조도 없을 때의 물리적 귀결'로 읽어야 한다.　· 젖은 노면 경보율 0.8 %는 더 낮출 여지가 있다. β(반사도 가중치)와 문턱의 재조정이 필요하다."],
      fill=PANEL, size=9.5, color=HBLUE, space_after=5)
sy = ry + 156
stat(sh, PAD, sy, 360, 70, "215 / 6,340", "실사진 얼음을 문턱 아래로 놓침 (3.4 %)", color=ARED, num_size=24)
stat(sh, PAD + 390, sy, 360, 70, "368 / 63,018", "얼음 아닌 노면에 경보 (0.6 %)", color=ARED, num_size=24)
stat(sh, PAD + 780, sy, 360, 70, "7.9 ~ 42.2 m", "카메라 ROI — 이 밖은 보지 못한다", color=HBLUE, num_size=24)
autoplay(s, movies)

# ═════════════════════════════════════════════════════════════════════════════
# 20. 결론
# ═════════════════════════════════════════════════════════════════════════════
s, sh, T, B = page("결론", "결론 · 재현성 · 향후 계획",
                   foot="팀 ARTS · 이지성 · 남윤상 · 김진찬 — 제24회 임베디드SW경진대회 자동차/모빌리티(현대자동차) 부문")
lw = 560
block(sh, PAD, T, lw, 96, "무엇을 만들었나",
      "카메라 한 겹에 기대지 않는 이중 안전망을 만들었다. 1차는 빙판에 닿기 전에 세우고, 1차가 놓치면 2차가 노면 마찰을 직접 재서 제동 시점을 노면에 맞춘다(정지 2/9 → 8/9). 2차 판단은 메인 컴퓨터와 분리된 RTOS 안전 섬에서 돌아 리눅스가 멈춰도 계속된다.",
      size=10)
label(sh, PAD, T + 110, lw, "재현 — 모든 숫자는 실측에서 자동 생성된다", color=HBLUE)
rect(sh, PAD, T + 134, lw, 92, fill=HBLUE)
text(sh, PAD + 18, T + 146, lw - 36, 70,
     ["<mono>python3 sw/scripts/analysis/summarize_runs.py</mono>   <s># 집계표 재생성</s>",
      "<mono>python3 -m pytest -q sw/tests</mono>                     <s># 단위 테스트 19개</s>",
      "<mono>python3 sw/fw/npu_lib/test_slip_core.py</mono>            <s># C 코어 ↔ 파이썬 동치</s>"],
     size=9.5, color=WHITE, spacing=1.5)
sy = T + 240
stat(sh, PAD, sy, 170, 60, "23", "근거 문서", color=HBLUE, num_size=24)
stat(sh, PAD + 180, sy, 170, 60, "41", "결과 그림", color=HBLUE, num_size=24)
stat(sh, PAD + 360, sy, 190, 60, "108", "주행 이벤트 JSON · 저장소에 함께", color=HBLUE, num_size=24)
rx, rw = PAD + lw + 28, CW - lw - 28
block(sh, rx, T, rw, 190, "향후 계획",
      ["<hb>1</hb>  경보 거리 확대 — ROI 해상도를 올려 1차가 보는 거리를 늘린다. 2차 의존도를 낮추는 가장 직접적인 길이다",
       "<hb>2</hb>  실사진 질감 재학습 — RSCD 질감을 CARLA 노면에 입혀 재수집하고 도메인 갭을 닫는다",
       "<hb>3</hb>  Hailo-10H 교차 검증 — Dataflow Compiler 확보 후 동일 모델을 두 NPU 에서 비교",
       "<hb>4</hb>  보드 실측 — 마찰 인식 제동 시점·파이 감시를 STM32N6 ThreadX 에서 다시 잰다 (이번 측정은 파이 격리 코어 대체)",
       "<hb>5</hb>  운전자가 못 느끼는 마찰 측정 — 앞축 구동·뒷축 제동으로 차 전체 가속을 바꾸지 않고 마찰만 재는 방법"],
      size=9.5, space_after=3)
rect(sh, rx, T + 196, rw, 110, fill=HBLUE)
label(sh, rx + 18, T + 206, rw - 36, "소스 코드", color=SKY)
text(sh, rx + 18, T + 228, rw - 36, 30, REPO, size=12, color=WHITE, bold=True, spacing=1.0, wrap=False)
text(sh, rx + 18, T + 262, rw - 36, 30, "MIT 라이선스 · 영상·그림·이벤트 데이터 포함 · README 에서 바로 재생된다", size=9, color=SKY)
py3 = T + 322
ph3 = B - py3 - 26
pw3 = (CW - 2 * 12) / 3
for i, (img, cy, cap) in enumerate([(crop_cover(HW / "hw_n6_bench.jpg", pw3, ph3, 0.5, 0.62), 0, "실물 보드가 실제 펌웨어를 돌린다"),
                                    (crop_cover(HW / "hw_moza_rig.jpg", pw3, ph3, 0.5, 0.35), 0, "사람이 운전해도 보드는 같은 판정을 한다"),
                                    (crop_cover(V / "v_compare_bev.jpg", pw3, ph3, 0.5, 0.5), 0, "CARLA 가 센서와 물리를 제공한다 — 같은 조건 비교")]):
    x = PAD + i * (pw3 + 12)
    sh.add_picture(img, px(x), px(py3), px(pw3), px(ph3))
    text(sh, x, py3 + ph3 + 5, pw3, 18, cap, size=9, color=MUTED)

# ═════════════════════════════════════════════════════════════════════════════
# 발표자 노트 — 쪽마다 말할 요지 (PDF 에는 들어가지 않는다)
# ═════════════════════════════════════════════════════════════════════════════
NOTES = {
    1: "IcePredict 는 블랙아이스를 AI 로 미리 예측하고, 예측이 틀려도 RTOS 가 차량 거동으로 반드시 잡는 이중 안전망입니다. 오른쪽 사진은 Moza 휠로 사람이 CARLA 를 직접 운전하는 HIL 시연 장면입니다.",
    2: "문제·해법·증명 세 줄과 숫자 네 개로 전체를 요약합니다. 실사진 블랙아이스 경보율 96.6 %, 마른 노면 오경보 0.3 %, 2차 방어 최악 응답 22.6 µs, 방어를 모두 끈 기준선은 9건 전부 제어 상실. 영상은 같은 조건에서 방어 없음(왼쪽)과 STM32N6 RTOS 2차 방어(오른쪽)의 차이입니다. 슬라이드가 열리면 자동 재생됩니다.",
    3: "공개 데이터에 블랙아이스 라벨이 없고, 투명한 얼음은 비전만으로 구분이 안 됩니다. 우리 보드도 실사진 얼음 6,340장 중 215장(3.4 %)을 놓쳤습니다. 그래서 1차는 블랙아이스를 직접 맞히려 하지 않고 위험도를 올리고, 그 전략이 실패할 때를 위해 2차를 둡니다.",
    4: "한 보드(STM32N6570-DK)에 두 임계도가 공존합니다. 1차는 NPU 추론 25 ms, 2차는 ThreadX 최악 응답 22.6 µs. IMU 융합 스레드(우선순위 3)가 NPU 프레임 스레드(4)를 선점하므로 추론이 돌고 있어도 IMU 판정은 밀리지 않습니다.",
    5: "STM32N6570-DK 와 AI 카메라 MB1854B 가 메인 보드, Raspberry Pi 5 16 GB + AI HAT+ 2 는 기상 맥락·브리지·교차 검증용 호스트(대회 필수 보드)입니다. RealSense D435i 와 RTX 5090 은 센서·렌더, NVMe SSD 와 보조배터리로 콘센트 없이 같은 구성을 돌립니다. Moza 휠·페달은 사람이 직접 CARLA 를 운전하는 경로입니다.",
    6: "개발계획서 대비 세 가지를 바꿨습니다. 2차 실행 환경을 Pi 5 리눅스에서 STM32N6 + ThreadX 로 — 리눅스 최악 깨어남 지연이 5,790 µs 로 측정됐기 때문입니다. 1차 NPU 를 Hailo 단독에서 Neural-ART 주로 — Dataflow Compiler 를 확보하지 못했습니다. 검증을 실차 실측에서 HIL + 실사진 69,358장으로. 이중 안전망 구조와 1·2차 전략은 계획서 그대로입니다.",
    7: "데이터셋을 먼저 고르지 않고 헤드별 정답을 먼저 정의했습니다. 1차 주 학습은 RSCD, 반사도 헤드 근거는 RoadSaW 의 실측 수막 두께, 2차 칼만 공분산은 PVS 실측 IMU. RSCD 27클래스를 정상·젖음·결빙 위험·포트홀 4클래스로 묶었습니다.",
    8: "보조 데이터셋 6종의 역할입니다. RoadSaW 에는 눈·얼음이 없고 ROAD 의 클래스는 노면 종류라 결빙이 없습니다. 이 한계를 알고 반사도 근거·국내 도메인 검증·실측 IMU·폭설 점검으로 역할을 나눠 썼습니다.",
    9: "MobileNetV3-Small 백본, 4클래스 로짓 + 반사도 헤드, ONNX opset 13, int8 PTQ. 표준 PTQ 로는 stem 층 활성 분포가 넓어 정확도가 떨어졌고 stem 등화와 클리핑으로 회복했습니다. Neural-ART @ 1 GHz 에서 추론 25 ms, 카메라 ROI 는 전방 7.9~42.2 m 입니다.",
    10: "시뮬레이션 화면이 아니라 RSCD 실사진 69,358장을 STM32N6 에 넣어 보드에서 받은 판정입니다. 블랙아이스 경보율 96.6 %, 마른 노면 오경보 0.3 %, 젖은 노면 0.8 %. 운영 문턱 0.603 은 전량 스윕에서 절벽 구간을 찾아 정했고, 장당 판정을 남겨 두어 문턱을 바꿔 재계산할 수 있습니다.",
    11: "맑은 낮 40 km/h, 마찰 0.02. 6.10 s 에 위험도 0.443 으로 1차 경보, 7.14 s 에 빙판 23.6 m 앞에서 정지. 빙판에 닿지 않았습니다. 같은 주행선에서 날씨 11종을 모두 돌려 인식·정지 14회, 경고 늦음 7회, 미인식 10회, 오경보 1회였습니다.",
    12: "위험도는 분류 확률·반사도·차선 가시성의 가중합이고 기상·위치 맥락이 가중치를 바꿉니다. 쓸 수 없는 신호에 0 을 넣으면 상한이 잘리므로 분모에서 빼서 재정규화합니다. 문턱은 0.441 에서 0.603 으로 재교정해 젖은 노면 오경보를 40 % 에서 0.8 % 로 내렸고, 폭우는 강수 게이트로 분리했습니다.",
    13: "IMU 50 Hz → 2상태 칼만 필터 → 자전거 모델 잔차 → 타원 밖이 3샘플(60 ms) 연속이면 미끄러짐 확정. 직사각형 규칙은 모서리에서 저속 지연이 있어 타원으로 바꿨고, 조향 지연 τ 0.06 s 를 넣어 급조향 오탐을 없앴습니다. C 코어는 파이썬 참조 구현과 같은 판정을 내리는지 매 커밋 검증합니다.",
    14: "1차를 끈 상태(미인식 가정)로 38.7 km/h 진입. 7.62 s 에 STM32N6 RTOS 가 미끄러짐을 확정(진입 1.78 s 만), hard_stop 뒤 왼쪽 차로가 비어 evade_left 로 전환, 11.68 s 에 차선 안 정지. 2차 발동 62회, 진입→확정 평균 2.41 s, 확정→정지 평균 3.78 s.",
    15: "리눅스는 중앙값 69 µs 로 빠르지만 최악 5,790 µs — 20 ms 제어 주기의 29 % 를 지터 한 번이 먹습니다. ThreadX 는 49,405 표본에서 최악 22.6 µs. 안전 기능의 기준은 평균이 아니라 꼬리이고, 우선순위를 뒤집으면 스케줄 자체가 불가능해집니다.",
    16: "방어를 모두 끈 기준선 9건 전부 차선을 벗어나 88~99° 가로로 미끄러졌고, 5건은 42~52 km/h 로 방호벽에 부딪혔습니다(충돌 직전 속도 — 처음 기록한 12~17 km/h 는 충격 뒤 값이었습니다). 제어 상실 후 운전자 입력은 모형화하지 않았습니다 — 비교 대상은 '우리 시스템이 개입하느냐' 이기 때문입니다. 영상은 방호벽 충돌 주행입니다.",
    17: "같은 날씨·주행선·빙판·주변 차량 8대, 차이는 2차 방어 하나. 왼쪽 방어 없음은 15.0 s 에 25.4 km/h 로 제어를 잃은 채 미끄러지는 중이고, 오른쪽 RTOS 2차 방어는 13.2 s 에 차선 안에서 정지했습니다. 맑은 낮·밤·젖은 노면·눈 4종에서 같은 비교를 만들었습니다.",
    18: "2차 방어를 더 다듬는 실험을 먼저 했습니다. 곡선 빙판에서는 마찰별 제어표 후보 78개가 고정 게인과 같았고, 직선 빙판에서는 2차 방어를 꺼도 결과가 같았습니다. 빙판에서는 누가 밟든 감속이 마찰 한계에서 막히고, 펄스 제동은 실차 ABS 가 이미 합니다. 바꿀 수 있는 것은 언제부터 밟느냐입니다. 지금의 긴급제동·간격 유지는 마찰을 모르면 마른 노면 기준으로 제동 시점을 잡습니다.",
    19: "카메라가 애매하거나 영하의 결빙 취약 구간에 들어서면 브레이크를 0.4 초 살짝 밟는 노면 확인 탭으로 마찰을 잽니다. 빙판은 살짝 밟아도 감속이 막히고, 그 값이 곧 마찰입니다. CARLA 에서 잰 감속 표로 정지 거리를 다시 계산하고, 20 ms 마다 순항·가속 금지·예비 제동·전력 제동을 고릅니다. 60 km/h 에서 마른 노면은 38 m, 블랙아이스는 105 m 앞에서 밟아야 합니다.",
    20: "같은 C 코드에 마찰 값만 다르게 넣었습니다. 마른 노면 기준은 9회 중 2회만 섰고, 마찰 인식은 8회 섰으며 그중 7회는 목표대로 앞차 4 m 앞이었습니다. 충돌한 1회는 295 m 가 필요한, 물리적으로 설 수 없는 조건이었습니다. 탭 판단 시험에서 빙판이 없을 때 잘못 제동한 적은 없었습니다. 실험 중 CARLA 결함을 찾아 정지 차량을 마찰 트리거 밖에 두고 모두 다시 했습니다.",
    21: "STM32 의 몫은 고장 격리입니다. 곡선 빙판에서 파이를 죽이면 2차 방어가 파이에 있을 때 53.1 km/h, 안전 섬에 있을 때 39.4 km/h 로 충돌했습니다 — 각각 방어 없음, 고장 없음과 같습니다. 보드가 없어 같은 C 코드를 파이 격리 코어에서 돌렸고, 전 코어 부하에서도 6,000 번 모두 20 ms 를 지켰습니다(최악 0.06 ms). 일반 프로그램은 211 번 놓쳤고 최악 206 ms 였습니다. 영상은 메인 프로그램을 실제로 강제 종료한 뒤에도 안전 섬이 차를 세우는 장면입니다.",
    22: "시연은 세 장면입니다. RealSense 로 빙판 사진을 비추면 1차 모델이 실시간으로 경고합니다. Moza 로 직접 운전하면 2차 방어를 끈 쪽은 평소처럼 밟아도 미끄러져 충돌하고, 켠 쪽은 탭과 경고 뒤 필요한 순간 시스템이 제동해 섭니다. 마지막으로 주행 중 파이 메인 프로그램을 죽여도 안전 섬이 계속 판단합니다.",
    23: "날씨 11종에서 1차 경보가 난 같은 순간, 시맨틱 라이다 64채널(청록이 빙판), 카메라·2D 분할·라이다 자동 라벨. 주행 108회 이벤트 JSON 을 저장소에 올려 CARLA 도 보드도 없이 summarize_runs.py 로 집계표를 재생성할 수 있습니다.",
    24: "한계 네 가지. 경보 거리는 해상도 문제(모델 최소 80 px vs ROI 91 px). 2차는 예방이 아니라 60 km/h 에서는 개입해도 39.2 km/h 로 충돌했습니다. 밤에 41 m 밖 빙판을 두고 오경보가 났고, RSCD 질감을 입힌 CARLA 노면에는 반응하지 않는 도메인 갭이 있습니다. Hailo 교차 검증과 D435i 실측 IMU 는 남은 과제입니다.",
    25: "블랙아이스 라벨이 없다는 한계에서 출발해 STM32N6 한 보드 위에 이중 안전망을 올렸고, 모든 숫자는 실측에서 자동 생성됩니다. 향후 ROI 해상도 확대, 실사진 질감 재학습, Hailo-10H 교차 검증, D435i 실측 IMU. 저장소 주소는 화면과 같습니다.",
}
for i, sl in enumerate(prs.slides, 1):
    sl.notes_slide.notes_text_frame.text = NOTES[i]

assert _n[0] == N_PAGES, _n[0]
prs.save(OUT)
print(f"저장: {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {N_PAGES}쪽)")
