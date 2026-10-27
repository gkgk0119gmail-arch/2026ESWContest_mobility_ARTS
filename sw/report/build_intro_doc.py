#!/usr/bin/env python3
"""작품소개서(대회 양식 .docx)를 채운다 — 양식의 표·서식은 그대로 두고 글자와 그림만 넣는다.

사용: python3 sw/report/build_intro_doc.py <양식.docx> [--team-photo 사진.jpg]
출력: sw/report/2026ESWContest_모빌리티_ARTS_작품소개서.docx
양식 조건: 총 2쪽 이내, 나눔명조 11pt, 검정 글씨, 파란 안내 문구 삭제. 팀원 사진이 없으면 그 칸의 안내 문구를 남긴다.
"""
from __future__ import annotations

import argparse
import html
import pathlib
import re
import shutil
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "sw/report/2026ESWContest_모빌리티_ARTS_작품소개서.docx"
WORK_PHOTO = ROOT / "sw/report/assets/intro_work_photos.png"

TEAM = [("팀장", "남윤상", "연세대학교", "HW·SW 개발"),
        ("팀원", "김진찬", "연세대학교", "HW·SW 개발"),
        ("팀원", "이지성", "연세대학교", "HW·SW 개발")]

OVERVIEW = ["블랙아이스 사고를 줄이는 운전자 보조 장치다. 카메라 AI 가 빙판을 미리 보고 경고·감속하는 1차 방어와, "
            "카메라가 놓쳤을 때 노면 마찰을 직접 재서 제동 시점을 다시 계산하는 2차 방어로 이중 안전망을 만든다. "
            "2차 방어는 메인 컴퓨터와 분리된 안전 전용 RTOS 에서 20 ms 마다 돌아 메인 컴퓨터가 멈춰도 계속 동작한다. "
            "조향은 운전자에게 두고 경고·가속 제한·제동만 돕는다."]
ENV = ["· 하드웨어: STM32N6570-DK(Neural-ART NPU, ThreadX), Raspberry Pi 5 16 GB + AI HAT+ 2, ST AI 카메라 MB1854B, "
       "Intel RealSense D435i, Moza R3 휠·SRP 페달",
       "· 소프트웨어: C(2차 방어 코어·ThreadX 펌웨어), Python, PyTorch(노면 분류 모델 학습), ONNX·ST Edge AI(보드 배포)",
       "· 검증: CARLA 0.9.16 HIL 시뮬레이션(RTX 5090·3090), 실제 도로 사진 69,358장",
       "· 데이터: RSCD, RoadSaW, AI Hub, PVS, ROAD"]
DESC = [("[1차 방어 — 미리 본다] ", "카메라 영상의 노면을 마른·젖은·블랙아이스·포트홀 4종으로 분류하고 반사도·기상 맥락과 융합해 위험도를 낸다. "
         "위험도가 문턱(0.60)을 넘으면 빙판에 닿기 전에 경고하고 감속한다. 실제 도로 사진 69,358장을 보드 NPU 에 넣어 "
         "블랙아이스 경보율 96.6 %, 마른 노면 오경보율 0.3 % 를 얻었다."),
        ("[2차 방어 — 재서 제때 세운다] ", "카메라가 애매하거나 영하의 결빙 취약 구간에 들어서면 브레이크를 0.4 초 살짝 밟는 '노면 확인 탭'으로 "
         "감속을 재고, 미리 측정한 마찰별 감속 표로 정지 거리를 다시 계산한다. 20 ms 마다 앞차 거리와 비교해 경고·가속 금지·예비 제동·전력 제동을 고른다. "
         "CARLA 에서 빙판 위 정지 차량 장면 9가지를 비교한 결과, 마른 노면 기준 제동은 2번만 섰고 마찰 인식 제동은 8번 섰다."),
        ("[안전 전용 RTOS] ", "2차 방어 판단은 리눅스 메인 컴퓨터와 분리해 돌린다(설계: STM32N6 ThreadX, 이번 시연: 라즈베리파이 격리 코어). "
         "곡선 빙판에서 메인 컴퓨터를 멈추자 2차 방어가 메인 컴퓨터에 있을 때는 53 km/h, 안전 전용 RTOS 에 있을 때는 39 km/h 로 충돌했다.")]
FEAT = [("① 두 방어가 서로 보완한다 — ", "카메라가 애매하면 2차 방어가 탭으로 확인하고, 카메라가 놓친 장면은 다시 학습할 데이터가 된다."),
        ("② 기존 장치와 겹치지 않는다 — ", "ABS·ESC 는 미끄러진 바퀴를 잡고, 긴급제동은 마찰을 모르면 마른 노면 기준으로 제동 시점을 정한다. "
         "IcePredict 는 잰 마찰로 '언제부터 서야 하는지'를 노면에 맞춘다."),
        ("③ 고장에 강하다 — ", "판단 루프를 메인 컴퓨터와 분리했다. 전 코어에 부하를 건 상태에서도 격리 코어의 판단 루프는 20 ms 주기를 6,000번 모두 지켰다(최대 지연 0.06 ms)."),
        ("④ 검증할 수 있다 — ", "보드 펌웨어와 같은 C 코드를 시뮬레이션에서 그대로 돌리고, 모든 수치를 공개 저장소의 스크립트로 다시 만들 수 있다.")]

RPR = ('<w:rPr><w:rFonts w:ascii="나눔명조" w:eastAsia="나눔명조" w:hAnsi="나눔명조" w:cs="KoPubWorld바탕체 Medium" w:hint="eastAsia"/>'
       '{b}<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr>')


def run(t, bold=False, sz=22):
    return f'<w:r>{RPR.format(b="<w:b/><w:bCs/>" if bold else "", sz=sz)}<w:t xml:space="preserve">{html.escape(t, quote=False)}</w:t></w:r>'


