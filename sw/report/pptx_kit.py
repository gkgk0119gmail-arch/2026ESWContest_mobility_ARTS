#!/usr/bin/env python3
"""build_pptx.py 가 쓰는 레이아웃 도구.

좌표는 전부 px(1280×720) 로 쓴다. PDF 판과 같은 눈금이라 두 판이 어긋나지 않는다.
1280px × 720px = 13.333in × 7.5in (96 dpi) 이므로 px/96 = 인치다.
"""
from __future__ import annotations

import pathlib
import re

from PIL import Image
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

EMU_PER_PX = 914400 // 96          # 9525

# ── 색 ───────────────────────────────────────────────────────────────────────
INK     = RGBColor(0x0F, 0x17, 0x2A)
SLATE   = RGBColor(0x33, 0x41, 0x55)
MUTED   = RGBColor(0x64, 0x74, 0x8B)
FAINT   = RGBColor(0x94, 0xA3, 0xB8)
LINE    = RGBColor(0xE2, 0xE8, 0xF0)
PANEL   = RGBColor(0xF8, 0xFA, 0xFC)
DARK    = RGBColor(0x1E, 0x29, 0x3B)
NAVY    = RGBColor(0x0B, 0x12, 0x20)
WHITE   = RGBColor(0xFF, 0xFF, 0xFF)
BLUE    = RGBColor(0x25, 0x63, 0xEB)
BLUE_BG = RGBColor(0xEF, 0xF6, 0xFF)
RED     = RGBColor(0xDC, 0x26, 0x26)
RED_BG  = RGBColor(0xFE, 0xF2, 0xF2)
GREEN   = RGBColor(0x16, 0xA3, 0x4A)
ZEBRA   = RGBColor(0xF1, 0xF5, 0xF9)

FONT = "맑은 고딕"          # 윈도우·맥 PowerPoint 둘 다 가진 글꼴
MONO = "Consolas"

_COLORS = {"r": RED, "g": GREEN, "b": BLUE, "k": SLATE, "w": WHITE, "m": MUTED, "f": FAINT}


def px(v: float) -> Emu:
    return Emu(int(round(v * EMU_PER_PX)))


# ── 인라인 표시 ──────────────────────────────────────────────────────────────
# <b>굵게</b>, <r>빨강</r> <g>초록</g> <b2> 굵은 빨강 … 그리고 <br> 줄바꿈.
_TAG = re.compile(r"<(/?)(b|br|r|g|bb|br2|rb|gb|mono)>")


def _runs(text: str):
    """(글자, 굵게, 색, 고정폭) 목록으로 쪼갠다. 줄바꿈은 ('\\n',…) 로 돌려준다."""
    out, bold, color, mono, i = [], False, None, False, 0
    for m in _TAG.finditer(text):
        if m.start() > i:
            out.append((text[i:m.start()], bold, color, mono))
        close, tag = m.group(1), m.group(2)
        if tag == "br":
            out.append(("\n", bold, color, mono))
        elif tag == "b":
            bold = not close
        elif tag == "mono":
            mono = not close
        elif tag in ("r", "g"):
            color = None if close else _COLORS[tag]
        elif tag in ("rb", "gb"):
            bold = not close
            color = None if close else _COLORS[tag[0]]
        i = m.end()
    if i < len(text):
        out.append((text[i:], bold, color, mono))
    return out


