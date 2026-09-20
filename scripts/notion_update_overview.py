#!/usr/bin/env python3
"""노션 정리 페이지에 2026-09-21 갱신분을 덧붙인다.

덧붙이는 것: 실사진 25,140 장 보드 평가, RTOS 논거 측정, 노면 조건별 분해,
반사도 헤드 전이 검증, IMU 노이즈 예산, 실사진 데모 영상.

맨 위 안내 콜아웃의 기준 시각도 함께 고친다.

사용: python3 scripts/notion_update_overview.py [--page <id>] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = "3e115059-8e1e-8190-8a76-e0a46e2d7f70"


def api(tok, path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.notion.com/v1/{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"노션 API 실패 {e.code}: {e.read().decode()[:400]}")


def rt(s, bold=False, code=False):
    """`**굵게**` 와 `` `코드` `` 를 노션 서식으로 바꿔 준다.

    그냥 문자열로 넘기면 별표와 백틱이 화면에 그대로 찍힌다. 팀이 읽을 문서에서
    `**중요**` 가 별표째 보이면 읽기 싫어진다. 그래서 여기서 한 번 해석한다.
    """
    import re as _re
    parts, out = _re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", s[:1900]), []
    for seg in parts:
        if not seg:
            continue
        b, c = bold, code
        if seg.startswith("**") and seg.endswith("**") and len(seg) > 4:
            seg, b = seg[2:-2], True
        elif seg.startswith("`") and seg.endswith("`") and len(seg) > 2:
            seg, c = seg[1:-1], True
        out.append({"type": "text", "text": {"content": seg},
                    "annotations": {"bold": b, "code": c}})
    return out or [{"type": "text", "text": {"content": ""},
                    "annotations": {"bold": bold, "code": code}}]


def para(s=""):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(s) if s else []}}


def head(level, s):
    k = f"heading_{level}"
    return {"object": "block", "type": k, k: {"rich_text": rt(s)}}


def bullet(s):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(s)}}


def callout(s, emoji="💡"):
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rt(s), "icon": {"type": "emoji", "emoji": emoji}}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def table(rows):
    w = max(len(r) for r in rows)
    kids = [{"object": "block", "type": "table_row",
             "table_row": {"cells": [rt(c) for c in r] + [rt("")] * (w - len(r))}} for r in rows]
    return {"object": "block", "type": "table",
            "table": {"table_width": w, "has_column_header": True,
                      "has_row_header": False, "children": kids}}


def build():
    B = []
    B.append(divider())
    B.append(head(1, "10. 2026-09-21 갱신 — 실사진 대규모 평가와 RTOS 논거"))
    B.append(callout(
        "발표 주제가 임베디드 SW / RTOS 라는 점과 기존 데이터셋 활용에 초점을 다시 맞춰 "
        "진행한 결과다. 근거 문서 다섯 편(05~09)이 새로 생겼다.", "🆕"))

    # --- 실사진 대규모 ---
    B.append(head(2, "10-1. 실사진 25,140장을 보드에 넣었다"))
    B.append(para(
        "RSCD 는 69,360장인데 그동안 1,200장(1.7 %)만 썼다. 표본별 판정을 JSONL 로 남기도록 바꿔 "
        "25,140장을 돌렸다. 이제 문턱을 바꿔 다시 계산할 때 보드를 다시 돌릴 필요가 없다."))
    B.append(table([
        ["실제 클래스", "장수", "보드 정답률", "문턱 0.60 경보율"],
        ["블랙아이스", "5,640", "96.0 %", "96.8 %"],
        ["포트홀", "6,500", "90.6 %", "0.5 %"],
        ["젖음", "6,500", "84.0 %", "1.9 %"],
        ["정상", "6,500", "80.3 %", "0.4 %"],
    ]))
    B.append(bullet("NPU 추론 25.49 ms, 편차 0.13 ms (평균의 0.5 %) — 입력과 무관한 고정 비용이라 상위 제어 주기를 설계할 수 있다"))
    B.append(bullet("측정 버그 하나 수정: 보드 융합기의 히스테리시스가 낱장 사진 평가를 오염시키고 있었다 (25,140장 중 1,388장). 기존 인-더-루프 경보율은 그만큼 부풀려져 있었다"))

    # --- 안전 관점 ---
    B.append(head(2, "10-2. 4클래스 정답률이 성능을 왜곡하고 있었다"))
    B.append(para(
        "오분류를 안전 관점으로 다시 셌다. 얼음을 놓치거나 얼음이 아닌 것을 얼음이라 하는 것만이 "
        "안전에 영향을 준다. 정상 ↔ 포트홀 혼동은 거칠기 등급을 한 칸 잘못 매긴 것이고 제동 판단을 바꾸지 않는다."))
    B.append(table([
        ["오분류 종류", "장수", "전체 대비", "안전 영향"],
        ["얼음이 아닌데 얼음이라 함", "87", "0.35 %", "있음"],
        ["얼음인데 못 알아봄", "224", "0.89 %", "있음"],
        ["나머지 (거칠기 등급 혼동)", "2,844", "11.31 %", "없음"],
    ]))
    B.append(callout(
        "안전에 영향을 주는 오분류는 전체의 1.24 % 다. 가장 큰 덩어리는 마른 콘크리트 slight 를 "
        "severe(포트홀)로 본 것 — 1,117장 중 691장. 그 1,117장 중 얼음이라 한 것은 단 1장이다. "
        "발표에서 '정상 80.3 %' 를 그대로 쓰면 손해다.", "⚠️"))
    B.append(para(
        "문턱 0.60 에서 남는 오경보 179건의 출처도 나왔다 — 새눈 7.0 %, 물 고인 매끈한 아스팔트 5.4 %, "
        "물 고인 진흙 1.9 %. 전부 실제로 미끄러운 조건이라 오경보라 부르기도 애매하다."))

    # --- RTOS ---
    B.append(head(2, "10-3. RTOS 논거를 측정으로 세웠다 ⭐"))
    B.append(para(
        "심사에서 가장 먼저 나올 질문은 \"그 계산 Pi 에서 하면 안 되나?\" 다. "
        "보드 펌웨어가 쓰는 헤더(slip_core.h)를 파이에서 그대로 컴파일해 같은 코드로 비교했다."))
    B.append(para(
        "처음에는 연산 시간만 쟀는데 약했다 — 연산이 86 ns 로 워낙 짧아 리눅스에서도 선점당할 틈이 없다. "
        "진짜 문제는 '계산이 느리다'가 아니라 '제때 깨워 주지 않는다' 였다. "
        "cyclictest 방식으로 1 ms 주기 태스크의 깨어남 지연을 쟀다."))
    B.append(table([
        ["조건", "평균", "p99.9", "최대"],
        ["Pi 5 리눅스 · 유휴", "64 µs", "1,693 µs", "4,677 µs"],
        ["Pi 5 리눅스 · 부하", "64 µs", "1,587 µs", "3,331 µs"],
        ["STM32N6 + ThreadX (전체 응답)", "12.5 µs", "—", "16.2 µs"],
    ]))
    B.append(bullet("리눅스는 아무것도 안 돌 때도 4.7 ms 늦게 깨어난다 — 제어 주기 20 ms 의 23 %"))
    B.append(bullet("보드는 깨어남부터 응답까지 합쳐 16.2 µs, 주기의 0.08 %"))
    B.append(bullet("보드가 스스로 증명한다: NPU 가 25.5 ms 추론을 도는 중에도 2차 응답이 10.9 → 12.5 µs 로 1.5 µs 만 늘어난다. 선점이 없었다면 25,500 µs 를 기다려야 한다"))
    B.append(callout(
        "정직하게 쓸 것. 연산 자체는 Pi 가 156배 빠르다 — \"보드가 빠르다\"고 말하면 안 된다. "
        "\"리눅스로는 불가능하다\"도 안 된다. PREEMPT_RT 로 가능하고, 복잡도 논거로 말해야 한다.", "🚨"))

    # --- 반사도 ---
    B.append(head(2, "10-4. 반사도 헤드는 전이됐지만 β 는 낮춰야 한다"))
    B.append(para(
        "문서가 한계로 적어 둔 \"RoadSaW 미착수, 반사도 라벨이 CARLA 합성\" 항목을 닫았다."))
    B.append(bullet("합성 라벨로만 학습했는데 실사진에서 얼음 vs 나머지 AUC 0.840 — 전이는 성공이다"))
    B.append(bullet("그런데 β 를 올릴수록 얼음 판별 AUC 가 단조 감소한다: β=0 에서 0.9978 → β=0.45 에서 0.9857"))
    B.append(bullet("이유: 반사도는 얼음과 정상은 잘 가르지만(0.970) 포트홀과는 거의 못 가른다(0.658). 분류기는 셋 다 잘 가른다"))
    B.append(bullet("OpenCV 고전 추정치는 실사진에서 AUC 0.383 — 무작위보다 나쁘다. 학습 헤드와 상관 −0.122. 고전 모듈은 실사진에 쓰면 안 된다"))
    B.append(callout(
        "따라서 RoadSaW 확보는 급하지 않다. β 를 낮추는 쪽이 먼저다. "
        "다만 CARLA 데모는 반대라 — 분류기가 약한 도메인이라 β 가 없으면 1차가 발화하지 않는다. "
        "제품 운영점과 데모 운영점을 분리해야 할 이유가 하나 더 늘었다.", "💡"))

    # --- IMU 예산 ---
    B.append(head(2, "10-5. 막힌 항목을 사양으로 바꿨다 — IMU 노이즈 예산"))
    B.append(para(
        "\"D435i 미연결\" 은 센서를 못 붙이면 못 푸는 항목처럼 보였다. 뒤집어서 "
        "\"얼마까지 견디는가\" 를 먼저 정했다. 보드와 동치가 검증된 로직에 백색잡음을 키워 가며 쟀다."))
    B.append(table([
        ["가속도계 노이즈", "빙판 탐지율", "정상 주행 오탐률"],
        ["200 mg RMS", "100 %", "0 %"],
        ["300 mg", "100 %", "9 %"],
        ["450 mg", "100 %", "83.5 %"],
    ]))
    B.append(bullet("예산은 200 mg RMS. 절벽이 가파르다 — 그 위로는 잡음만으로 발화해 확정 시각이 빙판 진입보다 앞서기까지 한다"))
    B.append(bullet("일반 MEMS(300 µg/√Hz)를 50 Hz 로 받으면 1.5 mg RMS — 133배 여유. D435i 의 IMU 가 이 등급이다"))
    B.append(bullet("센서를 붙이면 차를 세워 두고 10분 기록해 RMS 를 재는 것으로 확정된다. 검증이 측정을 기다리지 않는다"))

    # --- 영상 ---
    B.append(head(2, "10-6. 시뮬이 한 프레임도 없는 데모 영상"))
    B.append(para(
        "RSCD 실사진 100장을 \"정상 → 젖음 → 빙판 → 복귀\" 순서로 보드에 넣고 판정을 그렸다. "
        "화면에 클래스 확률·위험도 게이지·이력·보드 처리시간이 실시간으로 나온다. "
        "빙판 구간 34프레임 중 33프레임에서 경보가 났다."))
    B.append(bullet("파일: logs/carla_demo/demo_실사진주행_split.mp4"))
    B.append(bullet("\"시뮬에서만 되는 것 아니냐\" 는 반론에 그대로 내밀 수 있다"))

    # --- 문서 목록 ---
    B.append(head(2, "10-7. 새로 생긴 근거 문서"))
    B.append(table([
        ["문서", "무엇을 답하나"],
        ["정리/05_실사진_대규모평가.md", "실사진 25,140장 보드 판정 — 정답률·혼동행렬·문턱곡선·추론시간"],
        ["정리/06_RTOS가_왜_필요한가.md", "같은 C 코드로 잰 리눅스 vs ThreadX. 주기 지터가 핵심"],
        ["정리/07_노면조건별_분해.md", "27가지 노면 조건별 분해. 안전 관점 오분류 재집계"],
        ["정리/08_반사도헤드_전이검증.md", "반사도 헤드가 실사진으로 전이됐는가. β 스윕"],
        ["정리/09_IMU노이즈_허용예산.md", "2차 방어가 견디는 IMU 노이즈 한계"],
    ]))
    return B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=PAGE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tok = yaml.safe_load(open(ROOT / "secrets.yaml"))["notion_token"]
    blocks = build()
    print(f"블록 {len(blocks)}개 준비")
    if a.dry_run:
        print("(--dry-run)")
        return

    rest = blocks
    while rest:
        api(tok, f"blocks/{a.page}/children", "PATCH", {"children": rest[:90]})
        rest = rest[90:]
    print("갱신분 추가 완료")

    # 맨 위 안내 콜아웃의 기준 시각 갱신
    ch = api(tok, f"blocks/{a.page}/children?page_size=3")
    first = ch["results"][0]
    if first["type"] == "callout":
        api(tok, f"blocks/{first['id']}", "PATCH", {
            "callout": {"icon": {"type": "emoji", "emoji": "🧭"},
                        "rich_text": rt(
                            "IcePredict 연구·산출물 지도. 어떤 상황으로 나눠 실험했는지, 결과물이 어느 폴더에 "
                            "어떻게 정리돼 있는지, 지금 무엇이 확정됐고 무엇이 남았는지를 한 곳에 모았다. "
                            "기준 시각 2026-09-21 02:00 (§10 에 최신 갱신분). "
                            "원본은 팀 저장소 /mnt/ssd/icepredict 에 있다.")}})
        print("상단 안내 갱신")
    print(f"\n주소: https://app.notion.com/p/{a.page.replace('-','')}")


if __name__ == "__main__":
    main()
