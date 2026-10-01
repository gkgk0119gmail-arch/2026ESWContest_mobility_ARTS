#!/usr/bin/env python3
"""맥락 계층이 값을 하는가 — 같은 화면, 기온만 다르게.

빙판 없는 젖은 노면(WetNoon) 대조군에서 1차가 노면 확률 **0.87** 로 얼음이라 단언했다.
확인 10프레임을 통과했고, 문턱을 +10 °C 수준(0.742)까지 올려도 못 막는다.
영상만으로는 젖음과 얼음이 갈리지 않는다는 뜻이다. 그러면 갈라 줄 쪽은 맥락뿐이다.

`ctx_temp_demo.sh` 가 완전히 같은 장면을 기온만 바꿔 두 번 돌렸다.
이 스크립트는 두 주행의 프레임별 위험도를 나란히 놓고, 판단이 실제로 갈렸는지 확인한다.

사용: python3 sw/scripts/analysis/ctx_temp_report.py
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
DEMO = ROOT / "logs/carla_demo"
CASES = [("ctxtemp_m3", -3.0, "얼음이 있을 수 있는 날씨"),
         ("ctxtemp_p8", 8.0, "얼음이 있을 수 없는 날씨")]


def load(tag):
    ev, tr = DEMO / f"events_{tag}.json", DEMO / f"trace_{tag}.json"
    if not ev.exists():
        return None
    d = json.load(open(ev))
    rows = json.load(open(tr)) if tr.exists() else []
    warn = [e for e in d.get("events", []) if e["event"] == "primary_warning"]
    return dict(tag=tag, events=d.get("events", []), rows=rows, warn=warn, args=d.get("args", {}))


def main():
    got = [(t, temp, note, load(t)) for t, temp, note in CASES]
    if not any(g[3] for g in got):
        print("ctxtemp 주행이 없다. sw/scripts/archive/ctx_temp_demo.sh 를 먼저 돌릴 것.")
        return

    L = ["# 맥락 계층이 값을 하는가 — 같은 화면, 기온만 다르게\n\n",
         "## 1. 무엇이 문제였나\n\n",
         "빙판이 **없는** 젖은 노면 대조군(WetNoon)에서 1차가 노면 확률 **0.87** 로 얼음이라 단언했다.\n",
         "위험도 0.871, 확인 10프레임 연속 통과. 경보가 나갔고 차가 섰다.\n\n",
         "막을 방법을 하나씩 짚어 봤다.\n\n",
         "| 시도 | 되나 |\n|---|---|\n",
         "| 문턱을 올린다 | 안 된다. +10 °C 수준의 문턱(0.742)도 0.871 아래다 |\n",
         "| 확인 프레임을 늘린다 | 안 된다. 이미 10프레임 연속이다 |\n",
         "| 반사도 가중치를 낮춘다 | 안 된다. 얼음 확률 자체가 0.87 이다 |\n",
         "| 강수 게이트 | 안 걸린다. 비가 오는 게 아니라 **젖어 있는** 것이다 (0.6 mm/h) |\n\n",
         "영상만으로는 젖음과 얼음이 갈리지 않는다. 그러면 갈라 줄 쪽은 **맥락**뿐이다.\n\n",
         "### 먼저 짚을 것 — 이건 시뮬 현상이다\n\n",
         "실사진 25,140장에서는 젖음과 얼음이 거의 완전히 갈린다. 얼음 판별 AUC **0.998**, "
         "운영 문턱 0.60 에서 젖은 노면 오경보 **1.9 %** (`정리/05`, `08`).\n",
         "CARLA 의 젖은 노면 렌더가 실물과 다르다는 뜻이고, 이건 `정리/03` 에서 이미 정리한 도메인 갭이다.\n\n",
         "그러니 이 기온 게이트는 **실제 결함을 때우는 패치가 아니라 한 겹 더 두는 방어**다.\n",
         "센서나 모델이 틀려도 물리가 허락하지 않는 경보는 나가지 않게 막는 층이다.\n",
         "발표에서 \"시뮬에서 오경보가 났다\"를 근거로 쓰면 안 된다 — 지각 성능은 실사진으로만 말한다.\n\n",
         "## 2. 어떻게 갈랐나\n\n",
         "블랙아이스는 노면이 어는점 이하여야 생긴다. 노면은 공기보다 차가울 수 있으니\n",
         "그 차이를 **최악으로** 잡고, 그러고도 0 °C 를 넘으면 \"얼음이 있을 수 없다\"고 말한다.\n\n",
         "| 조건 | 노면이 공기보다 낮을 수 있는 폭 |\n|---|---|\n",
         "| 낮 · 트인 곳 | 2 °C |\n| 낮 · 다리·터널출구·그늘 | 3 °C |\n",
         "| 밤 · 트인 곳 | 4 °C |\n| 밤 · 다리·터널출구·그늘 | 6 °C |\n\n",
         "강수 게이트와 의미가 다르다. 강수 게이트는 \"카메라를 못 믿겠다 → 2차에 맡긴다\"이고,\n",
         "이쪽은 \"얼음이 있을 수 없다 → 얼음 경보를 내지 않는다\"이다.\n",
         "**어느 쪽이든 2차 방어는 그대로 돈다** — 따뜻해도 젖은 노면은 미끄럽다.\n\n",
         "## 3. 실제로 갈렸나\n\n"]

    L.append("| 주행 | 기온 | 얼음 가능 | 1차 경보 | 최대 위험도 | 문턱 넘은 프레임 |\n|---|---|---|---|---|---|\n")
    for tag, temp, note, d in got:
        if not d:
            L.append(f"| {tag} | {temp:+.0f} °C | - | 주행 없음 | - | - |\n")
            continue
        rows = d["rows"]
        mx = max((r.get("risk", 0.0) for r in rows), default=0.0)
        th = 0.592
        over = sum(1 for r in rows if r.get("risk", 0.0) >= th)
        gated = any("얼음 불가능" in str(e) for e in d["events"])
        L.append(f"| {tag} | {temp:+.0f} °C | {'아니오' if temp > 3 else '예'} | "
                 f"{'**' + str(len(d['warn'])) + '회**' if d['warn'] else '**없음**'} | "
                 f"{mx:.3f} | {over}/{len(rows)} |\n")

    L.append("\n> \"최대 위험도 0.000\" 은 카메라가 아무것도 못 봤다는 뜻이 아니다. 게이트가 "
             "**1차 경로 자체를 끈** 결과다. 지금 구현은 얼음 경보만 막는 게 아니라 1차를 통째로 "
             "내린다 — 이 시스템의 1차 출력이 블랙아이스 위험도 하나뿐이라 결과는 같지만, "
             "포트홀·젖음 같은 다른 노면 정보까지 같이 버리는 것은 개선 여지다.\n")
    L.append("> 주행 길이가 다른 것도 그 때문이다. −3 °C 는 경보 뒤 차를 세워 654프레임에서 끝났고, "
             "+8 °C 는 끝까지 달려 1,200프레임을 돌았다.\n")

    ok = [d for _, t_, _, d in got if d and t_ > 3 and not d["warn"]]
    bad = [d for _, t_, _, d in got if d and t_ < 0 and d["warn"]]
    L.append("\n## 4. 읽는 법\n\n")
    if ok and bad:
        L.append("- **−3 °C 에서 경보가 난 것은 잘못이 아니다.** 어는점 아래에서 번들거리는 노면은 "
                 "얼음일 수 있다. 보수적으로 맞는 판단이다.\n")
        L.append("- **+8 °C 에서 침묵한 것이 맥락 계층의 값이다.** 화면은 똑같은데 판단이 갈렸다.\n")
        L.append("- 두 영상을 나란히 놓으면 설명이 거의 필요 없다.\n")
    else:
        L.append("- 아직 두 주행이 다 모이지 않았거나, 기대와 다르게 나왔다. 위 표를 먼저 볼 것.\n")
    L.append("\n> 한계 ①: 지금은 얼음 경보만이 아니라 1차 경로를 통째로 끈다. "
             "다른 노면 등급까지 같이 잃는다 — 보드 융합 코드에서 얼음 항만 막는 쪽이 맞다.\n")
    L.append("> 한계 ②: 기온은 차량 외기온 센서나 기상 API 에서 온다. 그 값이 틀리면 이 판단도 틀린다.\n")
    L.append("> 그래서 노면과 공기의 온도 차를 **최악으로** 잡았다. 틀리는 쪽이 있다면 "
             "\"얼음이 있을 수 있다\"로 틀리게 만들어 둔 것이다.\n")

    out = DEMO / "정리" / "15_맥락계층_기온게이트.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
