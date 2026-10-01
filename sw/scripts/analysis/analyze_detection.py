#!/usr/bin/env python3
"""주행 trace/events 를 모아 1차(카메라) 탐지 성능을 정량화한다.

지금까지의 요약 스크립트는 "몇 번 멈췄나"를 셌지만, 발표에서 방어해야 하는 숫자는
**얼마나 멀리서 봤나(경보 거리)**와 **얼마나 자주 헛봤나(오경보)** 다. 이 스크립트는
- 경보 거리: primary_warning 이벤트의 patch 가장자리까지 거리 / 그때 속도 / 확보한 시간여유(TTC)
- 정지 여유: 정지 시점의 가장자리까지 거리 (음수면 빙판을 밟고 멈춘 것)
- 오경보: 빙판 밖(inside=False)이고 가장자리까지 멀리 있는 구간에서 risk 가 임계값을 넘은 프레임 비율
- 확률 분해: p=[normal, wet, black_ice, pothole] 중 얼음 확률이 거리별로 어떻게 자라는지
를 날씨·시나리오별로 뽑아 마크다운으로 낸다.

사용법: python3 sw/scripts/analysis/analyze_detection.py [--out logs/carla_demo/정리/01_탐지성능.md]
"""
from __future__ import annotations

import pathlib
import argparse
import glob
import json
import os
import statistics as st
from collections import defaultdict

DEMO = str(pathlib.Path(__file__).resolve().parents[3] / "logs/carla_demo")
# 빙판 밖이라고 확실히 말할 수 있는 거리. 패치 가장자리에서 이만큼 앞이면 노면은 정상이다.
CLEAN_EDGE_M = 45.0
# 경보 임계값을 모르는 주행을 위한 기본값. 실제 발화 risk 의 최솟값으로 덮어쓴다.
DEFAULT_TH = 0.44

# 집계에 섞이면 인식률을 부풀리는 주행들. gt 는 --gt-detect 치트(거리 기반)라 카메라 성능이 아니고,
# primary*/secondary_only 는 9/16~18 레거시, dyncal_* 은 1·2차 모두 끈 보정 주행이다.
EXCLUDE_PREFIX = ("gt", "primary", "secondary_only", "dyncal_", "demo")
EXCLUDE_SCEN = ("visual", "rscdtex_visual", "icerender", "icerender_n6", "icerender_n6npu", "?")
# 문서가 쓰는 오경보 판정 규칙: 워밍업이 풀리자마자 최대 거리에서 발화한 것.
FP_EDGE_M = 32.0

