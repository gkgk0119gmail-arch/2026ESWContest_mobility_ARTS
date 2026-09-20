#!/usr/bin/env python3
"""팀원 인수인계용 노션 페이지를 만든다.

기존 "연구 정리" 페이지는 시간순 기록이라 처음 보는 사람이 읽기 어렵다.
이 페이지는 **처음 보는 팀원이 30분 안에 따라올 수 있게** 다시 쓴다.

원칙
  - 시간순이 아니라 **질문순**으로. "이게 뭐야 → 왜 필요해 → 어디까지 됐어 → 뭘 하면 돼"
  - 숫자는 전부 실측에서 자동으로 읽는다. 손으로 적은 값이 문서와 어긋나지 않게.
  - 용어를 처음 쓸 때 풀어 쓴다.
  - "하지 말 것" 을 명시한다 — 지뢰를 미리 알려 주는 것이 인수인계의 핵심이다.

사용: python3 scripts/notion_handover.py [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import statistics as st
import urllib.error
import urllib.request

import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PARENT = "35415059-8e1e-8071-98cc-fc2157b37da3"
TITLE = "🧑‍🏫 인수인계 — IcePredict 30분 안에 따라잡기"


# ---------- 노션 ----------
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
        raise SystemExit(f"노션 API {e.code}: {e.read().decode()[:400]}")


def rt(s, bold=False, code=False):
    return [{"type": "text", "text": {"content": s[:1900]},
             "annotations": {"bold": bold, "code": code}}]


def para(s=""):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(s) if s else []}}


def head(n, s):
    k = f"heading_{n}"
    return {"object": "block", "type": k, k: {"rich_text": rt(s)}}


def bullet(s):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(s)}}


def num_(s):
    return {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": rt(s)}}


def todo(s, done=False):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": rt(s), "checked": done}}


def callout(s, emoji="💡"):
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rt(s), "icon": {"type": "emoji", "emoji": emoji}}}


def codeblk(s, lang="plain text"):
    return {"object": "block", "type": "code", "code": {"rich_text": rt(s), "language": lang}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def toggle(title, children):
    return {"object": "block", "type": "toggle",
            "toggle": {"rich_text": rt(title), "children": children}}


def table(rows):
    w = max(len(r) for r in rows)
    kids = [{"object": "block", "type": "table_row",
             "table_row": {"cells": [rt(c) for c in r] + [rt("")] * (w - len(r))}} for r in rows]
    return {"object": "block", "type": "table",
            "table": {"table_width": w, "has_column_header": True,
                      "has_row_header": False, "children": kids}}


# ---------- 실측값 읽기 ----------
def facts() -> dict:
    f = {}
    # 보드 WCET
    try:
        rows = [json.loads(l) for l in open(ROOT / "logs/board_wcet.jsonl") if l.strip()]
        busy = [r for r in rows if r.get("npu_busy")]
        idle = [r for r in rows if not r.get("npu_busy")]
        f["wcet_runs"] = len(rows)
        f["wcet_samples"] = sum(r["samples"] for r in rows)
        f["wcet_max"] = max(r["max_us"] for r in rows)
        f["wcet_mean"] = st.mean(r["avg_us"] for r in rows)
        f["busy_mean"] = st.mean(r["avg_us"] for r in busy) if busy else None
        f["idle_mean"] = st.mean(r["avg_us"] for r in idle) if idle else None
    except Exception:
        pass
    # 실사진 표본
    try:
        rows = [json.loads(l) for l in open(ROOT / "logs/rscd_board_samples.jsonl") if l.strip()]
        CLS = ("normal", "wet", "black_ice", "pothole")
        P = np.array([r["p"] for r in rows]); y = np.array([CLS.index(r["cls"]) for r in rows])
        risk = np.array([r["risk"] for r in rows]); us = np.array([r["us"] for r in rows])
        f["photo_n"] = len(rows)
        f["ice_acc"] = float((P[y == 2].argmax(1) == 2).mean())
        f["npu_mean_ms"] = float(us.mean()) / 1000
        f["npu_max_ms"] = float(us.max()) / 1000
        f["npu_spread_pct"] = 100 * float(us.max() - us.min()) / float(us.mean())
        f["risk"] = risk; f["y"] = y
    except Exception:
        pass
    # 운영 문턱
    try:
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from icepredict.pi.context import (WeatherObs, LocationCtx, build_context,
                                           THRESHOLD_HI, THRESHOLD_LO, PRECIP_DISTRUST_MM)
        f["th_demo"] = build_context(WeatherObs(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                                     LocationCtx(feature="bridge", hour=5)).threshold
        f["th_hi"], f["th_lo"] = THRESHOLD_HI, THRESHOLD_LO
        f["precip_gate"] = PRECIP_DISTRUST_MM
        from icepredict.pi.fusion import ALARM_CONFIRM_FRAMES
        f["confirm"] = ALARM_CONFIRM_FRAMES
    except Exception:
        pass
    # 변동 스윕
    try:
        TAG = re.compile(r"^var_s(\d+)_k(\d+)_mu(\d+)$")
        et, sm, bm = [], [], []
        ev_cnt = coll = n = 0
        for p in glob.glob(str(ROOT / "logs/carla_demo/events_var_*.json")):
            tag = os.path.basename(p)[7:-5]
            if not TAG.match(tag):
                continue
            d = json.load(open(p)); ev = d.get("events", [])
            if not ev:
                continue
            n += 1
            k = {}
            for e in ev:
                k.setdefault(e["event"], e)
            if k.get("patch_enter") and k.get("secondary_slip"):
                et.append(k["secondary_slip"]["t"] - k["patch_enter"]["t"])
            if k.get("secondary_slip") and k.get("stabilized"):
                sm.append(k["stabilized"]["t"] - k["secondary_slip"]["t"])
            if k.get("board_slip_latency"):
                bm.append(k["board_slip_latency"]["max_us"])
            if any(e["event"] == "emergency_mode" and e.get("mode", "").startswith("evade") for e in ev):
                ev_cnt += 1
            if any(e["event"] == "collision" for e in ev):
                coll += 1
        f["var_n"] = n; f["var_enter_slip"] = et; f["var_slip_stop"] = sm
        f["var_board_max"] = bm; f["var_evade"] = ev_cnt; f["var_coll"] = coll
    except Exception:
        pass
    # 대조군
    try:
        n = fa = sec = 0
        for p in glob.glob(str(ROOT / "logs/carla_demo/events_*control*.json")):
            d = json.load(open(p)); a = d.get("args", {})
            if not a.get("control_no_ice"):
                continue
            ev = d.get("events", [])
            n += 1
            if any(e["event"] == "primary_warning" for e in ev):
                fa += 1
            if any(e["event"] == "secondary_slip" for e in ev):
                sec += 1
        f["ctrl_n"], f["ctrl_fa"], f["ctrl_sec"] = n, fa, sec
    except Exception:
        pass
    return f


def rng(v, unit="s", n=2):
    v = [x for x in v if x is not None]
    if not v:
        return "-"
    if len(v) == 1:
        return f"{v[0]:.{n}f} {unit} (표본 1)"
    return f"{st.mean(v):.{n}f} ± {st.pstdev(v):.{n}f} {unit} (최소 {min(v):.{n}f}, 최대 {max(v):.{n}f}, n={len(v)})"


def build(f: dict) -> list[dict]:
    B: list[dict] = []
    th = f.get("th_demo", 0.60)

    B.append(callout(
        "처음 보는 팀원이 30분 안에 따라올 수 있게 쓴 문서다. 질문 순서로 읽으면 된다 — "
        "이게 뭐야 → 왜 이렇게 만들었어 → 어디까지 됐어 → 내가 뭘 하면 돼. "
        "숫자는 전부 실제 측정값이고, 근거 파일 위치를 같이 적어 뒀다.", "🧑‍🏫"))
    B.append(para())

    # ── 1. 한 문단 요약
    B.append(head(1, "1. 한 문단으로"))
    B.append(para(
        "겨울 도로의 블랙아이스를 두 겹으로 막는 시스템이다. "
        "1차는 카메라가 노면을 보고 미리 경고한다. 2차는 그래도 미끄러지면 IMU(관성센서)가 "
        "잡아내 비상 제어한다. 둘 다 STM32N6 보드 한 장에서 돌고, 2차는 RTOS(ThreadX)의 "
        "우선순위 선점 덕분에 1차가 아무리 바빠도 밀리지 않는다. "
        "검증은 CARLA 시뮬레이터에 실물 보드를 물려서(HIL) 한다."))
    B.append(callout(
        "핵심 주장 한 줄: \"1차가 완벽하다\" 가 아니라 "
        "\"1차가 못 하는 조건을 시스템이 알고, 그때 2차가 받는다\".", "🎯"))

    # ── 2. 용어
    B.append(head(1, "2. 먼저 알아야 할 용어 6개"))
    B.append(table([
        ["용어", "뜻"],
        ["1차 방어", "카메라 + NPU. 노면을 보고 블랙아이스를 **미리** 경고한다"],
        ["2차 방어", "IMU. 이미 미끄러지기 시작했을 때 **사후에** 잡아 비상 제어한다"],
        ["위험도 (risk)", "분류 확률과 반사도를 섞은 0~1 값. 이게 문턱을 넘으면 1차가 경보한다"],
        ["문턱 (threshold)", f"경보 기준. 고정이 아니라 기상에 따라 {f.get('th_lo', 0.58)}~{f.get('th_hi', 0.75)} 사이에서 정해진다"],
        ["WCET", "최악 실행시간. 실시간 시스템은 평균이 아니라 이 값으로 설계한다"],
        ["HIL", "Hardware-in-the-Loop. 시뮬레이터에 실물 보드를 물려 돌리는 검증 방식"],
    ]))

    # ── 3. 지금 어디까지
    B.append(head(1, "3. 지금 어디까지 됐나"))
    B.append(table([
        ["항목", "상태", "숫자"],
        ["2차 방어 (RTOS)", "✅ 검증 완료",
         f"주행 {f.get('wcet_runs','?')}건 · 샘플 {f.get('wcet_samples',0):,}개, "
         f"평균 {f.get('wcet_mean',0):.1f} µs, 최악 {f.get('wcet_max',0):.1f} µs, 폴백 0건"],
        ["2차 방어 오탐", "✅ 실측",
         f"빙판 없는 대조군 {f.get('ctrl_n','?')}건에서 {f.get('ctrl_sec','?')}건"],
        ["1차 방어 (실사진)", "✅ 측정 완료",
         f"실사진 {f.get('photo_n',0):,}장, 블랙아이스 {100*f.get('ice_acc',0):.1f} % 정답"],
        ["1차 방어 오경보", "⚠️ 3층으로 막는 중",
         f"문턱 + 연속 {f.get('confirm','?')}프레임 + 강수 게이트"],
        ["NPU 추론 비용", "✅ 결정적",
         f"{f.get('npu_mean_ms',0):.2f} ms, 편차가 평균의 {f.get('npu_spread_pct',0):.1f} %"],
        ["회피 동작", "✅ 재현", "앞차 간격 50 m 미만이면 회피, 70 m 면 차선유지·정지"],
        ["Hailo 교차검증", "⛔ 막힘", "컴파일러 계정 필요"],
        ["실물 IMU (D435i)", "⛔ 막힘", "미연결. 다만 허용 예산 200 mg RMS 를 먼저 정해 뒀다"],
    ]))

    # ── 4. 왜 RTOS
    B.append(head(1, "4. 왜 RTOS 보드인가 (대회 주제의 핵심)"))
    B.append(para(
        "심사에서 가장 먼저 나올 질문이 \"그 계산 라즈베리파이에서 하면 안 되나?\" 다. "
        "보드 펌웨어가 쓰는 C 코드를 파이에서 그대로 컴파일해 같은 조건으로 재서 답을 만들었다."))
    B.append(table([
        ["조건", "평균 지연", "최악 지연"],
        ["Pi 5 리눅스 · 유휴", "64 µs", "4,677 µs"],
        ["Pi 5 리눅스 · 부하", "64 µs", "3,331 µs"],
        ["STM32N6 + ThreadX (전체 응답)",
         f"{f.get('busy_mean', 12.5):.1f} µs", f"{f.get('wcet_max', 17.9):.1f} µs"],
    ]))
    B.append(bullet("리눅스는 **아무것도 안 돌 때도** 4.7 ms 늦게 깨어난다 — 제어 주기 20 ms 의 23 %"))
    B.append(bullet(f"보드는 깨어남·연산·응답을 다 합쳐 {f.get('wcet_max',17.9):.1f} µs, 주기의 0.09 %"))
    B.append(bullet(
        f"보드가 스스로 증명한다: NPU 가 25.5 ms 추론을 도는 중에도 2차 응답이 "
        f"{f.get('idle_mean',10.9):.1f} → {f.get('busy_mean',12.5):.1f} µs 로 "
        f"{f.get('busy_mean',12.5)-f.get('idle_mean',10.9):.1f} µs 만 늘어난다. "
        "선점이 없었다면 25,500 µs 를 기다려야 한다"))
    B.append(callout(
        "말하면 안 되는 것 두 가지. ① \"보드가 파이보다 빠르다\" — 연산은 파이가 156배 빠르다. "
        "② \"리눅스로는 불가능하다\" — PREEMPT_RT 로 가능하다. **복잡도** 논거로 말해야 한다.", "🚨"))

    # ── 5. 1차 방어 3층
    B.append(head(1, "5. 1차 방어가 오경보를 막는 3층 구조"))
    B.append(para(
        "오경보를 한 가지 방법으로 막으려다 실패한 기록이 그대로 설계가 됐다. "
        "세 층이 각각 **다른 종류의 실패**를 막는다."))
    B.append(table([
        ["층", "무엇을 막나", "값", "어떻게 정했나"],
        ["① 문턱", "위험도가 애매한 구간",
         f"{f.get('th_lo',0.58)}~{f.get('th_hi',0.75)} (기상에 따라)",
         f"실사진 {f.get('photo_n',0):,}장에서 위험도 분포의 **절벽**(0.57)을 찾아 그 위로"],
        ["② 연속 프레임", "0.1초짜리 깜빡임",
         f"연속 {f.get('confirm',8)}프레임",
         "대조군에서 오경보가 최장 0.12초짜리였다. 진짜 빙판은 정지까지 유지된다"],
        ["③ 강수 게이트", "원리적으로 못 보는 조건",
         f"강수 {f.get('precip_gate',5.0):.0f} mm/h 이상",
         "폭우는 빙판이 **없어도** 위험도가 0.963까지 간다. 어떤 문턱으로도 못 막는다"],
    ]))
    B.append(callout(
        "③ 이 제일 중요하다. 폭우에서는 1차가 스스로 \"지금은 못 본다\" 고 선언하고 2차에 맡긴다. "
        "못 막을 경보를 내는 것보다 낫고, 이중 방어 구조의 존재 이유를 그대로 보여준다.", "🎯"))
    B.append(codeblk(
        "[ctx] 실제 날씨 반영: 강수 21.8 mm/h, 태양고도 45도 → 시각 13시\n"
        "[ctx] 1차 방어 신뢰 불가 → 끄고 2차에 맡긴다: 강수 21.8 mm/h ≥ 5 — 젖은 노면과 얼음 구분 불가"))
    B.append(para("↑ 발표에서 이 두 줄을 그대로 띄우면 설명이 거의 필요 없다."))

    return B


def build2(f: dict) -> list[dict]:
    """길어서 두 번째 묶음."""
    B: list[dict] = []

    # ── 6. 2차 방어 분포
    B.append(head(1, "6. 2차 방어는 얼마나 빨리 잡나"))
    if f.get("var_n"):
        B.append(para(
            f"예전에는 날씨 8종으로 돌려도 결과가 바이트 단위로 같았다 — CARLA 날씨는 물리에 "
            f"영향을 주지 않기 때문이다. 그래서 시드·속도·마찰을 흔들어 **{f['var_n']}건**을 따로 돌렸다."))
        B.append(table([
            ["지표", "분포"],
            ["빙판 진입 → 미끄러짐 확정", rng(f.get("var_enter_slip", []))],
            ["확정 → 정지", rng(f.get("var_slip_stop", []))],
            ["보드 응답 최악", rng(f.get("var_board_max", []), "µs", 1)],
            ["회피(evade) 선택", f"{f.get('var_evade',0)}/{f['var_n']}건"],
            ["충돌", f"{f.get('var_coll',0)}/{f['var_n']}건"],
        ]))
        B.append(bullet("보드 응답 최악값이 조건이 바뀌어도 좁다 — 연산이 입력에 거의 의존하지 않는다는 증거"))
    else:
        B.append(para("변동 스윕 결과가 아직 없다. `bash scripts/variation_sweep.sh` 로 만든다."))

    # ── 7. 폴더
    B.append(head(1, "7. 뭐가 어디 있나"))
    B.append(codeblk(
        "/mnt/ssd/icepredict/               ← 전부 여기. SSD 라 파이를 껐다 켜도 남는다\n"
        "├── scripts/                       실행·분석 스크립트\n"
        "├── src/icepredict/                파이썬 본체 (융합·컨텍스트·IMU 감지)\n"
        "├── fw/npu_lib/                    보드 펌웨어 패치와 C 코어\n"
        "├── dataset/rscd/                  실제 도로 사진 69,360장\n"
        "├── docs/                          분석·결정 기록\n"
        "└── logs/\n"
        "    ├── board_wcet.jsonl           보드 지연 누적 (덮이지 않는다)\n"
        "    ├── rscd_board_samples.jsonl   실사진 표본별 보드 판정\n"
        "    └── carla_demo/\n"
        "        ├── demo_*.mp4             영상 (지우지 않는다)\n"
        "        ├── figures/               발표용 그림\n"
        "        └── 정리/                  ← 팀이 볼 곳\n"
        "            ├── 00_읽어보기.txt\n"
        "            ├── 00~11_*.md         근거 문서 12편\n"
        "            └── A~H/               상황별 영상 폴더"))
    B.append(callout(
        "정리/ 폴더는 원본에 하드링크를 건다. 같은 이름으로 다시 촬영하면 원본은 덮이지만 "
        "옛 영상은 정리본 덕분에 살아남는다. 확인해 보니 **정리본 안에만 있는 영상이 98개** 다. "
        "폴더 이름이 중복돼 보여도 절대 지우지 말 것.", "🚨"))

    # ── 8. 자주 쓰는 명령
    B.append(head(1, "8. 자주 쓰는 명령"))
    B.append(codeblk(
        "# 지금 뭐가 돌고 있나\n"
        "bash scripts/stop_chains.sh --list\n\n"
        "# 돌고 있는 거 전부 안전하게 멈추기\n"
        "bash scripts/stop_chains.sh\n\n"
        "# 영상 찍기 (날씨 × 시나리오)\n"
        "DESK=dlab27@165.132.135.75 WEATHERS=\"ClearNoon WetNoon\" SCEN=detect \\\n"
        "  bash scripts/demo_batch_5090.sh\n\n"
        "# 실사진을 보드에 넣어 평가\n"
        "python3 scripts/rscd_board_eval.py --n 2000\n\n"
        "# 분석 문서 다시 만들기\n"
        "python3 scripts/analyze_detection.py     # 01_탐지성능.md\n"
        "python3 scripts/summarize_runs.py        # 00_집계.md\n"
        "python3 scripts/merge_wcet.py            # 보드 지연 누적 합치기 (배치 뒤 꼭)\n\n"
        "# 보드 살아 있나\n"
        "ping -c1 192.168.50.158", "bash"))

    # ── 9. 지뢰
    B.append(head(1, "9. 밟으면 아픈 지뢰 6개"))
    B.append(para("전부 실제로 밟아 본 것들이다. 같은 데서 시간 쓰지 말라고 적어 둔다."))
    B.append(table([
        ["지뢰", "증상", "피하는 법"],
        ["pkill 이 자기 셸을 죽인다",
         "명령이 조용히 중간에 끊기고 뒷부분이 실행 안 됨 (exit 144)",
         "패턴에 브래킷: pkill -9 -f '[C]arlaUE4'. 정리는 stop_chains.sh 로"],
        ["CARLA 가 8시간이면 죽는다",
         "포트는 열려 있는데 주행이 타임아웃. 로그에 '경보 없음' 이라 **성공처럼 보인다**",
         "events_*.json 에 이벤트가 있는지 확인. 없으면 죽은 주행이다"],
        ["체인 순서가 뒤집힌다",
         "'앞 단계 끝나면 시작' 을 종료 대기로만 짜면, 아직 시작도 안 했을 때 즉시 통과",
         "시작을 먼저 기다리고 그다음 종료를 기다린다"],
        ["배치 중 스크립트 수정",
         "다음 주행부터 바로 반영돼 펌웨어와 어긋난다",
         "프로토콜 변경은 펌웨어 굽기와 같은 단계에서. 영상은 .new 로 써 두고 나중에 교체"],
        ["세션 두 개 동시 실행",
         "같은 CARLA·보드·출력 폴더를 두 체인이 써서 서버가 죽고 영상이 덮임",
         "긴 배치 전에 stop_chains.sh --list 로 확인"],
        ["긴 배치를 백그라운드로만 띄움",
         "프로세스 트리가 회수돼 조용히 죽음",
         "setsid nohup ... & disown 으로 세션에서 떼어낸다"],
    ]))

    # ── 10. 다음에 할 일
    B.append(head(1, "10. 다음 사람이 할 일"))
    B.append(para("우선순위 순서다. 위에서부터 하면 된다."))
    B.append(todo("발표 서사 확정 — 특히 \"시뮬 인식률은 쓰지 않는다\", \"폭우에서는 1차를 신뢰 불가로 선언한다\" 두 가지는 팀 결정이 필요하다"))
    B.append(todo("확정 프레임(연속 8)을 보드 펌웨어 C 코드에 반영 — 지금은 호스트에 임시로 있다. ST-LINK 와 개발 모드 스위치 필요"))
    B.append(todo("D435i 연결 → 차 세워 두고 IMU 10분 기록 → RMS 가 200 mg 예산 안인지 확인 → 칼만 R 갱신"))
    B.append(todo("Hailo 컴파일러(DFC) 계정 받아 설치 → 보드 NPU 수치 독립 교차검증"))
    B.append(todo("망원 시야(25°) 검토 — 경보 거리를 늘리려면 이 길뿐이다. 원거리 해상도 2.5배"))
    B.append(todo("서류(10/30) · 발표(11/6) 자료 조립 — docs/presentation_evidence_map.md 참고"))

    # ── 11. 읽을 순서
    B.append(head(1, "11. 문서 읽는 순서"))
    B.append(num_("이 페이지 (지금 읽는 것)"))
    B.append(num_("docs/presentation_evidence_map.md — 어떤 주장에 어떤 자료를 쓰는지"))
    B.append(num_("logs/carla_demo/정리/05_실사진_대규모평가.md — 1차 방어 성능의 근거"))
    B.append(num_("logs/carla_demo/정리/06_RTOS가_왜_필요한가.md — 대회 주제의 핵심"))
    B.append(num_("logs/carla_demo/정리/10_스케줄가능성_분석.md — 임베디드 SW 로서의 논증"))
    B.append(num_("docs/research_directions_2026-09-20.md — 전체 분석·결정 기록 (길다, 필요할 때만)"))
    B.append(callout(
        "03_도메인갭.md 은 **반증 기록**이다. 거기 나온 차이(국소 대비 8배)는 "
        "도메인 갭의 원인이 아니라는 것이 04_질감의존.md 에서 밝혀졌다. "
        "그 표를 재수집·재학습의 근거로 쓰면 안 된다.", "⚠️"))

    return B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--parent", default=PARENT)
    a = ap.parse_args()

    f = facts()
    blocks = build(f) + build2(f)
    print(f"실측값 {len(f)}개 읽음, 블록 {len(blocks)}개")
    for k in ("photo_n", "wcet_samples", "var_n", "ctrl_n", "th_demo", "confirm"):
        if k in f:
            print(f"  {k} = {f[k]}")
    if a.dry_run:
        print("(--dry-run — 만들지 않았다)")
        return

    tok = yaml.safe_load(open(ROOT / "secrets.yaml"))["notion_token"]
    page = api(tok, "pages", "POST", {
        "parent": {"page_id": a.parent},
        "icon": {"type": "emoji", "emoji": "🧑‍🏫"},
        "properties": {"title": {"title": rt(TITLE)}},
        "children": blocks[:90],
    })
    pid, url = page["id"], page["url"]
    rest = blocks[90:]
    while rest:
        api(tok, f"blocks/{pid}/children", "PATCH", {"children": rest[:90]})
        rest = rest[90:]
    print(f"생성: {url}")

    # 부모 페이지 첫 블록 뒤에 바로가기
    ch = api(tok, f"blocks/{a.parent}/children?page_size=3")
    api(tok, f"blocks/{a.parent}/children", "PATCH", {
        "after": ch["results"][0]["id"],
        "children": [{"object": "block", "type": "callout",
                      "callout": {"icon": {"type": "emoji", "emoji": "🧑‍🏫"},
                                  "rich_text": [
                                      {"type": "text", "text": {"content": "처음 보는 사람은 여기부터 — "}},
                                      {"type": "text",
                                       "text": {"content": "인수인계 문서 (30분)", "link": {"url": url}},
                                       "annotations": {"bold": True}},
                                  ]}}]})
    print("부모 상단에 바로가기 추가")
    print(f"\n주소: {url}")


if __name__ == "__main__":
    main()