def para(runs, jc="left", after=40, line=264):
    rp = RPR.format(b="", sz=22)
    return (f'<w:p><w:pPr><w:spacing w:after="{after}" w:line="{line}" w:lineRule="auto"/><w:jc w:val="{jc}"/>{rp}</w:pPr>'
            + "".join(runs) + "</w:p>")


def drawing(rid, cx, cy, pid, name):
    return (f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{pid}" name="{name}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{pid}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


def set_cell_paras(cell, paras_xml):
    """셀의 tcPr 은 남기고 문단만 바꾼다."""
    m = re.match(r"(<w:tc>(?:<w:tcPr>.*?</w:tcPr>)?)", cell, re.S)
    return m.group(1) + "".join(paras_xml) + "</w:tc>"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("template"); ap.add_argument("--team-photo", default=None)
    a = ap.parse_args()
    tmp = pathlib.Path(tempfile.mkdtemp())
    with zipfile.ZipFile(a.template) as z: z.extractall(tmp)
    dp = tmp / "word/document.xml"; x = dp.read_text(encoding="utf8")
    t0, t1 = x.index("<w:tbl>"), x.index("</w:tbl>") + len("</w:tbl>")
    tbl = x[t0:t1]
    rows = re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S)
    head, tail = tbl[:tbl.index(rows[0])], tbl[tbl.index(rows[-1]) + len(rows[-1]):]
    cells = [re.findall(r"<w:tc>.*?</w:tc>", r, re.S) for r in rows]

    def put(ri, ci, paras_xml):
        old = cells[ri][ci]; new = set_cell_paras(old, paras_xml)
        rows[ri] = rows[ri].replace(old, new, 1); cells[ri][ci] = new

    # 제목의 [부문명] → 모빌리티 (검정)
    # 제목 칸의 두 번째 문단([부문명], 여러 run 으로 쪼개져 있다)을 통째로 '모빌리티'(검정)로 바꾼다
    c0 = cells[0][0]; ps = re.findall(r"<w:p[ >].*?</w:p>", c0, re.S)
    put(0, 0, [ps[0], para([run("모빌리티", sz=32)], jc="center", after=0)])
    assert "부문명" not in rows[0]
    put(1, 1, [para([run("ARTS")], jc="center", after=0)])
    put(2, 1, [para([run("IcePredict")], jc="center", after=0)])
    # 팀원 표 (행 4~6), 7~8 은 지운다
    for k, (role, name, org, work) in enumerate(TEAM):
        ri = 4 + k
        put(ri, 1, [para([run(role)], jc="center", after=0)])
        put(ri, 2, [para([run(name)], jc="center", after=0)])
        put(ri, 3, [para([run(org)], jc="center", after=0)])
        put(ri, 4, [para([run(work)], jc="center", after=0)])
    rels = (tmp / "word/_rels/document.xml.rels").read_text(encoding="utf8")
    ct = (tmp / "[Content_Types].xml").read_text(encoding="utf8")
    media = tmp / "word/media"; media.mkdir(exist_ok=True)
    if 'Extension="png"' not in ct:
        ct = ct.replace("<Default Extension=\"xml\"", '<Default Extension="png" ContentType="image/png"/><Default Extension="jpg" ContentType="image/jpeg"/><Default Extension="xml"')
    from PIL import Image

    def add_image(src, rid, fname, max_w_twips, max_h_twips, pid):
        nonlocal rels
        shutil.copy(src, media / fname)
        rels = rels.replace("</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{fname}"/></Relationships>')
        w, h = Image.open(src).size; s = min(max_w_twips / w, max_h_twips / h)
        return drawing(rid, int(w * s * 635), int(h * s * 635), pid, fname)    # 1 twip = 635 EMU
    put(9, 0, [add_image(WORK_PHOTO, "rIdImg1", "work_photos.png", 8700, 3000, 101)])
    rows[9] = re.sub(r'<w:trHeight w:val="\d+"/>', '<w:trHeight w:val="2700"/>', rows[9], count=1)   # 사진 높이에 맞춘다
    if a.team_photo:
        ext = pathlib.Path(a.team_photo).suffix.lower().lstrip(".")
        put(3, 0, [add_image(a.team_photo, "rIdImg2", f"team_photo.{ext}", 2400, 2600, 102)])
    put(10, 1, [para([run(t)]) for t in OVERVIEW])
    put(11, 1, [para([run(t)], jc="left") for t in ENV])
    put(12, 1, [para([run(h, bold=True), run(b)]) for h, b in DESC])
    put(13, 1, [para([run(h, bold=True), run(b)]) for h, b in FEAT])
    rows = rows[:7] + rows[9:]                                    # 빈 팀원 행 2개 삭제 (세로 병합은 그대로 이어진다)
    x = x[:t0] + head + "".join(rows) + tail + x[t1:]
    # 표 뒤 파란 안내 문구 3줄 삭제 (빈 문단 하나는 남긴다 — 표로 끝나는 문서는 Word 가 문단을 요구한다)
    after = x[x.index("</w:tbl>") + len("</w:tbl>"):x.index("<w:sectPr")]
    x = x.replace(after, '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>', 1)
    dp.write_text(x, encoding="utf8")
    (tmp / "word/_rels/document.xml.rels").write_text(rels, encoding="utf8"); (tmp / "[Content_Types].xml").write_text(ct, encoding="utf8")
    OUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(tmp.rglob("*")):
            if f.is_file(): z.write(f, f.relative_to(tmp).as_posix())
    shutil.rmtree(tmp)
    blue = x.count('w:val="0000FF"')
    print(f"저장: {OUT} — 남은 파란 서식 {blue}곳 (팀원 사진 안내 등)")


if __name__ == "__main__":
    main()
