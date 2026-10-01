#!/usr/bin/env python3
"""README 용 구조도를 PNG 로 그린다.

왜 mermaid 가 아닌가: GitHub 에서 두 번 "Cannot read properties of undefined (reading 'render')"
로 깨졌다. 그 오류는 문법 오류가 아니라 GitHub 쪽 mermaid 모듈이 로드에 실패할 때 나는 것이라
우리가 고칠 수 없다. 구조도는 README 의 첫인상이라 추측에 맡기지 않고 이미지로 고정한다.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib import font_manager as fm
import pathlib

FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf"
fp = fm.FontProperties(fname=FONT)
fm.fontManager.addfont(FONT)
# 나눔 글꼴에 µ 가 없다 → 빠진 글자는 DejaVu 로 떨어지게 목록으로 준다
plt.rcParams["font.family"] = [fm.FontProperties(fname=FONT).get_name(), "DejaVu Sans"]

BLUE, BLUE_E = "#dbeafe", "#2563eb"      # 1차 (인지)
RED, RED_E = "#fee2e2", "#dc2626"        # 2차 (안전 반응)
GRAY, GRAY_E = "#f1f5f9", "#64748b"      # 센서·주변
BOARD = "#fafafa"

fig, ax = plt.subplots(figsize=(15.2, 6.6), dpi=170)
ax.set_xlim(0, 152); ax.set_ylim(0, 64); ax.axis("off")

def box(x, y, w, h, text, fc, ec, fs=10.5, bold=True, z=3):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.6",
                                fc=fc, ec=ec, lw=1.6, zorder=z))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            zorder=z + 1, fontproperties=fp, color="#0f172a",
            fontweight="bold" if bold else "normal", linespacing=1.5)

def arrow(x1, y1, x2, y2, color="#475569", label=None, lw=1.8, dy=7.0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15,
                                 lw=lw, color=color, zorder=2,
                                 shrinkA=0, shrinkB=0))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, label, ha="center", va="bottom",
                fontsize=8.6, color=color, fontproperties=fp, zorder=5)

# ── 보드 영역 ────────────────────────────────────────────────────────────────
ax.add_patch(FancyBboxPatch((32, 3.5), 72, 57, boxstyle="round,pad=0.8,rounding_size=2.2",
                            fc=BOARD, ec="#cbd5e1", lw=1.8, ls=(0, (6, 3)), zorder=1))
ax.text(68, 57.6, "STM32N6570-DK  ·  한 보드에서 두 임계도가 함께 돈다",
        ha="center", fontsize=11.5, fontproperties=fp, color="#334155", fontweight="bold", zorder=2)

# ── 1차 방어 (위쪽) ──────────────────────────────────────────────────────────
box(1, 40, 24, 11, "전방 카메라\n1280×720", GRAY, GRAY_E, 10)
box(36, 40, 26, 11, "NPU 추론\nMobileNetV3 int8", BLUE, BLUE_E)
box(72, 40, 26, 11, "위험도 융합\n분류·반사도·기상", BLUE, BLUE_E)
box(110, 40, 40, 11, "1차 경보 → 감속\n빙판에 닿기 전", BLUE, BLUE_E)

arrow(25, 45.5, 36, 45.5, BLUE_E, "ROI 224×224 · 150 KB")
arrow(62, 45.5, 72, 45.5, BLUE_E)
arrow(98, 45.5, 110, 45.5, BLUE_E, "위험도 0.60 이상")

ax.text(49, 36.3, "25 ms  ·  우선순위 4", ha="center", fontsize=9,
        color=BLUE_E, fontproperties=fp, zorder=5)

# ── 2차 방어 (아래쪽) ────────────────────────────────────────────────────────
box(1, 12, 24, 11, "IMU 50 Hz\n횡가속·yaw·종가속", GRAY, GRAY_E, 10)
box(36, 12, 26, 11, "미끄러짐 감지\n칼만 + 자전거 모델", RED, RED_E)
box(72, 12, 26, 11, "비상 제어\n모드 결정", RED, RED_E)
box(110, 12, 40, 11, "차선 유지 / ABS 펄스\n빈 차로 회피 / 최대 제동", RED, RED_E)

arrow(25, 17.5, 36, 17.5, RED_E)
arrow(62, 17.5, 72, 17.5, RED_E, "미끄러짐 확정")
arrow(98, 17.5, 110, 17.5, RED_E, "매 샘플 20 ms")

# fontproperties 를 주면 나눔만 쓰게 돼 µ 가 빠진다. 여기만 rcParams 목록(나눔 → DejaVu)에 맡긴다.
ax.text(49, 8.2, "최악 22.6 µs  ·  우선순위 3 (선점)", ha="center", fontsize=9,
        color=RED_E, zorder=5)

# ── 선점 관계 ────────────────────────────────────────────────────────────────
ax.add_patch(FancyArrowPatch((49, 38.2), (49, 23.8), arrowstyle="-|>", mutation_scale=13,
                             lw=1.6, color=RED_E, ls=(0, (4, 2.5)), zorder=2,
                             connectionstyle="arc3,rad=0"))
ax.text(51.5, 31, "2차가 1차를 선점한다\n추론이 돌아도 판정은 밀리지 않는다", ha="left", va="center",
        fontsize=8.8, color=RED_E, fontproperties=fp, zorder=5, linespacing=1.5)

# ── 아래 설명 ────────────────────────────────────────────────────────────────
ax.text(76, 0.6, "1차는 예방이다 — 똑똑하지만 틀릴 수 있다.      2차는 안전망이다 — 단순하지만 제때 반드시 실행된다.",
        ha="center", fontsize=10.2, color="#475569", fontproperties=fp)

out = pathlib.Path(__file__).resolve().parents[2] / "docs/figures/architecture.png"
fig.savefig(out, bbox_inches="tight", facecolor="white", pad_inches=0.25)
print(f"{out}  ({out.stat().st_size // 1024} KB)")