# 운영 문턱은 `icepredict.pi.context` 가 결빙 prior 로 정한다. 분석에서 하드코딩하면
# 교정할 때마다 문서와 어긋나므로 거기서 읽어 온다 (데모 기본 조건 기준).
try:
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "src"))
    from icepredict.pi.context import WeatherObs as _W, LocationCtx as _L, build_context as _bc
    TH_OPS = _bc(_W(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                 _L(feature="bridge", hour=5)).threshold
except Exception:
    TH_OPS = 0.60
TH_LEGACY = 0.441        # 2026-09-21 교정 이전 값. 옛 주행과 비교할 때만 쓴다.
# 클래스 순서는 ['normal', 'wet', 'black_ice', 'pothole'] 이다. 얼음은 0번이 아니라 **2번**이다.
# 처음에 0번을 얼음으로 읽어 "폭우에서 p(ice)=0.02" 라는 정반대 결론을 냈었다.
ICE_I, NORMAL_I, WET_I = 2, 0, 1


def load_runs() -> dict[str, dict]:
    runs: dict[str, dict] = {}
    for f in sorted(glob.glob(os.path.join(DEMO, "events_*.json"))):
        tag = os.path.basename(f)[len("events_"):-len(".json")]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        runs.setdefault(tag, {})["events"] = d
    for f in sorted(glob.glob(os.path.join(DEMO, "trace_*.json"))):
        tag = os.path.basename(f)[len("trace_"):-len(".json")]
        try:
            runs.setdefault(tag, {})["trace"] = json.load(open(f))
        except Exception:
            pass
    return runs


def split_tag(tag: str) -> tuple[str, str]:
    """demo 태그를 (날씨, 시나리오)로 나눈다. 시나리오 이름은 알려진 접미사 목록으로 맞춘다."""
    for scen in ("rscdtex_detect", "miss_rtos_traffic", "detect_traffic_60kph",
                 "nodefense_traffic", "detect_traffic", "miss_rtos", "nodefense_60kph",
                 "detect_60kph", "nodefense", "detect", "miss", "visual"):
        if tag.endswith("_" + scen):
            return tag[: -(len(scen) + 1)], scen
        if tag == scen:
            return "?", scen
    if "_" in tag:
        w, s = tag.split("_", 1)
        return w, s
    return tag, "?"


def events_of(d) -> list[dict]:
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("events", "log"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def args_of(d) -> dict:
    return d.get("args", {}) if isinstance(d, dict) else {}


def first(evs: list[dict], name: str) -> dict | None:
    for e in evs:
        if e.get("event") == name:
            return e
    return None


def analyze(tag: str, r: dict) -> dict | None:
    evs = events_of(r.get("events"))
    tr = r.get("trace") or []
    if not evs and not tr:
        return None
    w, scen = split_tag(tag)
    a = args_of(r.get("events"))
    half = float(a.get("patch_len", 40.0)) / 2.0  # dist_m 은 패치 중심까지. 가장자리는 절반 뺀 값.

    warm = float(a.get("warmup", 3.0))
    warn = first(evs, "primary_warning")
    stop = first(evs, "stopped")
    enter = first(evs, "patch_enter")
    slip = first(evs, "secondary_slip")
    emerg = first(evs, "emergency_mode")
    crash = first(evs, "collision")
    spin = first(evs, "spin")
    dep = first(evs, "lane_departure")
    lat = first(evs, "board_slip_latency")

    out: dict = {
        "tag": tag, "weather": w, "scen": scen,
        "target_kph": a.get("target_kph"), "friction": a.get("friction"),
        "primary_off": bool(a.get("disable_primary")),
        "warn_edge_m": None, "warn_kph": None, "warn_risk": None, "ttc_s": None,
        "stop_edge_m": None, "entered": enter is not None,
        "slip_t": slip.get("t") if slip else None,
        "emerg": emerg is not None, "crash": crash is not None,
        "spin": spin is not None, "departure": dep is not None,
        "board_us": (lat or {}).get("mean_us") or (lat or {}).get("avg_us"),
        "fp_rate": None, "clean_frames": 0, "risk_clean_p95": None,
        "ice_p_at": {},
    }

    out["control"] = bool(a.get("control_no_ice"))
    # 대조군은 빙판이 없으므로 **어느 프레임이든** 문턱을 넘으면 그것이 오경보다.
    # 워밍업 이후 프레임의 최대 위험도를 남겨 두면 문턱을 사후에 쓸어볼 수 있다.
    _post = [float(x.get("risk", 0)) for x in tr if x.get("t", 0) >= warm]
    out["risk_max_post"] = max(_post) if _post else None
    out["traffic"] = int(a.get("traffic", 0) or 0)
    out["excluded"] = tag.startswith(EXCLUDE_PREFIX) or scen in EXCLUDE_SCEN
    out["false_alarm"] = False
    if warn:
        edge = warn.get("dist_to_edge_m")
        if edge is None and warn.get("dist_to_patch_m") is not None:
            edge = warn["dist_to_patch_m"] - half
        kph = warn.get("speed_kph")
        out["warn_edge_m"] = edge
        out["warn_kph"] = kph
        out["warn_risk"] = warn.get("risk")
        if edge is not None and kph:
            out["ttc_s"] = edge / (kph / 3.6)
        wt = warn.get("t", 0.0)
        out["warn_t"] = wt
        out["false_alarm"] = bool(wt < warm + 0.5 or (edge is not None and edge > FP_EDGE_M))

    if stop:
        e = stop.get("dist_to_edge_m")
        if e is None and stop.get("dist_to_patch_m") is not None:
            e = stop["dist_to_patch_m"] - half
        if e is None:
            # trace 마지막 정지 프레임에서 추정
            for s in reversed(tr):
                if s.get("state") == "STOPPED":
                    e = s.get("dist_m", 0) - half
                    break
        out["stop_edge_m"] = e

    # ---- 오경보: 빙판에서 충분히 먼 구간에서 임계값을 넘은 프레임 ----
    th = out["warn_risk"] if out["warn_risk"] else DEFAULT_TH
    clean = [s for s in tr
             if not s.get("inside")
             and s.get("t", 0) >= warm
             and (s.get("dist_m", 0) - half) >= CLEAN_EDGE_M]
    if clean:
        risks = [float(s.get("risk", 0)) for s in clean]
        out["clean_frames"] = len(clean)
        out["fp_rate"] = sum(1 for x in risks if x >= th) / len(risks)
        risks.sort()
        out["risk_clean_p95"] = risks[int(0.95 * (len(risks) - 1))]

    # ---- 거리 구간별 확률 (얼음은 2번 열) ----
    out["normal_p_at"] = {}
    buckets = [(70, 90), (50, 70), (35, 50), (25, 35), (15, 25), (5, 15)]
    for lo, hi in buckets:
        sel = [s for s in tr
               if s.get("p") and not s.get("inside")
               and lo <= (s.get("dist_m", 0) - half) < hi]
        if sel:
            out["ice_p_at"][f"{lo}-{hi}m"] = sum(float(s["p"][ICE_I]) for s in sel) / len(sel)
            out["normal_p_at"][f"{lo}-{hi}m"] = sum(float(s["p"][NORMAL_I]) for s in sel) / len(sel)
    return out


def fmt(x, n=1):
    return "-" if x is None else f"{x:.{n}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(DEMO, "정리", "01_탐지성능.md"))
    a = ap.parse_args()

    runs = load_runs()
    rows = [x for x in (analyze(t, r) for t, r in sorted(runs.items())) if x]
    rows = [r for r in rows if not r["excluded"]]
    ctrl = [r for r in rows if r["control"]]
    rows = [r for r in rows if not r["control"]]
    prim = [r for r in rows if not r["primary_off"]]

    L: list[str] = []
    L.append("# 1차(카메라/NPU) 탐지 성능 정량화\n")
    L.append(f"주행 {len(rows)}건, 1차가 켜진 주행 {len(prim)}건. "
             f"거리는 모두 **빙판 가장자리까지**이며 패치 중심 거리에서 절반 길이를 뺀 값이다.\n")

    # ---------- 음성 대조군 ----------
    if ctrl:
        L.append("\n## 음성 대조군 — 빙판이 **없는** 같은 경로\n\n")
        L.append("지금까지 모든 주행에 빙판이 있어 '헛보는 비율'을 잰 적이 없었다. "
                 "여기서 나오는 경보는 정의상 전부 오경보다.\n\n")
        L.append("**읽는 법**: 오경보가 나면 차가 제동·정지하면서 주행이 거기서 끝난다. "
                 "반대로 오경보가 없는 주행은 24 초를 끝까지 달린다. 그래서 아래 비율은 "
                 "**주행당 비율**이지 주행 거리당 비율이 아니다. 노출 시간이 서로 다르므로 "
                 "km 당 오경보로 환산하지 말 것.\n\n")
        _dur = [(r["tag"], r.get("slip_t")) for r in ctrl]
        _ = _dur
        fa1 = [r for r in ctrl if r["warn_edge_m"] is not None]
        fa2 = [r for r in ctrl if r["slip_t"] is not None]
        L.append(f"| 항목 | 값 |\n|---|---|\n")
        L.append(f"| 대조군 주행 | {len(ctrl)}건 (주변차량 있는 주행 {sum(1 for r in ctrl if r['traffic'])}건) |\n")
        L.append(f"| **1차 오경보** | {len(fa1)}건 = **{100*len(fa1)/len(ctrl):.0f}%** |\n")
        L.append(f"| **2차 오탐(IMU 미끄러짐)** | {len(fa2)}건 = **{100*len(fa2)/len(ctrl):.0f}%** |\n")
        L.append(f"| 차로이탈·스핀 | {sum(1 for r in ctrl if r['departure'] or r['spin'])}건 |\n")
        L.append("\n### 문턱을 바꾸면 대조군 오경보가 어떻게 되나\n\n")
        L.append("빙판이 없으므로 워밍업 이후 **어느 프레임이든** 문턱을 넘으면 오경보다. "
                 "주행별 최대 위험도로 쓸어본 결과다.\n\n")
        L.append("| 문턱 | 오경보 주행 | 비율 |\n|---|---|---|\n")
        _rm = [r["risk_max_post"] for r in ctrl if r.get("risk_max_post") is not None]
        for _th in sorted({TH_LEGACY, 0.50, 0.55, 0.57, TH_OPS, 0.65, 0.70}):
            _n = sum(1 for x in _rm if x >= _th)
            mark = (" **(현행)**" if abs(_th - TH_OPS) < 1e-6 else
                    (" (교정 전)" if abs(_th - TH_LEGACY) < 1e-6 else ""))
            L.append(f"| {_th:.3f}{mark} | {_n}/{len(_rm)} | {100*_n/max(len(_rm),1):.0f}% |\n")
        if _rm:
            L.append("\n| 주행 | 최대 위험도 |\n|---|---|\n")
            for r in sorted(ctrl, key=lambda x: -(x.get("risk_max_post") or 0)):
                if r.get("risk_max_post") is None:
                    continue
                L.append(f"| {r['tag']} | {r['risk_max_post']:.3f} |\n")

        if fa1 or fa2:
            L.append("\n| 주행 | 1차 경보 시각 s | risk | 2차 발화 시각 s |\n|---|---|---|---|\n")
            for r in ctrl:
                if r["warn_edge_m"] is None and r["slip_t"] is None:
                    continue
                L.append(f"| {r['tag']} | {fmt(r.get('warn_t'),2)} | {fmt(r['warn_risk'],3)} | "
                         f"{fmt(r['slip_t'],2)} |\n")
        else:
            L.append("\n빙판이 없을 때 1차도 2차도 한 번도 발화하지 않았다. "
                     "문서가 채택한 측정 기준(정상 교통 상황에서의 오탐률)을 처음으로 만족한 결과다.\n")

    # ---------- 경보 거리 ----------
    allfired = [r for r in prim if r["warn_edge_m"] is not None]
    fired = [r for r in allfired if not r["false_alarm"]]
    fps = [r for r in allfired if r["false_alarm"]]
    L.append("\n## 경보 거리와 시간여유\n")
    if fired:
        eds = [r["warn_edge_m"] for r in fired]
        ttcs = [r["ttc_s"] for r in fired if r["ttc_s"]]
        L.append(f"- 1차가 발화한 주행 {len(allfired)}/{len(prim)}건 중, "
                 f"**제대로 된 탐지 {len(fired)}건 / 오경보 {len(fps)}건**\n")
        L.append(f"- 오경보 판정 기준: 워밍업 해제 뒤 0.5 s 안에 발화했거나, "
                 f"빙판 가장자리 {FP_EDGE_M:.0f} m 밖에서 발화한 것\n")
        L.append(f"- 경보 거리 중앙값 **{st.median(eds):.1f} m** "
                 f"(최소 {min(eds):.1f}, 최대 {max(eds):.1f})\n")
        if ttcs:
            L.append(f"- 확보한 시간여유 중앙값 **{st.median(ttcs):.2f} s** "
                     f"(최소 {min(ttcs):.2f}, 최대 {max(ttcs):.2f})\n")
        L.append("\n| 주행 | 날씨 | 시나리오 | 경보거리 m | 속도 km/h | 시간여유 s | 정지여유 m | 발화 risk |\n")
        L.append("|---|---|---|---|---|---|---|---|\n")
        for r in sorted(fired, key=lambda x: x["warn_edge_m"]):
            L.append(f"| {r['tag']} | {r['weather']} | {r['scen']} | {fmt(r['warn_edge_m'])} | "
                     f"{fmt(r['warn_kph'],0)} | {fmt(r['ttc_s'],2)} | {fmt(r['stop_edge_m'])} | "
                     f"{fmt(r['warn_risk'],3)} |\n")
    else:
        L.append("- 1차 발화 기록 없음\n")

    if fps:
        L.append(f"\n### 오경보 {len(fps)}건 — 주행 시작 직후 먼 거리에서 발화\n\n")
        L.append("| 주행 | 발화 시각 s | 가장자리 m | risk |\n|---|---|---|---|\n")
        for r in sorted(fps, key=lambda x: x.get("warn_t") or 0):
            L.append(f"| {r['tag']} | {fmt(r.get('warn_t'),2)} | {fmt(r['warn_edge_m'])} | {fmt(r['warn_risk'],3)} |\n")
        L.append("\n이 주행들은 폭우·야간·젖은 노면이다. 반사도 항이 문턱을 넘겨 놓은 것으로, "
                 "`logs/carla_demo/정리/02_융합가중치.md` 의 문턱 0.60 권고가 정확히 이 현상을 겨냥한다.\n")

    missed = [r for r in prim if r["warn_edge_m"] is None]
    if missed:
        L.append(f"\n### 1차가 켜져 있었는데 발화하지 않은 주행 ({len(missed)}건)\n\n")
        L.append("| 주행 | 빙판 진입 | 2차 미끄러짐 | 충돌 | 스핀 |\n|---|---|---|---|---|\n")
        for r in missed:
            L.append(f"| {r['tag']} | {'예' if r['entered'] else '아니오'} | "
                     f"{'예' if r['slip_t'] is not None else '아니오'} | "
                     f"{'예' if r['crash'] else '아니오'} | {'예' if r['spin'] else '아니오'} |\n")

    # ---------- 문턱을 올리면 시뮬에서 무엇이 남나 ----------
    L.append("\n## 문턱 0.60 을 이 시뮬에 그대로 적용하면\n\n")
    L.append("RSCD 실사진 분석(`02_융합가중치.md`)은 문턱 0.60 을 권한다. 그런데 시뮬의 발화 risk 는 "
             "실사진 얼음(중앙값 0.98)보다 훨씬 낮다. 그대로 올리면 무엇이 남는지 센다.\n\n")
    surv = [r for r in fired if (r["warn_risk"] or 0) >= 0.60]
    lost = [r for r in fired if (r["warn_risk"] or 0) < 0.60]
    killed_fp = [r for r in fps if (r["warn_risk"] or 0) < 0.60]
    L.append(f"- 살아남는 정상 탐지: **{len(surv)}/{len(fired)}건** "
             f"({', '.join(r['tag'] for r in surv) if surv else '없음'})\n")
    L.append(f"- 사라지는 정상 탐지: **{len(lost)}건**\n")
    L.append(f"- 사라지는 오경보: **{len(killed_fp)}/{len(fps)}건**\n\n")
    L.append("즉 실사진에서는 문턱 0.60 이 오경보만 깨끗이 걷어내지만, **CARLA 합성 얼음에서는 "
             "증거 자체가 약해서 탐지까지 같이 사라진다.** 이것이 바로 문서가 말하는 도메인 갭이며, "
             "문턱은 도메인별로 따로 잡아야 하는 값이다. 발표에서는 실사진 운영점(0.60)과 "
             f"데모 운영점({TH_OPS:.3f})을 구분해 말해야 한다.\n\n")
    L.append("| 구분 | 발화 risk 중앙값 |\n|---|---|\n")
    if fired:
        L.append(f"| 시뮬 정상 탐지 | {st.median([r['warn_risk'] for r in fired if r['warn_risk']]):.3f} |\n")
    if fps:
        L.append(f"| 시뮬 오경보 | {st.median([r['warn_risk'] for r in fps if r['warn_risk']]):.3f} |\n")
    L.append("| RSCD 실사진 얼음 | 0.980 |\n| RSCD 실사진 젖음 | 0.410 |\n")
    L.append("\n시뮬 탐지와 시뮬 오경보의 risk 가 겹친다 — 한 문턱으로는 둘을 못 가른다. "
             "실사진에서는 겹치지 않는다. 합성 얼음의 외관을 실사진 쪽으로 끌어오는 것이 "
             "문턱을 만지는 것보다 근본적이다.\n")

    # ---------- 거리별 얼음 확률 ----------
    L.append("\n## 거리별 얼음 확률 p(black_ice) — 모델이 언제부터 보이기 시작하나\n\n")
    L.append("클래스 순서는 `[normal, wet, black_ice, pothole]` 이므로 얼음은 **2번 열**이다.\n\n")
    keys = ["70-90m", "50-70m", "35-50m", "25-35m", "15-25m", "5-15m"]
    L.append("| 날씨 | " + " | ".join(keys) + " |\n" + "|---" * (len(keys) + 1) + "|\n")
    byw2: dict[str, list] = defaultdict(list)
    for r in prim:
        byw2[r["weather"]].append(r)
    for w in sorted(byw2):
        cells = []
        for k in keys:
            vals = [r["ice_p_at"][k] for r in byw2[w] if k in r["ice_p_at"]]
            cells.append(f"{st.mean(vals):.2f}" if vals else "-")
        L.append(f"| {w} | " + " | ".join(cells) + " |\n")
    L.append("\n### 같은 구간의 정상 노면 확률 p(normal) — 대조용\n\n")
    L.append("| 날씨 | " + " | ".join(keys) + " |\n" + "|---" * (len(keys) + 1) + "|\n")
    for w in sorted(byw2):
        cells = []
        for k in keys:
            vals = [r["normal_p_at"][k] for r in byw2[w] if k in r.get("normal_p_at", {})]
            cells.append(f"{st.mean(vals):.2f}" if vals else "-")
        L.append(f"| {w} | " + " | ".join(cells) + " |\n")

    # ---------- 결과 ----------
    crashes = [r for r in rows if r["crash"]]
    L.append("\n## 충돌\n\n")
    if crashes:
        L.append("| 주행 | 시나리오 | 목표 km/h | 1차 경보거리 m | 2차 발화 |\n|---|---|---|---|---|\n")
        for r in crashes:
            L.append(f"| {r['tag']} | {r['scen']} | {fmt(r['target_kph'],0)} | {fmt(r['warn_edge_m'])} | "
                     f"{'예' if r['slip_t'] is not None else '아니오'} |\n")
        L.append("\n기존 `00_집계.md` 에는 충돌 열이 아예 없어 이 주행들이 보이지 않았다.\n")
    else:
        L.append("기록된 충돌 없음.\n")

    # ---------- 보드 WCET (덮이지 않는 누적 로그) ----------
    wf = os.path.join(os.path.dirname(DEMO), "board_wcet.jsonl")
    if os.path.exists(wf):
        recs = []
        for line in open(wf):
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
        if recs:
            L.append("\n## 보드 IMU 판정 지연 (WCET 근거)\n\n")
            L.append("`events_*.json` 은 태그당 하나뿐이라 재실행하면 덮인다. "
                     "그래서 `logs/board_wcet.jsonl` 에 주행마다 한 줄씩 **덧붙인다**. "
                     "아래는 그 누적 기록 전체다.\n\n")
            L.append("| 조건 | 주행 | 샘플 합계 | 평균 µs | 주행별 최대의 평균 µs | 관측 WCET µs |\n")
            L.append("|---|---|---|---|---|---|\n")
            for name, g in (("NPU 동시", [r for r in recs if r.get("npu_busy")]),
                            ("NPU 유휴", [r for r in recs if not r.get("npu_busy")])):
                if not g:
                    continue
                L.append(f"| {name} | {len(g)} | {sum(r['samples'] for r in g)} | "
                         f"{st.mean(r['avg_us'] for r in g):.1f} | "
                         f"{st.mean(r['max_us'] for r in g):.1f} | "
                         f"**{max(r['max_us'] for r in g):.1f}** |\n")
            L.append(f"| 전체 | {len(recs)} | {sum(r['samples'] for r in recs)} | "
                     f"{st.mean(r['avg_us'] for r in recs):.1f} | "
                     f"{st.mean(r['max_us'] for r in recs):.1f} | "
                     f"**{max(r['max_us'] for r in recs):.1f}** |\n")
            nb = sum(1 for r in recs if r.get("backfilled"))
            if nb:
                L.append(f"\n{nb}건은 기존 `events_*.json` 에서 옮겨 담은 것이다. "
                         "이전 세션이 적어 둔 더 큰 값(19.3 µs)은 재실행으로 이미 덮여 복구할 수 없다 — "
                         "그래서 이 로그가 필요하다.\n")

    L.append("\n## 주행 결과 요약\n\n")
    L.append("| 시나리오 | 주행수 | 빙판진입 | 2차발화 | 비상제어 | 충돌 | 스핀 | 차로이탈 |\n")
    L.append("|---|---|---|---|---|---|---|---|\n")
    bys: dict[str, list] = defaultdict(list)
    for r in rows:
        bys[r["scen"]].append(r)
    for s in sorted(bys):
        rs = bys[s]
        L.append(f"| {s} | {len(rs)} | {sum(1 for x in rs if x['entered'])} | "
                 f"{sum(1 for x in rs if x['slip_t'] is not None)} | "
                 f"{sum(1 for x in rs if x['emerg'])} | {sum(1 for x in rs if x['crash'])} | "
                 f"{sum(1 for x in rs if x['spin'])} | {sum(1 for x in rs if x['departure'])} |\n")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write("".join(L))
    print("".join(L))
    print(f"\n[저장] {a.out}")


if __name__ == "__main__":
    main()
