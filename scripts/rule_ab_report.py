#!/usr/bin/env python3
"""판정 규칙 A/B 결과 — 같은 주행선 위에서 타원과 직사각형이 각각 언제 확정했나.

보드 펌웨어는 재기록에 물리 접근(BOOT1 스위치 + SWD)이 필요해 원격으로 못 바꾼다.
그래서 호스트가 두 참조 구현을 나란히 돌려 첫 확정 시각을 같이 기록했다(carla_demo 의 rule_ab).
제어 주체는 그대로 보드(=직사각형)이므로, 기록된 "타원" 시각은 **같은 궤적 위의 반사실**이다.
시뮬레이션을 두 번 돌려 비교하는 것보다 깨끗하다 — 입력이 바이트 단위로 같기 때문이다.

사용: python3 scripts/rule_ab_report.py
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO = ROOT / "logs/carla_demo"


def load():
    rows = []
    for f in sorted(glob.glob(str(DEMO / "events_*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ab = d.get("rule_ab")
        if not ab or not (ab.get("ellipse") or ab.get("box")):
            continue
        a = d.get("args", {})
        rows.append(dict(tag=os.path.basename(f)[7:-5],
                         kph=a.get("target_kph"), seed=a.get("ice_seed"),
                         ell=ab.get("ellipse"), box=ab.get("box"),
                         gain=ab.get("gain_s")))
    return rows


def main():
    rows = load()
    if not rows:
        print("rule_ab 가 담긴 주행이 없다. scripts/rule_ab_sweep.sh 를 먼저 돌릴 것.")
        return
    rows.sort(key=lambda r: (r["kph"] or 0, r["seed"] or 0))

    L = ["# 판정 규칙 A/B — 같은 주행선 위에서 잰 타원 대 직사각형\n\n",
         "`정리/13` 은 합성 신호로 타원 규칙이 더 빠름을 보였다. 여기서는 **CARLA 실주행**에서 잰다.\n\n",
         "방법: 한 번의 주행에서 두 참조 구현을 동시에 돌리고 각각의 첫 확정 시각을 기록했다.\n",
         "판정·제어 주체는 그대로 보드(직사각형 규칙)다. 그래서 \"타원\" 열은 **같은 궤적 위의 반사실**이다.\n",
         "시뮬을 두 번 돌려 비교하는 것보다 깨끗하다 — 입력이 바이트 단위로 같다.\n\n",
         "> 보드 펌웨어는 재기록에 물리 접근(BOOT1 스위치 + SWD)이 필요하다. "
         "코드는 `fw/npu_lib/slip_core.h` 에 이미 반영돼 있고 C↔파이썬 동치도 통과했다. "
         "다음 현장 작업 때 구우면 이 표의 \"타원\" 열이 실제 동작이 된다.\n\n"]

    L.append("## 1. 주행별\n\n")
    L.append("| 주행 | km/h | 직사각형(보드 실제) | 타원(반사실) | 이득 |\n|---|---|---|---|---|\n")
    for r in rows:
        f = lambda v: "미발화" if not v else f"{v['t']:.2f} s ({v['trigger']})"
        g = "-" if r["gain"] is None else (f"**{r['gain']:+.2f} s**" if r["gain"] else "같음")
        if r["ell"] and not r["box"]:
            g = "**직사각형은 못 잡음**"
        L.append(f"| {r['tag']} | {r['kph']} | {f(r['box'])} | {f(r['ell'])} | {g} |\n")

    gains = [r["gain"] for r in rows if r["gain"] is not None]
    if gains:
        L.append("\n## 2. 요약\n\n")
        L.append(f"- 주행 {len(rows)} 건 중 이득이 잰 것은 {len(gains)} 건.\n")
        L.append(f"- 평균 **{st.mean(gains):+.2f} s**, 최대 **{max(gains):+.2f} s**, 최소 {min(gains):+.2f} s.\n")
        worse = [g for g in gains if g < -1e-9]
        L.append(f"- 느려진 주행 {len(worse)} 건 — {'없다. 타원은 직사각형을 안에 품으므로 있을 수 없다.' if not worse else '있다. 원인을 봐야 한다.'}\n")
        by = {}
        for r in rows:
            if r["gain"] is not None:
                by.setdefault(r["kph"], []).append(r["gain"])
        if len(by) > 1:
            L.append("\n| 진입 속도 | 주행 | 평균 이득 |\n|---|---|---|\n")
            for k in sorted(by):
                L.append(f"| {k} km/h | {len(by[k])} | **{st.mean(by[k]):+.2f} s** |\n")
            L.append("\n저속일수록 이득이 크면 예상대로다 — 직사각형 모서리에 걸리는 것이 저속 현상이다.\n")

    trig = {}
    for r in rows:
        if r["ell"]:
            trig[r["ell"]["trigger"]] = trig.get(r["ell"]["trigger"], 0) + 1
    if trig:
        L.append("\n## 3. 무엇이 방아쇠를 당겼나 (타원)\n\n")
        for k, v in sorted(trig.items(), key=lambda x: -x[1]):
            note = " ← 두 잔차 모두 임계 아래인데 합쳐서 넘은 것. 직사각형이면 놓쳤을 구간이다." if k == "combo" else ""
            L.append(f"- `{k}` {v} 건{note}\n")

    out = DEMO / "정리" / "14_판정규칙_실주행AB.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