def text(sh_tree, x, y, w, h, body, size=12.5, color=INK, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.45, space_after=0):
    """글상자 하나. body 는 문단 목록(list[str]) 이거나 문자열."""
    tb = sh_tree.add_textbox(px(x), px(y), px(w), px(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    paras = body if isinstance(body, (list, tuple)) else [body]
    for pi, raw in enumerate(paras):
        p = tf.paragraphs[0] if pi == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        if space_after:
            p.space_after = Pt(space_after)
        for frag, b, c, mono in _runs(raw):
            for li, chunk in enumerate(frag.split("\n")):
                if li:
                    p.add_line_break()
                if not chunk:
                    continue
                r = p.add_run()
                r.text = chunk
                r.font.size = Pt(size)
                r.font.bold = bold or b
                r.font.name = MONO if mono else FONT
                r.font.color.rgb = c or color
    return tb


def rect(sh_tree, x, y, w, h, fill=None, line=None, lw=1.0, radius=None):
    shape = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    s = sh_tree.add_shape(shape, px(x), px(y), px(w), px(h))
    if radius:
        s.adjustments[0] = radius / min(w, h)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(lw)
    s.shadow.inherit = False
    s.text_frame.text = ""
    return s


def veil(sh_tree, x, y, w, h, color=NAVY, opacity=0.75):
    """반투명 덮개. opacity 1.0 이 완전 불투명. python-pptx 에 API 가 없어 a:alpha 를 직접 넣는다."""
    from pptx.oxml.ns import qn
    s = rect(sh_tree, x, y, w, h, fill=color)
    srgb = s.fill.fore_color._xFill.find(qn("a:srgbClr"))
    a = srgb.makeelement(qn("a:alpha"), {"val": str(int(round(opacity * 100000)))})
    srgb.append(a)
    return s


def box(sh_tree, x, y, w, h, title, paras, accent=FAINT, fill=PANEL,
        size=11.5, title_size=12.5):
    """왼쪽에 색 띠가 있는 설명 상자."""
    rect(sh_tree, x, y, w, h, fill=fill, line=LINE, lw=0.75, radius=7)
    rect(sh_tree, x, y, 4, h, fill=accent)
    ty = y + 10
    if title:
        text(sh_tree, x + 15, ty, w - 28, 20, title, size=title_size, bold=True, spacing=1.0)
        ty += title_size * 1.9 + 3
    text(sh_tree, x + 15, ty, w - 28, y + h - ty - 8, paras, size=size,
         color=SLATE, spacing=1.5, space_after=5)


def table(sh_tree, x, y, w, head, rows, colw, size=10.5, row_h=22, head_h=22,
          head_fill=DARK, zebra=True):
    """표. colw 는 비율 목록."""
    n_c, n_r = len(head), len(rows) + 1
    g = sh_tree.add_table(n_r, n_c, px(x), px(y), px(w), px(head_h + row_h * len(rows)))
    tbl = g.table
    tbl.first_row = False
    tbl.horz_banding = False
    tot = sum(colw)
    for i, cw in enumerate(colw):
        tbl.columns[i].width = px(w * cw / tot)
    tbl.rows[0].height = px(head_h)
    for i in range(len(rows)):
        tbl.rows[i + 1].height = px(row_h)

    def fill_cell(cell, raw, bold, color, fill, sz):
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill
        cell.margin_left = px(9)
        cell.margin_right = px(7)
        cell.margin_top = px(3)
        cell.margin_bottom = px(3)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.line_spacing = 1.28
        for frag, b, c, mono in _runs(str(raw)):
            for li, chunk in enumerate(frag.split("\n")):
                if li:
                    p.add_line_break()
                if not chunk:
                    continue
                r = p.add_run()
                r.text = chunk
                r.font.size = Pt(sz)
                r.font.bold = bold or b
                r.font.name = MONO if mono else FONT
                r.font.color.rgb = c or color

    for j, htxt in enumerate(head):
        fill_cell(tbl.cell(0, j), htxt, True, WHITE, head_fill, size - 0.5)
    for i, row in enumerate(rows):
        bg = ZEBRA if (zebra and i % 2 == 1) else WHITE
        for j, cell in enumerate(row):
            fill_cell(tbl.cell(i + 1, j), cell, False, INK, bg, size)
    return g


def pic(sh_tree, root, rel, x, y, w, h, cap=None, cap_size=9.2, fit="contain",
        cap_h=None, border=True):
    """사진 한 장 + 설명. fit='contain' 은 상자 안에 다 들어가게, 'cover' 는 꽉 채우고 자른다."""
    p = pathlib.Path(root) / rel
    iw, ih = Image.open(p).size
    cap_h = cap_h if cap_h is not None else (26 if cap else 0)
    ah = h - cap_h - (4 if cap else 0)
    if fit == "contain":
        s = min(w / iw, ah / ih)
        dw, dh = iw * s, ih * s
        dx, dy = x + (w - dw) / 2, y + (ah - dh) / 2
        shp = sh_tree.add_picture(str(p), px(dx), px(dy), px(dw), px(dh))
    else:
        s = max(w / iw, ah / ih)
        dw, dh = iw * s, ih * s
        shp = sh_tree.add_picture(str(p), px(x - (dw - w) / 2), px(y - (dh - ah) / 2), px(dw), px(dh))
        # 넘치는 부분은 잘라낸다
        shp.crop_left = shp.crop_right = max(0.0, (dw - w) / dw / 2)
        shp.crop_top = shp.crop_bottom = max(0.0, (dh - ah) / dh / 2)
        shp.left, shp.top, shp.width, shp.height = px(x), px(y), px(w), px(ah)
    if border:
        shp.line.color.rgb = LINE
        shp.line.width = Pt(0.75)
    if cap:
        text(sh_tree, x, y + ah + 4, w, cap_h, cap, size=cap_size, color=MUTED, spacing=1.35)
    return shp
