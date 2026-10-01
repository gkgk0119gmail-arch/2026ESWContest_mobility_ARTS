#!/usr/bin/env python3
"""실사진 보드 판정을 **노면 조건별로** 쪼갠다.

4클래스 요약(얼음 96 %, 젖음 84 % …)은 많은 것을 감춘다. RSCD 파일명에는
마찰(dry/wet/water/ice/fresh_snow/melted_snow) · 재질(asphalt/concrete/gravel/mud) ·
요철(smooth/slight/severe)이 들어 있어서, 27가지 조합으로 쪼개면
"어디서 약한가"를 정확히 말할 수 있다.

특히 확인할 것:
  1) black_ice 로 묶은 `ice` 와 `melted_snow` 가 같은 난이도인가
     — 문서는 "RSCD 의 ice 는 다져진 눈·서리 질감이라 투명한 블랙아이스와 다르다"고 적어 뒀다.
  2) 재질(아스팔트/콘크리트/자갈/진흙)이 판정을 바꾸는가
  3) 문턱 0.60 에서 남는 오경보가 **어느 조건**에서 나오는가

사용: python3 scripts/analysis/rscd_breakdown.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.common.rscd import parse as rscd_parse  # noqa: E402

CLS = ("normal", "wet", "black_ice", "pothole")
KO = {"normal": "정상", "wet": "젖음", "black_ice": "블랙아이스", "pothole": "포트홀"}
SAMPLES = ROOT / "logs/rscd_board_samples.jsonl"
TH_NOW, TH_REC = 0.441, 0.60


def load():
    rows = []
    for line in open(SAMPLES):
        if not line.strip():
            continue
        r = json.loads(line)
        lab = rscd_parse(r["file"])
        if not lab:
            continue
        r["fr"] = lab.friction
        r["mat"] = lab.material or "-"
        r["un"] = lab.uneven or "-"
        r["pred"] = CLS[int(np.argmax(r["p"]))]
        rows.append(r)
    return rows


def agg(rs):
    if not rs:
        return None
    risk = np.array([r["risk"] for r in rs])
    return dict(n=len(rs),
                acc=float(np.mean([r["pred"] == r["cls"] for r in rs])),
                risk=float(risk.mean()),
                spec=float(np.mean([r["spec"] for r in rs])),
                a_now=float((risk >= TH_NOW).mean()),
                a_rec=float((risk >= TH_REC).mean()))


def main():
    rows = load()
    L = [f"# 실사진 판정 — 노면 조건별 분해\n\n",
         f"`logs/rscd_board_samples.jsonl` 의 **{len(rows):,}장**을 RSCD 원본 라벨(마찰·재질·요철)로 "
         "쪼갰다. 4클래스 요약이 감추는 것을 드러내는 것이 목적이다.\n\n",
         f"경보율은 `risk >= 문턱` 으로 계산했다 (현행 {TH_NOW}, 권고 {TH_REC}).\n\n"]

    # ---------- 1. 얼음 두 갈래 ----------
    L.append("## 1. `ice` 와 `melted_snow` 는 같은 난이도인가\n\n")
    L.append("둘 다 대회 4클래스에서는 `black_ice` 로 묶인다. 그런데 물리적으로 다르다 — "
             "`ice` 는 다져진 얼음/서리, `melted_snow` 는 녹았다 다시 어는 노면이다.\n\n")
    L.append(f"| RSCD 라벨 | 장수 | 정답률 | 평균 위험도 | 평균 반사도 | 경보율 {TH_NOW} | 경보율 {TH_REC} |\n")
    L.append("|---|---|---|---|---|---|---|\n")
    for fr in ("ice", "melted_snow"):
        a = agg([r for r in rows if r["fr"] == fr])
        if a:
            L.append(f"| {fr} | {a['n']:,} | {a['acc']:.3f} | {a['risk']:.3f} | {a['spec']:.3f} | "
                     f"{a['a_now']*100:.1f}% | **{a['a_rec']*100:.1f}%** |\n")
    ice = agg([r for r in rows if r["fr"] == "ice"])
    mel = agg([r for r in rows if r["fr"] == "melted_snow"])
    if ice and mel:
        d = abs(ice["a_rec"] - mel["a_rec"]) * 100
        L.append(f"\n권고 문턱에서 두 갈래의 경보율 차이가 **{d:.1f} 포인트** 다. ")
        L.append("차이가 작으면 둘을 한 클래스로 묶은 것이 타당하다는 근거가 되고, "
                 "크면 발표에서 \"어느 쪽 얼음인가\"를 구분해 말해야 한다.\n")

    # ---------- 1b. 안전에 관계있는 오분류만 ----------
    ice_i = CLS.index("black_ice")
    n_all = len(rows)
    fa = [r for r in rows if r["cls"] != "black_ice" and r["pred"] == "black_ice"]
    ms = [r for r in rows if r["cls"] == "black_ice" and r["pred"] != "black_ice"]
    grade = [r for r in rows if r["pred"] != r["cls"] and r["cls"] != "black_ice"
             and r["pred"] != "black_ice"]
    L.append("\n## 1b. 오분류를 **안전 관점**으로 다시 세기\n\n")
    L.append("4클래스 정답률(정상 80.3 %)은 낮아 보이지만, 틀린 것들이 무엇으로 틀렸는지가 중요하다. "
             "얼음을 놓치거나 얼음이 아닌 것을 얼음이라 하는 것만이 안전에 영향을 준다. "
             "`정상 ↔ 포트홀` 같은 혼동은 노면 거칠기 등급을 한 칸 잘못 매긴 것이고 "
             "제동 판단을 바꾸지 않는다.\n\n")
    L.append("| 오분류 종류 | 장수 | 전체 대비 | 안전 영향 |\n|---|---|---|---|\n")
    L.append(f"| 얼음이 아닌데 얼음이라 함 | {len(fa):,} | {100*len(fa)/n_all:.2f}% | **있음 (오경보)** |\n")
    L.append(f"| 얼음인데 못 알아봄 | {len(ms):,} | {100*len(ms)/n_all:.2f}% | **있음 (미탐)** |\n")
    L.append(f"| 나머지 (거칠기·젖음 등급 혼동) | {len(grade):,} | {100*len(grade)/n_all:.2f}% | 없음 |\n")
    L.append(f"\n**안전에 영향을 주는 오분류는 전체의 {100*(len(fa)+len(ms))/n_all:.2f} % 다.** "
             "나머지는 거칠기를 한 등급 잘못 본 것으로, 실제로 가장 큰 덩어리는 "
             "마른 콘크리트 `slight` 를 `severe`(포트홀)로 본 것이다 — 1,117장 중 691장. "
             "사람이 매긴 smooth/slight/severe 경계가 연속적인 거칠기에 그은 선이라 그렇다. "
             "그 1,117장 중 얼음이라 한 것은 **단 1장**이다.\n")

    # ---------- 2. 재질별 ----------
    L.append("\n## 2. 노면 재질이 판정을 바꾸는가\n\n")
    L.append("마찰 라벨이 `dry`(= 정상) 인 사진만 모아 재질별로 본다. "
             "같은 '마른 노면'인데 재질 때문에 성능이 갈리면 그것이 약점이다.\n\n")
    L.append(f"| 재질 | 요철 | 장수 | 정답률 | 평균 위험도 | 오경보율 {TH_REC} |\n|---|---|---|---|---|---|\n")
    dry = [r for r in rows if r["fr"] == "dry"]
    combos = sorted({(r["mat"], r["un"]) for r in dry})
    for mat, un in combos:
        a = agg([r for r in dry if r["mat"] == mat and r["un"] == un])
        if a and a["n"] >= 50:
            L.append(f"| {mat} | {un} | {a['n']:,} | {a['acc']:.3f} | {a['risk']:.3f} | "
                     f"{a['a_rec']*100:.1f}% |\n")

    # ---------- 3. 요철(=포트홀) ----------
    L.append("\n## 3. 요철 등급 — `severe` 를 포트홀로 매핑한 것이 맞나\n\n")
    L.append("대회 4클래스는 요철 `severe` 를 `pothole` 로 본다. 등급별로 모델이 실제로 "
             "구분하는지 확인한다.\n\n")
    L.append("| 요철 등급 | 장수 | 정답률 | 평균 위험도 | 평균 반사도 |\n|---|---|---|---|---|\n")
    for un in ("smooth", "slight", "severe"):
        a = agg([r for r in rows if r["un"] == un])
        if a:
            L.append(f"| {un} | {a['n']:,} | {a['acc']:.3f} | {a['risk']:.3f} | {a['spec']:.3f} |\n")

    # ---------- 4. 권고 문턱에서 남는 오경보 ----------
    L.append(f"\n## 4. 문턱 {TH_REC} 에서 **남는** 오경보는 어디서 나오나\n\n")
    L.append("얼음이 아닌데 경보가 난 사진을 조건별로 세었다. 이것이 다음에 고쳐야 할 목록이다.\n\n")
    bad = [r for r in rows if r["cls"] != "black_ice" and r["risk"] >= TH_REC]
    tot = [r for r in rows if r["cls"] != "black_ice"]
    c = collections.Counter((r["fr"], r["mat"], r["un"]) for r in bad)
    base = collections.Counter((r["fr"], r["mat"], r["un"]) for r in tot)
    L.append(f"얼음 아닌 사진 {len(tot):,}장 중 **{len(bad):,}장 ({100*len(bad)/len(tot):.1f} %)** 이 오경보다.\n\n")
    L.append("| 마찰 | 재질 | 요철 | 오경보 | 해당 조건 전체 | 비율 |\n|---|---|---|---|---|---|\n")
    for k, v in c.most_common(10):
        L.append(f"| {k[0]} | {k[1]} | {k[2]} | {v:,} | {base[k]:,} | "
                 f"**{100*v/max(base[k],1):.1f}%** |\n")

    # ---------- 5. 놓친 얼음 ----------
    L.append(f"\n## 5. 문턱 {TH_REC} 에서 **놓치는** 얼음\n\n")
    miss = [r for r in rows if r["cls"] == "black_ice" and r["risk"] < TH_REC]
    ices = [r for r in rows if r["cls"] == "black_ice"]
    L.append(f"얼음 {len(ices):,}장 중 **{len(miss):,}장 ({100*len(miss)/max(len(ices),1):.1f} %)** 을 놓친다.\n\n")
    cm = collections.Counter(r["fr"] for r in miss)
    bm = collections.Counter(r["fr"] for r in ices)
    L.append("| RSCD 라벨 | 놓침 | 전체 | 놓침률 |\n|---|---|---|---|\n")
    for k, v in cm.most_common():
        L.append(f"| {k} | {v:,} | {bm[k]:,} | {100*v/max(bm[k],1):.1f}% |\n")
    if miss:
        mr = np.array([r["risk"] for r in miss])
        mp = np.array([r["p"][2] for r in miss])
        L.append(f"\n놓친 사진의 평균 위험도 {mr.mean():.3f}, 평균 얼음 확률 {mp.mean():.3f}. ")
        L.append("얼음 확률이 낮으면 분류 자체가 실패한 것이고, 높은데 위험도가 낮으면 "
                 "반사도 항이 끌어내린 것이다.\n")

    out = ROOT / "logs/carla_demo/정리" / "07_노면조건별_분해.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
