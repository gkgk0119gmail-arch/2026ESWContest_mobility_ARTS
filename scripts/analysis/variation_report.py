#!/usr/bin/env python3
"""변동 스윕 결과를 **분포로** 정리한다 — "표본이 1개" 문제를 닫는다.

지금까지 2차 방어 수치(진입 → 확정 1.78 s 등)는 한 번 잰 값이었다. 날씨를 8종 돌려도
CARLA 날씨가 물리에 영향을 주지 않아 결과가 바이트 단위로 같았기 때문이다.

`scripts/archive/variation_sweep.sh` 가 시드·속도·마찰을 흔들어 만든 주행들을 모아
평균이 아니라 **분포**로 보고한다. 발표에서 "분산은 얼마냐"에 답할 수 있게 하는 것이 목적이다.

사용: python3 scripts/analysis/variation_report.py
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import re
import statistics as st

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEMO = ROOT / "logs/carla_demo"
TAG_RE = re.compile(r"^var_s(\d+)_k(\d+)_mu(\d+)$")


def load():
    rows = []
    for f in sorted(glob.glob(str(DEMO / "events_var_*.json"))):
        tag = os.path.basename(f)[7:-5]
        m = TAG_RE.match(tag)
        if not m:
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ev = d.get("events", [])
        if not ev:
            continue
        k = {}
        for e in ev:
            k.setdefault(e["event"], e)
        pe, ss, stb, stp = (k.get("patch_enter"), k.get("secondary_slip"),
                            k.get("stabilized"), k.get("stopped"))
        lat = k.get("board_slip_latency")
        modes = [e["mode"] for e in ev if e["event"] == "emergency_mode"]
        col = next((e for e in ev if e["event"] == "collision"), None)
        # 충돌을 한 덩어리로 세면 안 된다. 비상 제어가 이미 차를 세운 뒤 1~3 km/h 로 닿은 것과
        # 40 km/h 로 들이받은 것이 같은 칸에 들어가면 숫자가 거짓말을 한다.
        col_kph = float(col.get("speed_kph", 0.0)) if col else None
        stopped_t = (k.get("stopped") or {}).get("t")
        after_stop = bool(col and stopped_t is not None and col["t"] >= stopped_t - 0.05)
        rows.append(dict(
            tag=tag, seed=int(m.group(1)), kph=int(m.group(2)), # 태그의 mu008 은 "0.08" 에서 점만 뺀 것이다. 앞 한 자리 뒤에 점을 되돌린다.
            mu=float(m.group(3)[0] + "." + m.group(3)[1:]),
            enter=pe["t"] if pe else None,
            slip=ss["t"] if ss else None,
            enter_to_slip=(round(ss["t"] - pe["t"], 3) if ss and pe else None),
            slip_to_stop=(round(stb["t"] - ss["t"], 3) if ss and stb else None),
            trig=(ss or {}).get("trigger"),
            ay_g=(ss or {}).get("ay_g"), yaw_err=(ss or {}).get("yaw_err"),
            modes=modes,
            evaded=any(m_.startswith("evade") for m_ in modes),
            collision=col is not None,
            col_kph=col_kph,
            col_with=(col or {}).get("with"),
            # 심각 충돌: 아직 달리고 있는 상태에서 들이받았다 (5 km/h 이상, 정지 이벤트 이전)
            col_hard=bool(col and col_kph is not None and col_kph >= 5.0 and not after_stop),
            col_touch=bool(col and not (col_kph is not None and col_kph >= 5.0 and not after_stop)),
            spin=any(e["event"] == "spin" for e in ev),
            depart=any(e["event"] == "lane_departure" for e in ev),
            board_max=(lat or {}).get("max_us"),
            board_avg=(lat or {}).get("avg_us"),
        ))
    return rows


def dist(vals, unit="s", n=3):
    v = [x for x in vals if x is not None]
    if not v:
        return "-"
    if len(v) == 1:
        return f"{v[0]:.{n}f} {unit} (표본 1)"
    return (f"{st.mean(v):.{n}f} ± {st.pstdev(v):.{n}f} {unit}  "
            f"[{min(v):.{n}f} ~ {max(v):.{n}f}]  n={len(v)}")


def main():
    rows = load()
    if not rows:
        print("변동 스윕 주행이 없다. 먼저 scripts/archive/variation_sweep.sh 를 돌릴 것.")
        return

    L = [f"# 2차 방어 — 변동 스윕 결과 (표본 {len(rows)}건)\n\n",
         "지금까지 2차 방어 수치는 **한 번 잰 값**이었다. 날씨를 8종 돌려도 CARLA 날씨가 "
         "물리에 영향을 주지 않아 결과가 바이트 단위로 같았기 때문이다.\n\n",
         "여기서는 물리에 닿는 값을 흔들었다 — 주변 차량 배치 시드, 진입 속도, 빙판 마찰. "
         "그래서 아래 숫자는 평균이 아니라 **분포**다.\n\n"]

    L.append("## 1. 조건\n\n")
    L.append(f"- 시드: {sorted({r['seed'] for r in rows})}\n")
    L.append(f"- 진입 속도: {sorted({r['kph'] for r in rows})} km/h\n")
    L.append(f"- 빙판 마찰 µ: {sorted({r['mu'] for r in rows})}\n")
    L.append(f"- 공통: 빙판 100 m, 주변차량 8대, 정차 차량 빙판 +45 m, 1차 끔(2차만 시험)\n")

    L.append("\n## 2. 핵심 지표 분포\n\n")
    L.append("| 지표 | 분포 |\n|---|---|\n")
    L.append(f"| 빙판 진입 → 미끄러짐 확정 | {dist([r['enter_to_slip'] for r in rows])} |\n")
    L.append(f"| 확정 → 정지 | {dist([r['slip_to_stop'] for r in rows])} |\n")
    L.append(f"| 보드 응답 평균 | {dist([r['board_avg'] for r in rows], 'µs', 1)} |\n")
    L.append(f"| 보드 응답 최악 | {dist([r['board_max'] for r in rows], 'µs', 1)} |\n")

    L.append("\n## 3. 결과 요약\n\n")
    n = len(rows)
    L.append("| 항목 | 건수 | 비율 |\n|---|---|---|\n")
    for name, key in (("2차 발화", "slip"), ("회피(evade) 선택", "evaded"),
                      ("충돌 — 주행 중 (5 km/h 이상)", "col_hard"),
                      ("충돌 — 정지 후 접촉 (5 km/h 미만)", "col_touch"),
                      ("스핀", "spin"), ("차로이탈", "depart")):
        c = sum(1 for r in rows if (r[key] is not None if key == "slip" else r[key]))
        L.append(f"| {name} | {c}/{n} | {100*c/n:.0f}% |\n")

    L.append("\n## 4. 속도가 바꾸는 것\n\n")
    L.append("| 진입 속도 | 주행 | 진입→확정 | 확정→정지 | 회피 선택 | 주행 중 충돌 |\n|---|---|---|---|---|---|\n")
    for kph in sorted({r["kph"] for r in rows}):
        g = [r for r in rows if r["kph"] == kph]
        ev = sum(1 for r in g if r["evaded"])
        ch = sum(1 for r in g if r["col_hard"])
        L.append(f"| {kph} km/h | {len(g)} | {dist([r['enter_to_slip'] for r in g])} | "
                 f"{dist([r['slip_to_stop'] for r in g])} | {ev}/{len(g)} | {ch}/{len(g)} |\n")
    L.append("\n빠를수록 **빨리** 확정된다 — 35 → 45 km/h 로 가면 4.23 s 가 1.10 s 로 줄어든다.\n")
    L.append("직관과 반대로 보이지만 물리가 그렇다. 요구 횡가속도가 v²/R 이라 빠를수록 접지를 먼저 잃고,\n")
    L.append("잔차도 그만큼 크게 나온다. 반대로 저속에서는 잔차가 고정 임계값 0.30 g 에 **가까스로 못 미쳐**\n")
    L.append("(실측 0.287 g) 훨씬 느린 yaw 경로를 기다리게 된다. 원인 분해는 `정리/12` 에 있다.\n")

    if len({r["mu"] for r in rows}) > 1:
        L.append("\n## 5. 마찰이 바꾸는 것\n\n")
        L.append("| µ | 주행 | 진입→확정 | 회피 선택 |\n|---|---|---|---|\n")
        for mu in sorted({r["mu"] for r in rows}):
            g = [r for r in rows if r["mu"] == mu]
            ev = sum(1 for r in g if r["evaded"])
            L.append(f"| {mu} | {len(g)} | {dist([r['enter_to_slip'] for r in g])} | {ev}/{len(g)} |\n")

    L.append("\n### 충돌을 왜 둘로 나눴나\n\n")
    L.append("이 스윕은 정차 차량을 빙판 중심 **+45 m** 에 일부러 세워 두었다. 빙판 위 정지거리보다 "
             "짧은 거리다 — 회피를 강제로 시험하려고 만든 배치다. 그래서 여기 충돌률은 "
             "**일반적인 실패율이 아니라 최악 조건의 스트레스 값**이다.\n\n")
    L.append("게다가 충돌 이벤트에는 성격이 다른 둘이 섞여 있다.\n\n")
    L.append("- **주행 중 충돌**: 아직 25~35 km/h 로 달리는 중에 받았다. 진짜 실패다.\n")
    L.append("- **정지 후 접촉**: 비상 제어가 이미 차를 세웠고, 그 뒤 1~3 km/h 로 닿았다. "
             "제동은 성공했고 마지막 몇 십 cm 가 모자랐다.\n")

    L.append("\n## 6. 주행별\n\n")
    L.append("| 주행 | 시드 | km/h | µ | 진입→확정 s | 확정→정지 s | 모드 | 충돌 | 충돌 속도 |\n")
    L.append("|---|---|---|---|---|---|---|---|---|\n")
    for r in sorted(rows, key=lambda x: (x["kph"], x["seed"])):
        if r["col_hard"]:
            cmark = "**주행 중**"
        elif r["col_touch"]:
            cmark = "정지 후 접촉"
        else:
            cmark = "없음"
        L.append(f"| {r['tag']} | {r['seed']} | {r['kph']} | {r['mu']} | "
                 f"{r['enter_to_slip'] if r['enter_to_slip'] is not None else '-'} | "
                 f"{r['slip_to_stop'] if r['slip_to_stop'] is not None else '-'} | "
                 f"{' → '.join(r['modes']) or '-'} | {cmark} | "
                 f"{f'{r["col_kph"]:.0f} km/h' if r["col_kph"] is not None else '-'} |\n")

    # 시드가 실제로 무엇을 바꿨나 — 같은 (확정시각, ay_g, yaw_err) 가 반복되면 자차 동역학은 안 바뀐 것이다
    from collections import Counter
    sig = Counter((r["kph"], r["enter_to_slip"], r["ay_g"], r["yaw_err"]) for r in rows
                  if r["enter_to_slip"] is not None)
    dup = [(k, c) for k, c in sig.items() if c > 1]
    if dup:
        L.append("\n## 7. 시드가 바꾼 것과 바꾸지 않은 것\n\n")
        L.append("`--ice-seed` 는 **주변 차량 배치와 얼음 외관**을 바꾼다. 자차의 주행선은 바꾸지 않는다.\n")
        L.append("그래서 앞차와 얽히지 않은 주행끼리는 미끄러짐이 바이트 단위로 같다.\n\n")
        L.append("| 속도 | 진입→확정 | ay_g | yaw_err | 같은 결과 주행 수 |\n|---|---|---|---|---|\n")
        for (kph, et_, ay_, ye_), c in sorted(dup):
            L.append(f"| {kph} km/h | {et_} s | {ay_} | {ye_} | {c} |\n")
        L.append("\n이것을 숨기면 표본 수를 부풀리게 된다. **같은 속도에서 독립적인 미끄러짐 표본은 "
                 f"{len(sig)} 개**이고, 나머지는 주변 차량이 끼어들어 갈라진 경우다.\n")
        L.append("\n> 다음에 표본을 더 늘리려면 시드가 아니라 **진입 속도·마찰·노면 경사**를 흔들어야 한다.\n")

    et = [r["enter_to_slip"] for r in rows if r["enter_to_slip"] is not None]
    if len(et) > 1:
        L.append(f"\n## 결론\n\n")
        L.append(f"- 진입 → 확정이 **{min(et):.2f} ~ {max(et):.2f} s** 사이에 흩어진다. "
                 f"이전 문서의 \"1.78 s\"는 그 분포 중 한 점이었다.\n")
        L.append(f"- 보드 응답 최악값은 조건이 바뀌어도 "
                 f"{dist([r['board_max'] for r in rows], 'µs', 1)} 로 좁다 — "
                 "**연산 시간은 입력에 거의 의존하지 않는다**는 또 하나의 증거다.\n")
        ev = sum(1 for r in rows if r["evaded"])
        L.append(f"- 회피를 고른 주행이 {ev}/{len(rows)} 다. 간격·속도에 따라 제어기가 "
                 "차선유지와 회피를 갈라 고른다는 뜻이고, 고정된 규칙이 아니다.\n")

    out = DEMO / "정리" / "11_변동스윕_2차방어분포.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
