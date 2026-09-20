#!/usr/bin/env python3
"""문서의 숫자를 실측과 **다시 맞춘다** — 밤새 데이터가 늘어난 뒤 한 번 돌린다.

문제: 실사진 표본이 25,140 → 69,360 장으로 늘면 자동 생성 문서는 따라오지만
손으로 쓴 `docs/presentation_evidence_map.md` 는 옛 숫자를 그대로 들고 있다.
발표 자료를 그 문서 보고 만들면 어긋난 숫자를 말하게 된다.

하는 일
  1. 자동 생성 근거 문서를 다시 만든다
  2. 근거 지도의 실사진 장수를 살아 있는 값으로 고친다
  3. 남아 있는 옛 숫자를 찾아 보고한다 (고치지는 않는다 — 기록 문서일 수 있다)

`docs/research_directions_*.md` 는 **날짜가 박힌 기록**이라 건드리지 않는다.
그때 25,140 장이었던 것은 사실이기 때문이다.

사용: python3 scripts/finalize_docs.py [--no-regen]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAP = ROOT / "docs/presentation_evidence_map.md"
REGEN = ["summarize_runs", "analyze_detection", "rscd_breakdown", "spec_head_transfer",
         "imu_noise_tolerance", "schedulability", "slip_rule_compare", "variation_report",
         "rule_ab_report", "slip_onset_analysis", "yawlag_report", "ctx_temp_report",
         "rule_boundary_figure", "latency_figure", "board_verdict_figures", "schedule_figure"]


def photo_stats():
    f = ROOT / "logs/rscd_board_samples.jsonl"
    if not f.exists():
        return None
    n = 0
    ice_hit = ice_n = 0
    us_max = 0
    for l in open(f):
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        n += 1
        us_max = max(us_max, int(r.get("us", 0)))
        if r.get("cls") == "black_ice":
            ice_n += 1
            p = r.get("p") or []
            if p and max(range(len(p)), key=lambda i: p[i]) == 2:
                ice_hit += 1
    return dict(n=n, ice_n=ice_n, ice_acc=(ice_hit / ice_n if ice_n else 0.0),
                npu_max_ms=us_max / 1000.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-regen", action="store_true")
    a = ap.parse_args()

    if not a.no_regen:
        print("=== 자동 생성 문서·그림 다시 만들기 ===")
        for m in REGEN:
            p = ROOT / f"scripts/{m}.py"
            if not p.exists():
                continue
            r = subprocess.run(["python3", str(p)], capture_output=True, text=True, timeout=900)
            print(f"  {m:24s} {'OK' if r.returncode == 0 else '실패'}")
            if r.returncode != 0:
                print("    " + (r.stderr.strip().splitlines() or ["(출력 없음)"])[-1])

    st = photo_stats()
    if not st:
        print("실사진 표본 파일이 없다.")
        return
    print(f"\n=== 살아 있는 값 ===")
    print(f"  실사진 표본 {st['n']:,}장 (블랙아이스 {st['ice_n']:,}장, 정답률 {100*st['ice_acc']:.1f} %)")
    print(f"  NPU 최악 추론 {st['npu_max_ms']:.2f} ms")

    if MAP.exists():
        s = MAP.read_text()
        before = s
        # 근거 지도만 고친다 — 주장 문서이지 기록 문서가 아니다
        s = re.sub(r"\d{1,3}(?:,\d{3})*장, 블랙아이스 \*\*\d+\.\d+ %\*\*",
                   f"{st['n']:,}장, 블랙아이스 **{100*st['ice_acc']:.1f} %**", s)
        s = re.sub(r"실사진 \d{1,3}(?:,\d{3})*장으로 말합니다",
                   f"실사진 {st['n']:,}장으로 말합니다", s)
        s = re.sub(r"실사진 \d{1,3}(?:,\d{3})*장 표본별 판정",
                   f"실사진 {st['n']:,}장 표본별 판정", s)
        s = re.sub(r"(편차 [\d.]+ ms \(평균의 [\d.]+ %\), )\d{1,3}(?:,\d{3})* 회",
                   rf"\g<1>{st['n']:,} 회", s)
        s = re.sub(r"`정리/05`, `figures/real_confusion.jpg`", "`정리/05`, `figures/real_confusion.jpg`", s)
        if s != before:
            MAP.write_text(s)
            print("\n근거 지도의 실사진 수치를 갱신했다.")
        else:
            print("\n근거 지도는 이미 최신이다.")

    print("\n=== 남아 있는 옛 숫자 (확인만, 고치지 않음) ===")
    pat = re.compile(r"\b25,?140\b")
    hits = 0
    for p in sorted(list((ROOT / "docs").glob("*.md")) +
                    list((ROOT / "logs/carla_demo/정리").glob("*.md"))):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if pat.search(line) and st["n"] != 25140:
                rel = p.relative_to(ROOT)
                tag = " (날짜 기록 — 그대로 두는 게 맞다)" if "research_directions" in p.name else ""
                print(f"  {rel}:{i}{tag}")
                hits += 1
    if not hits:
        print("  없다.")


if __name__ == "__main__":
    main()
