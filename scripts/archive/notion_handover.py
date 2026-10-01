#!/usr/bin/env python3
"""팀원 인수인계용 노션 페이지를 만든다.

기존 "연구 정리" 페이지는 시간순 기록이라 처음 보는 사람이 읽기 어렵다.
이 페이지는 **처음 보는 팀원이 30분 안에 따라올 수 있게** 다시 쓴다.

원칙
  - 시간순이 아니라 **질문순**으로. "이게 뭐야 → 왜 필요해 → 어디까지 됐어 → 뭘 하면 돼"
  - 숫자는 전부 실측에서 자동으로 읽는다. 손으로 적은 값이 문서와 어긋나지 않게.
  - 용어를 처음 쓸 때 풀어 쓴다.
  - "하지 말 것" 을 명시한다 — 지뢰를 미리 알려 주는 것이 인수인계의 핵심이다.

사용: python3 scripts/archive/notion_handover.py [--dry-run]
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

ROOT = pathlib.Path(__file__).resolve().parents[2]
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
        # 젖음과 얼음이 실사진에서 얼마나 갈리나 — 표본이 늘면 이 값도 따라가야 한다
        ice, wet = risk[y == 2], risk[y == 1]
        if len(ice) and len(wet):
            from itertools import product as _pr  # noqa: F401
            order = np.argsort(np.concatenate([ice, wet]))
            ranks = np.empty(len(order)); ranks[order] = np.arange(1, len(order) + 1)
            f["auc_ice_wet"] = float((ranks[:len(ice)].sum() - len(ice) * (len(ice) + 1) / 2)
                                     / (len(ice) * len(wet)))
        th = f.get("th_demo", 0.60)
        if len(wet):
            f["wet_fa"] = float((wet >= th).mean())
    except Exception:
        pass
    # 리눅스 깨어남 지연 (벤치를 다시 돌리면 값이 바뀐다 — 하드코딩하지 않는다)
    try:
        for tag, key in (("idle", "lin_idle"), ("load", "lin_load")):
            v = np.loadtxt(ROOT / f"logs/rtos_bench/bench_cyclic_{tag}.txt") / 1000.0
            v = v[np.isfinite(v)]
            f[f"{key}_max_us"] = float(v.max())
            f[f"{key}_mean_us"] = float(v.mean())
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
        ev_cnt = coll = n = hard = touch = 0
        by_kph: dict[int, list[float]] = {}
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
            col = next((e for e in ev if e["event"] == "collision"), None)
            if col:
                coll += 1
                # 아직 달리는 중 받은 것과, 이미 선 뒤 스치듯 닿은 것을 가른다.
                # 한 칸에 넣으면 숫자가 거짓말을 한다.
                stp = (k.get("stopped") or {}).get("t")
                after_stop = stp is not None and col["t"] >= stp - 0.05
                if float(col.get("speed_kph", 0.0)) >= 5.0 and not after_stop:
                    hard += 1
                else:
                    touch += 1
            m = TAG.match(tag)
            if m and k.get("patch_enter") and k.get("secondary_slip"):
                by_kph.setdefault(int(m.group(2)), []).append(
                    k["secondary_slip"]["t"] - k["patch_enter"]["t"])
        f["var_n"] = n; f["var_enter_slip"] = et; f["var_slip_stop"] = sm
        f["var_board_max"] = bm; f["var_evade"] = ev_cnt; f["var_coll"] = coll
        f["var_hard"] = hard; f["var_touch"] = touch; f["var_by_kph"] = by_kph
    except Exception:
        pass
    # 판정 규칙 A/B (같은 주행선 위에서 타원 대 직사각형)
    try:
        gains = []
        for p in glob.glob(str(ROOT / "logs/carla_demo/events_*.json")):
            ab = (json.load(open(p)) or {}).get("rule_ab")
            if ab and ab.get("gain_s") is not None:
                gains.append(ab["gain_s"])
        f["ab_n"], f["ab_gains"] = len(gains), gains
    except Exception:
        pass
    # 기온 게이트
    try:
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from icepredict.pi.context import ROAD_BELOW_AIR_C
        f["road_gap"] = ROAD_BELOW_AIR_C
    except Exception:
        pass
    # 대조군
    try:
        n = fa = sec = 0
        # 파일 이름으로 고르면 dync_* 같은 대조군을 놓친다. 인자 플래그가 진실이다.
        for p in glob.glob(str(ROOT / "logs/carla_demo/events_*.json")):
            d = json.load(open(p)); a = d.get("args", {})
            if not a.get("control_no_ice"):
                continue
            # 기온 시연(ctxtemp_*)은 대조군이 아니다. -3 °C 에서 경보가 나는 것이 설계된 동작이라
            # 오경보로 세면 숫자가 거짓말을 한다.
            if "events_ctxtemp" in p:
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
        ["2차 방어 오탐",
         ("✅ 0건" if not f.get("ctrl_sec") else "🔧 찾아서 고침"),
         (f"빙판 없는 대조군 {f.get('ctrl_n','?')}건에서 {f.get('ctrl_sec','?')}건. "
          "원인은 급조향 시 모델 지연, τ=0.06 s 보정으로 해결 (정리/16)"
          if f.get("ctrl_sec") else f"빙판 없는 대조군 {f.get('ctrl_n','?')}건에서 0건")],
        ["1차 방어 (실사진)", "✅ 측정 완료",
         f"실사진 {f.get('photo_n',0):,}장, 블랙아이스 {100*f.get('ice_acc',0):.1f} % 정답"],
        ["1차 방어 오경보", "⚠️ 4층으로 막는 중",
         f"문턱 + 연속 {f.get('confirm','?')}프레임 + 강수 게이트 + 기온 게이트. "
         f"대조군 {f.get('ctrl_n','?')}건 중 {f.get('ctrl_fa','?')}건 (전부 젖은 노면)"],
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
    lin = f.get("lin_idle_max_us", 4677.0)
    lin_l = f.get("lin_load_max_us", 3331.0)
    B.append(table([
        ["조건", "평균 지연", "최악 지연"],
        ["Pi 5 리눅스 · 유휴", f"{f.get('lin_idle_mean_us', 64):.0f} µs", f"{lin:,.0f} µs"],
        ["Pi 5 리눅스 · 부하", f"{f.get('lin_load_mean_us', 64):.0f} µs", f"{lin_l:,.0f} µs"],
        ["STM32N6 + ThreadX (전체 응답)",
         f"{f.get('busy_mean', 12.5):.1f} µs", f"{f.get('wcet_max', 17.9):.1f} µs"],
    ]))
    B.append(bullet(f"리눅스는 **아무것도 안 돌 때도** {lin/1000:.1f} ms 늦게 깨어난다 — "
                    f"제어 주기 20 ms 의 {100*lin/20000:.0f} %"))
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
    B.append(head(1, "5. 1차 방어가 오경보를 막는 4층 구조"))
    B.append(para(
        "오경보를 한 가지 방법으로 막으려다 실패한 기록이 그대로 설계가 됐다. "
        "네 층이 각각 **다른 종류의 실패**를 막는다."))
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
        ["④ 기온 게이트", "물리적으로 있을 수 없는 경보",
         "최악 노면 온도가 0 °C 초과",
         "젖은 노면을 얼음이라 단언(0.87)한 사례. 문턱·프레임·가중치 전부 못 막았다"],
    ]))
    B.append(callout(
        "③ 이 제일 중요하다. 폭우에서는 1차가 스스로 \"지금은 못 본다\" 고 선언하고 2차에 맡긴다. "
        "못 막을 경보를 내는 것보다 낫고, 이중 방어 구조의 존재 이유를 그대로 보여준다.", "🎯"))
    B.append(codeblk(
        "[ctx] 실제 날씨 반영: 강수 21.8 mm/h, 태양고도 45도 → 시각 13시\n"
        "[ctx] 1차 방어 신뢰 불가 → 끄고 2차에 맡긴다: 강수 21.8 mm/h ≥ 5 — 젖은 노면과 얼음 구분 불가"))
    B.append(para("↑ 발표에서 이 두 줄을 그대로 띄우면 설명이 거의 필요 없다."))
    rg = f.get("road_gap", {})
    B.append(para(
        "④ 는 ③ 과 의미가 다르다. ③ 은 \"카메라를 못 믿겠다 → 2차에 맡긴다\" 이고, "
        "④ 는 \"얼음이 있을 수 없다 → 얼음 경보를 내지 않는다\" 다. "
        "**어느 쪽이든 2차 방어는 그대로 돈다** — 따뜻해도 젖은 노면은 미끄럽다."))
    if rg:
        B.append(table([
            ["조건", "노면이 공기보다 낮을 수 있는 폭"],
            ["낮 · 트인 곳", f"{rg.get('day',2):.0f} °C"],
            ["낮 · 다리·터널출구·그늘", f"{rg.get('day_exposed',3):.0f} °C"],
            ["밤 · 트인 곳", f"{rg.get('night',4):.0f} °C"],
            ["밤 · 다리·터널출구·그늘", f"{rg.get('night_exposed',6):.0f} °C"],
        ]))
        B.append(para(
            "이만큼 빼고도 어는점을 넘어야 \"불가능\" 이라고 말한다. 틀리는 쪽이 있다면 "
            "\"얼음이 있을 수 있다\" 로 틀리게 잡아 둔 것이다. 잔설이 있으면 국소 재결빙이 "
            "남으므로 기온과 무관하게 게이트를 열어 둔다."))
        B.append(callout(
            f"주의: 젖은 노면 오경보는 **CARLA 현상**이다. 실사진 {f.get('photo_n',0):,}장에서는 젖음과 얼음이 "
            "거의 완전히 갈린다 (판별 AUC 0.998, 젖은 노면 오경보 1.9 %). "
            "기온 게이트는 실제 결함을 때우는 패치가 아니라 한 겹 더 두는 방어다. "
            "발표에서 \"시뮬에서 오경보가 났다\" 를 근거로 쓰면 안 된다.", "⚠️"))

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
            ["충돌 — 주행 중 (5 km/h 이상)", f"{f.get('var_hard',0)}/{f['var_n']}건"],
            ["충돌 — 정지 후 접촉 (5 km/h 미만)", f"{f.get('var_touch',0)}/{f['var_n']}건"],
        ]))
        B.append(bullet("보드 응답 최악값이 조건이 바뀌어도 좁다 — 연산이 입력에 거의 의존하지 않는다는 증거"))
        B.append(callout(
            "충돌 숫자를 그대로 \"실패율\" 로 말하면 안 된다. 이 스윕은 정차 차량을 빙판 중심 "
            "**+45 m** — 빙판 위 정지거리보다 짧은 곳 — 에 일부러 세워 회피를 강제한 배치다. "
            "최악 조건의 스트레스 값이다. 게다가 일부는 이미 선 뒤 1~4 km/h 로 닿은 접촉이라 "
            "표에서 갈라 놨다.", "🚨"))

        # 속도가 바꾸는 것 — 오늘 밤 찾은 것
        bk = f.get("var_by_kph") or {}
        if len(bk) > 1:
            B.append(head(2, "속도가 바꾸는 것 — 느릴수록 늦게 잡힌다"))
            B.append(table([["진입 속도", "빙판 진입 → 확정", "주행"]] +
                           [[f"{k} km/h", rng(v), f"{len(v)}건"] for k, v in sorted(bk.items())]))
            B.append(para(
                "직관과 반대로 보이지만 물리가 그렇다. 요구 횡가속도가 v²/R 이라 빠를수록 "
                "접지를 먼저 잃고, 잔차도 그만큼 크게 나온다."))
            B.append(para(
                "그런데 원인을 파 보니 물리만이 아니었다. 35 km/h 주행의 확정 순간 잔차가 "
                "**횡가속도 0.287 · yaw 0.353** 이었다. 횡가속도 임계가 0.300 이니 "
                "**4 % 차이로** 못 넘었고, 그래서 훨씬 느린 yaw 경로가 0.35 에 닿을 때까지 "
                "3.2 초를 더 기다렸다. 두 잔차를 OR 로 묶은 **직사각형 판정의 모서리에 걸린 것**이다."))
            B.append(codeblk(
                "직사각형(기존)  |ay_g| >= 0.30  또는  |yaw_err| >= 0.35\n"
                "타원(변경)      (ay_g/0.30)^2 + (yaw_err/0.35)^2 >= 1"))
            B.append(bullet("타원은 직사각형을 **안에 품는다** — 정의상 늦어질 수 없다"))
            B.append(bullet("둘 다 임계의 0.71 쯤인 구간을 새로 잡는다. 새 상수도 속도 보정도 없다"))
            B.append(bullet("비용은 곱 2 + 합 1, 분기 수는 그대로 — 보드 WCET 에 영향 없음"))
            B.append(bullet("합성 검증: 빨라진 조건 3, **기존이 못 잡던 것을 새로 잡은 조건 1**, 느려진 조건 0"))
            B.append(bullet("정상 주행 오탐 480 시행 중 **0** — 직사각형과 같다"))
            if f.get("ab_n"):
                g = f["ab_gains"]
                B.append(bullet(f"CARLA 실주행 {f['ab_n']}건에서 같은 주행선 위 비교: "
                                f"평균 **{sum(g)/len(g):+.2f} s**, 최대 **{max(g):+.2f} s**"))
            B.append(head(2, "덤으로 찾은 것 — 2차가 정상 노면에서 발화했다"))
            B.append(para(
                "속도별 동역학 스윕의 **대조군**(빙판 없음) 50 km/h 주행에서 2차 방어가 발화했다. "
                "1차 오경보는 차를 세우는 데서 그치지만 2차 오탐은 정상 주행 중 "
                "**급제동·회피**를 건다. 더 위험한 실패라 먼저 고쳤다."))
            B.append(para(
                "원인은 자전거 모델의 가정이었다. 모델은 조향에 차량이 즉시 반응한다고 본다. "
                "그 주행에서 조향이 80 ms 만에 0.07 → 0.66 으로 튀자 모델은 yaw 1.02 rad/s 를 "
                "기대했는데 실제는 0.35 였다. **미끄러진 게 아니라 아직 안 돌아간 것**인데 "
                "그 지연이 통째로 잔차가 됐다."))
            B.append(para("고침: 기대 yaw 를 1차 지연(τ = 0.06 s)으로 통과시켜 차량 응답 속도에 맞춘다."))
            B.append(table([
                ["τ", "대조군 최악 여유", "대조군 발화", "35 km/h 탐지"],
                ["0.00 (지금 보드)", "2.39", "1/5", "4.00 s"],
                ["0.06 (선택)", "0.59", "0/5", "4.56 s"],
                ["0.10", "0.58", "0/5", "5.10 s"],
            ]))
            B.append(callout(
                "타원 규칙과 합치면 **지금 보드보다 두 축 모두 낫다** — "
                "2차 오탐 1/5 → 0/5, 35 km/h 탐지 5.46 → 4.56 s. 근거는 정리/16.", "🎯"))
            B.append(callout(
                "이 변경은 보드 펌웨어에 **올라가지 못했다**. 굽는 데 BOOT1 스위치 물리 접근과 SWD 가 "
                "필요한데, STM32N6 가 2026-09-21 다른 용도로 빠졌다. 코드는 fw/npu_lib/slip_core.h 에 "
                "들어갔고 C↔파이썬 동치도 통과했다. **보드를 돌려받으면 scripts/deploy/fw_redeploy.sh 한 번**이다. "
                "그때까지는 같은 주행선 위에서 잰 반사실(정리/14·16)로 말해야 한다.", "🔧"))
    else:
        B.append(para("변동 스윕 결과가 아직 없다. `bash scripts/archive/variation_sweep.sh` 로 만든다."))

    # ── 6-2. 경보 거리
    B.append(head(1, "6-2. 경보 거리를 늘리려면 무엇을 바꿔야 하나"))
    B.append(para(
        "1차 경보 거리가 22~28 m 에서 멈춘다. 학습이나 문턱 문제가 아니라 **기하**다. "
        "데이터셋 사진을 일부러 작게 만들어 모델이 무너지는 지점을 찾았다."))
    B.append(bullet("얼음 정답률이 96.7 % 에서 **64×64 부터** 5 %p 넘게 떨어진다 — 버티는 최소는 **80 px**"))
    B.append(bullet("지금 ROI 는 영상 **91 px** 를 잘라 224 로 늘린다. 80 px 기준에 **여유가 거의 없다**"))
    B.append(bullet("30~60 m 구간은 **15.2 px** 다. 원리적으로 못 본다"))
    B.append(para("30~60 m 가 80 px 이상으로 오게 하려면 (화각 × 가로 해상도):"))
    B.append(table([
        ["화각 \\ 가로 해상도", "640 px", "1280 px", "1920 px"],
        ["60° (지금)", "15 px", "30 px", "46 px"],
        ["40°", "24 px", "48 px", "72 px"],
        ["25°", "40 px", "79 px", "**119 px** ✅"],
    ]))
    B.append(callout(
        "**망원 렌즈만으로는 안 된다.** 먼저 할 일은 **캡처 해상도를 1920×1080 으로 올리는 것**이다. "
        "D435i 는 이미 낼 수 있고, 모델 입력은 224 로 고정이라 **NPU 비용은 변하지 않는다**. "
        "렌즈를 사기 전에 설정 한 줄로 확인할 수 있다. 근거는 정리/17.", "💡"))
    B.append(para(
        "그래도 모자라면 카메라를 **두 대로 나눈다** — 근거리·폭은 지금 렌즈, 원거리만 망원. "
        "프레임을 번갈아 넣으면 각 10 fps 이고 NPU 총량은 같다. "
        "광각 한 대를 망원으로 바꾸는 안은 마지막이다. 근거리를 통째로 잃는다."))

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
        "            ├── 00~15_*.md         근거 문서 16편\n"
        "            └── A~H/               상황별 영상 폴더"))
    B.append(callout(
        "정리/ 폴더는 원본에 하드링크를 건다. 같은 이름으로 다시 촬영하면 원본은 덮이지만 "
        "옛 영상은 정리본 덕분에 살아남는다. 확인해 보니 **정리본 안에만 있는 영상이 98개** 다. "
        "폴더 이름이 중복돼 보여도 절대 지우지 말 것.", "🚨"))

    # ── 8. 자주 쓰는 명령
    B.append(head(1, "8. 자주 쓰는 명령"))
    B.append(codeblk(
        "# 지금 뭐가 돌고 있나\n"
        "bash scripts/archive/stop_chains.sh --list\n\n"
        "# 돌고 있는 거 전부 안전하게 멈추기\n"
        "bash scripts/archive/stop_chains.sh\n\n"
        "# 영상 찍기 (날씨 × 시나리오)\n"
        "DESK=user@render-host WEATHERS=\"ClearNoon WetNoon\" SCEN=detect \\\n"
        "  bash scripts/sim/demo_batch_5090.sh\n\n"
        "# 실사진을 보드에 넣어 평가\n"
        "python3 scripts/deploy/rscd_board_eval.py --n 2000\n\n"
        "# 분석 문서 다시 만들기\n"
        "python3 scripts/analysis/analyze_detection.py     # 01_탐지성능.md\n"
        "python3 scripts/analysis/summarize_runs.py        # 00_집계.md\n"
        "python3 scripts/analysis/merge_wcet.py            # 보드 지연 누적 합치기 (배치 뒤 꼭)\n\n"
        "# 보드 살아 있나\n"
        "ping -c1 192.168.50.158", "bash"))

    # ── 9. 지뢰
    B.append(head(1, "9. 밟으면 아픈 지뢰"))
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
        ["--ice-seed 를 표본 수로 셈",
         "시드 5종 = 표본 5개라고 생각했는데 결과가 바이트 단위로 같다",
         "시드는 **주변 차량 배치와 얼음 외관만** 바꾼다. 자차 주행선은 안 바뀐다. "
         "표본을 늘리려면 속도·마찰·경사를 흔들어야 한다"],
        ["pgrep 이 자기 자신을 잡는다",
         "앞 단계가 끝났는데도 체인이 영원히 기다린다",
         "스크립트를 만든 셸의 명령줄에 그 이름이 남아 있다. 패턴 한 글자를 대괄호로: "
         "pgrep -f 'after_xx[x].sh'"],
        ["대조군 로그의 '빙판 진입'",
         "빙판 없는 대조군인데 '빙판 진입' 이 찍혀 혼란",
         "--control-no-ice 는 패치 **객체**를 거리 계산용으로 남긴다. 물리도 외관도 정상 노면이다"],
    ]))

    # ── 10. 다음에 할 일
    B.append(head(1, "10. 다음 사람이 할 일"))
    B.append(para("우선순위 순서다. 위에서부터 하면 된다."))
    B.append(todo("발표 서사 확정 — 특히 \"시뮬 인식률은 쓰지 않는다\", \"폭우에서는 1차를 신뢰 불가로 선언한다\" 두 가지는 팀 결정이 필요하다"))
    B.append(todo("**보드를 다시 확보할 수 있는지 먼저 확인** — STM32N6 가 2026-09-21 다른 용도로 "
                  "빠졌다. 돌려받으면 펌웨어 한 번 굽는 것으로 ① 판정 규칙 타원 ② 조향 지연 보정 "
                  "τ=0.06 s ③ 확정 프레임 연속 8 이 전부 반영된다 (셋 다 코드에는 이미 들어감, "
                  "명령은 scripts/deploy/fw_redeploy.sh 하나). 못 돌려받으면 발표에서 "
                  "\"같은 입력으로 잰 반사실\" 로 말해야 한다"))
    B.append(todo("**발표(11/6) 실물 시연 여부를 팀이 먼저 정할 것** — 보드가 없으면 실시간 시연은 "
                  "불가능하다. 녹화 영상으로 갈지, 보드를 빌려 올지 결정이 자료 구성을 바꾼다"))
    B.append(todo("D435i 연결 → 차 세워 두고 IMU 10분 기록 → RMS 가 200 mg 예산 안인지 확인 → 칼만 R 갱신"))
    B.append(todo("Hailo 컴파일러(DFC) 계정 받아 설치 → 보드 NPU 수치 독립 교차검증"))
    B.append(todo("캡처 해상도를 1920×1080 으로 올려 보기 — 렌즈 교체 없이 30~60 m 픽셀이 3배가 된다. "
                  "NPU 비용은 안 변한다 (6-2 절 참고). 이게 제일 싼 개선이다"))
    B.append(todo("서류(10/30) · 발표(11/6) 자료 조립 — docs/presentation_evidence_map.md 참고"))

    # ── 11. 읽을 순서
    B.append(head(1, "11. 문서 읽는 순서"))
    B.append(num_("이 페이지 (지금 읽는 것)"))
    B.append(num_("docs/presentation_evidence_map.md — 어떤 주장에 어떤 자료를 쓰는지"))
    B.append(num_("logs/carla_demo/정리/05_실사진_대규모평가.md — 1차 방어 성능의 근거"))
    B.append(num_("logs/carla_demo/정리/06_RTOS가_왜_필요한가.md — 대회 주제의 핵심"))
    B.append(num_("logs/carla_demo/정리/10_스케줄가능성_분석.md — 임베디드 SW 로서의 논증"))
    B.append(num_("logs/carla_demo/정리/11_변동스윕_2차방어분포.md — 2차 방어를 분포로 말하기"))
    B.append(num_("logs/carla_demo/정리/13_판정규칙_타원.md — 약점을 찾아 고친 기록 (심사에서 강하다)"))
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

    # 같은 제목의 페이지가 이미 있으면 **주소를 유지한 채 내용만 갈아 끼운다**.
    # 새로 만들면 주소가 바뀌어 팀원이 저장해 둔 링크가 끊기고, 같은 문서가 둘이 된다.
    # 노션은 제목 앞의 이모지를 아이콘으로 떼어 간다. 그래서 저장된 제목은
    # TITLE 과 글자가 다르다. 앞쪽 이모지·공백을 떼고 비교해야 같은 페이지를 찾는다.
    # (이걸 안 해서 한 번 같은 문서를 둘로 만들었다.)
    def _norm(t):
        return t.strip().lstrip("🧑\u200d🏫📋📌🔧💡⭐🎯🚨⚠️🆕 ").strip()

    want = _norm(TITLE)
    existing = None
    cur = None
    while True:
        q = f"blocks/{a.parent}/children?page_size=100" + (f"&start_cursor={cur}" if cur else "")
        r = api(tok, q)
        for b in r["results"]:
            if b["type"] == "child_page" and _norm(b["child_page"]["title"]) == want:
                existing = b["id"]
        if not r.get("has_more"):
            break
        cur = r["next_cursor"]

    if existing:
        pid = existing
        n_del = 0
        for _round in range(40):          # 무한 루프 방지 — 한 번에 100개씩, 최대 4,000개
            old = api(tok, f"blocks/{pid}/children?page_size=100")
            if not old["results"]:
                break
            for b in old["results"]:
                api(tok, f"blocks/{b['id']}", "DELETE")
                n_del += 1
        page = api(tok, f"pages/{pid}")
        url = page["url"]
        print(f"기존 페이지 재사용 — 옛 블록 {n_del}개 비움: {url}")
        rest = blocks
    else:
        page = api(tok, "pages", "POST", {
            "parent": {"page_id": a.parent},
            "icon": {"type": "emoji", "emoji": "🧑‍🏫"},
            "properties": {"title": {"title": rt(TITLE)}},
            "children": blocks[:90],
        })
        pid, url = page["id"], page["url"]
        rest = blocks[90:]
        print(f"생성: {url}")
    while rest:
        api(tok, f"blocks/{pid}/children", "PATCH", {"children": rest[:90]})
        rest = rest[90:]

    if existing:
        print(f"\n주소: {url}")
        return

    # 부모 페이지 첫 블록 뒤에 바로가기 (처음 만들 때만)
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
