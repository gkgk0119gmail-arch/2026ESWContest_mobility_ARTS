#!/usr/bin/env python3
"""모든 데모 주행(events_*.json)을 표로 집계한다 — 노션 '오탐률 측정 기준' 항목의 근거.
  1차: 인식률(빙판 앞 정지), 오경보(ROI 밖/정지 상태 경고), 경고 늦음(진입 후 경고), 미인식
  2차: 발동 건수, 판정 주체(보드/호스트), 진입→감지 지연, 감지→정지 시간, 보드 응답 시간
출력: logs/carla_demo/정리/00_집계.md"""
import json, pathlib, collections, statistics as st
# 경로를 박아 두면 저장소만 받은 사람이 돌릴 수 없다. logs/ 는 .gitignore 대상이라
# 클론에는 없고, 공개용 사본은 sw/docs/data/events/ 에 있다. 둘 중 있는 쪽을 쓴다.
ROOT = pathlib.Path(__file__).resolve().parents[3]
import sys
D = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    ROOT / "logs/carla_demo" if (ROOT / "logs/carla_demo").is_dir() else ROOT / "sw/docs/data/events")
OUT_DIR = (D / "정리") if (D / "정리").is_dir() or D.name != "events" else ROOT / "sw/docs/evidence"
rows = []
for ej in sorted(D.glob("events_*.json")):
    try: doc = json.load(open(ej))
    except Exception: continue
    a, ev = doc.get("args", {}), doc.get("events", [])
    if not ev: continue
    k = {e["event"]: e for e in ev}
    pw, pe, ss, stp, stb = k.get("primary_warning"), k.get("patch_enter"), k.get("secondary_slip"), k.get("stopped"), k.get("stabilized")
    # 충돌·스핀·차로이탈은 안전 논증의 핵심인데 지금까지 집계에 한 글자도 없었다.
    # ClearNoon_detect_traffic_60kph 는 2차가 돌았는데도 충돌로 끝났고 그 사실이 표에서 사라졌다.
    col = [e for e in ev if e["event"] == "collision"]
    spin = [e for e in ev if e["event"] == "spin"]
    dep = [e for e in ev if e["event"] == "lane_departure"]
    warm = float(a.get("warmup", 3.0))
    if a.get("disable_primary"): p1 = "꺼짐(미인식 가정)"
    elif pw and (pw["t"] < warm or float(pw.get("dist_to_edge_m", 0)) > 32): p1 = "오경보"
    elif pw and stp and stp.get("stopped_before_patch") and not pe: p1 = "인식·정지"
    elif pw and pe: p1 = "경고 늦음(진입)"
    elif not pw and pe: p1 = "미인식"
    else: p1 = "기타"
    rows.append(dict(tag=ej.stem[7:], weather=a.get("weather"), kph=a.get("target_kph"), traffic=a.get("traffic", 0), p1=p1,
                     warn_edge=(pw or {}).get("dist_to_edge_m"), risk=(pw or {}).get("risk"),
                     p2=("보드" if (ss or {}).get("decided_by") == "stm32n6" else ("호스트" if ss else None)),
                     trig=(ss or {}).get("trigger"), enter_to_slip=(round(ss["t"] - pe["t"], 2) if ss and pe else None),
                     slip_to_stop=(round(stb["t"] - ss["t"], 2) if ss and stb else None),
                     modes=[e["mode"] for e in ev if e["event"] == "emergency_mode"],
                     n_col=len(col), col_with=(col[0].get("with") if col else None),
                     col_kph=(col[0].get("speed_kph") if col else None),
                     spin=bool(spin), dep=bool(dep)))
L = ["# 주행 집계", "", f"총 {len(rows)}회", ""]
c1 = collections.Counter(r["p1"] for r in rows if r["p1"] != "꺼짐(미인식 가정)")
L += ["## 1차 방어(카메라) 결과 — 1차가 켜진 주행", "| 결과 | 횟수 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in c1.most_common()]
fa = [r for r in rows if r["p1"] == "오경보"]
L += ["", f"오경보 날씨: " + ", ".join(sorted(set(str(r['weather']) for r in fa))) if fa else "오경보 없음", ""]
cr = [r for r in rows if r["n_col"]]
L += ["", "## 충돌·스핀·차로이탈", f"충돌 {len(cr)}회, 스핀 {sum(r['spin'] for r in rows)}회, 차로이탈 {sum(r['dep'] for r in rows)}회", ""]
if cr:
    L += ["| 주행 | 1차 | 2차 판정 | 충돌 대상 | 충돌 속도 km/h |", "|---|---|---|---|---|"]
    L += [f"| {r['tag']} | {r['p1']} | {r['p2'] or ''} | {r['col_with'] or ''} | {r['col_kph'] if r['col_kph'] is not None else ''} |" for r in cr]
    L += [""]

s2 = [r for r in rows if r["p2"]]
L += ["## 2차 방어(IMU) 결과", f"발동 {len(s2)}회 — 보드 판정 {sum(r['p2']=='보드' for r in s2)}회, 호스트 {sum(r['p2']=='호스트' for r in s2)}회"]
d1 = [r["enter_to_slip"] for r in s2 if r["enter_to_slip"] is not None]; d2 = [r["slip_to_stop"] for r in s2 if r["slip_to_stop"] is not None]
if d1: L.append(f"빙판 진입→미끄러짐 확정: 평균 {st.mean(d1):.2f}s (최소 {min(d1):.2f}, 최대 {max(d1):.2f})")
if d2: L.append(f"확정→정지: 평균 {st.mean(d2):.2f}s (최소 {min(d2):.2f}, 최대 {max(d2):.2f})")
mc = collections.Counter(m for r in s2 for m in r["modes"])
if mc: L.append("비상 제어 모드 등장: " + ", ".join(f"{k} {v}" for k, v in mc.items()))
L += ["", "## 주행별", "| 태그 | 날씨 | km/h | 주변차 | 1차 | 경고 시 가장자리 m | 위험도 | 2차 판정 | 트리거 | 진입→감지 s | 감지→정지 s | 모드 | 충돌 | 스핀 | 이탈 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in rows:
    L.append(f"| {r['tag']} | {r['weather']} | {r['kph']} | {r['traffic']} | {r['p1']} | {r['warn_edge'] if r['warn_edge'] is not None else ''} | {r['risk'] if r['risk'] is not None else ''} | {r['p2'] or ''} | {r['trig'] or ''} | {r['enter_to_slip'] if r['enter_to_slip'] is not None else ''} | {r['slip_to_stop'] if r['slip_to_stop'] is not None else ''} | {' → '.join(r['modes'])} | {r['n_col'] or ''} | {'예' if r['spin'] else ''} | {'예' if r['dep'] else ''} |")
out = OUT_DIR / "00_집계.md"; out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(L) + "\n")
print("\n".join(L[:14])); print("...\n저장:", out)
