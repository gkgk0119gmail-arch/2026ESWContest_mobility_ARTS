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

사용: python3 sw/scripts/analysis/finalize_docs.py [--no-regen]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
MAP = ROOT / "docs/presentation_evidence_map.md"
# rtos_latency_bench 는 파이에서 실제로 벤치를 돌린다. 유휴 측정이 오염되지 않게
# 다른 작업이 모두 끝난 뒤(= 이 스크립트가 도는 시점)에만 돌려야 한다.
REGEN = ["rtos_latency_bench", "summarize_runs", "analyze_detection", "rscd_breakdown", "spec_head_transfer",
         "imu_noise_tolerance", "schedulability", "slip_rule_compare", "variation_report",
         "rule_ab_report", "slip_onset_analysis", "yawlag_report", "ctx_temp_report",
         "rule_boundary_figure", "latency_figure", "board_verdict_figures", "schedule_figure",
         "story_figure", "range_resolution"]


def photo_stats():  # noqa: C901
    f = ROOT / "logs/rscd_board_samples.jsonl"
    if not f.exists():
        return None
    import numpy as _np
    n = 0
    ice_hit = ice_n = 0
    us_max = 0
    miss_p, miss_spec = [], []
    risk_by = {c: [] for c in ("normal", "wet", "black_ice", "pothole")}
    for l in open(f):
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        n += 1
        us_max = max(us_max, int(r.get("us", 0)))
        c = r.get("cls")
        if c in risk_by:
            risk_by[c].append(float(r.get("risk", 0.0)))
        if c == "black_ice":
            ice_n += 1
            p = r.get("p") or []
            if p and max(range(len(p)), key=lambda i: p[i]) == 2:
                ice_hit += 1
            elif p:
                miss_p.append(float(p[2]))
                miss_spec.append(float(r.get("spec", 0.0)))
    th = 0.603
    try:
        import sys as _s
        _s.path.insert(0, str(ROOT / "src"))
        from icepredict.pi.context import LocationCtx, WeatherObs, build_context
        th = build_context(WeatherObs(temp_c=-3.0, humidity=88.0, temp_trend_c_per_h=-1.0),
                           LocationCtx(feature="bridge", hour=5)).threshold
    except Exception:
        pass
    rates = {c: (float((_np.array(v) >= th).mean()) if v else 0.0) for c, v in risk_by.items()}
    return dict(n=n, ice_n=ice_n, ice_acc=(ice_hit / ice_n if ice_n else 0.0),
                npu_max_ms=us_max / 1000.0, miss_n=len(miss_p),
                miss_rate=(len(miss_p) / ice_n if ice_n else 0.0),
                miss_p_max=(max(miss_p) if miss_p else 0.0),
                th=th, rates=rates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-regen", action="store_true")
    a = ap.parse_args()

    if not a.no_regen:
        print("=== 자동 생성 문서·그림 다시 만들기 ===")
        for m in REGEN:
            p = ROOT / f"sw/scripts/{m}.py"
            if not p.exists():
                continue
            r = subprocess.run(["python3", str(p)], capture_output=True, text=True, timeout=900)
            print(f"  {m:24s} {'OK' if r.returncode == 0 else '실패'}")
            if r.returncode != 0:
                print("    " + (r.stderr.strip().splitlines() or ["(출력 없음)"])[-1])

    # 리눅스 벤치 원본을 /tmp 에서 보존 폴더로 옮겨 둔다 (재부팅에 날아가지 않게)
    import shutil
    keep = ROOT / "logs/rtos_bench"
    keep.mkdir(parents=True, exist_ok=True)
    for f in pathlib.Path("/tmp").glob("bench_*.txt"):
        try:
            shutil.copy2(f, keep / f.name)
        except Exception:
            pass

    st = photo_stats()
    if not st:
        print("실사진 표본 파일이 없다.")
        return
    print(f"\n=== 살아 있는 값 ===")
    print(f"  실사진 표본 {st['n']:,}장 (블랙아이스 {st['ice_n']:,}장, 정답률 {100*st['ice_acc']:.1f} %)")
    print(f"  NPU 최악 추론 {st['npu_max_ms']:.2f} ms")

    # 리눅스 깨어남 지연도 다시 잰다 — 벤치를 돌릴 때마다 최악값이 바뀐다
    lin = None
    try:
        v = np.loadtxt(ROOT / "logs/rtos_bench/bench_cyclic_idle.txt") / 1000.0
        lin = float(v[np.isfinite(v)].max())
        print(f"  리눅스 유휴 최악 깨어남 {lin:,.0f} us")
    except Exception:
        pass

    # 보드 WCET 는 주행마다 쌓인다 — 근거 지도의 숫자도 같이 움직여야 한다
    wc = None
    try:
        rows = [json.loads(l) for l in open(ROOT / "logs/board_wcet.jsonl") if l.strip()]
        wc = dict(runs=len(rows), samples=sum(r["samples"] for r in rows),
                  max_us=max(r["max_us"] for r in rows))
        print(f"  보드 WCET 최악 {wc['max_us']:.1f} us, 표본 {wc['samples']:,}개 ({wc['runs']} 주행)")
    except Exception:
        pass

    if MAP.exists():
        s = MAP.read_text()
        before = s
        import datetime as _dt
        s = re.sub(r"기준 \d{4}-\d{2}-\d{2} \d{2}:\d{2}\.",
                   "기준 " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M") + ".", s)
        if lin:
            s = re.sub(r"리눅스 꼬리 [\d,]+ µs 대 보드 수직선",
                       f"리눅스 꼬리 {lin:,.0f} µs 대 보드 수직선", s)
            s = re.sub(r"리눅스는 [\d,]+ µs 까지 늘어진 꼬리",
                       f"리눅스는 {lin:,.0f} µs 까지 늘어진 꼬리", s)
            s = re.sub(r"\*\*유휴에서도 최대 [\d.]+ ms\*\* 늦게 깨어남 = 제어 주기의 \d+ %",
                       f"**유휴에서도 최대 {lin/1000:.1f} ms** 늦게 깨어남 = "
                       f"제어 주기의 {100*lin/20000:.0f} %", s)
        try:
            rows = [json.loads(l) for l in open(ROOT / "logs/board_wcet.jsonl") if l.strip()]
            busy = [r["avg_us"] for r in rows if r.get("npu_busy")]
            idle = [r["avg_us"] for r in rows if not r.get("npu_busy")]
            if busy and idle:
                bi, ii = sum(busy)/len(busy), sum(idle)/len(idle)
                s = re.sub(r"NPU 가 25\.5 ms 추론 중에도 2차 응답 [\d.]+ → \*\*[\d.]+ µs\*\* \(\+[\d.]+ µs\)",
                           f"NPU 가 25.5 ms 추론 중에도 2차 응답 {ii:.1f} → **{bi:.1f} µs** "
                           f"(+{bi-ii:.1f} µs)", s)
        except Exception:
            pass
        if wc:
            s = re.sub(r"깨어남·연산·응답 합쳐 \*\*[\d.]+ µs\*\*, 주기의 [\d.]+ %\. [\d,]+ 샘플",
                       f"깨어남·연산·응답 합쳐 **{wc['max_us']:.1f} µs**, "
                       f"주기의 {100*wc['max_us']/20000:.2f} %. {wc['samples']:,} 샘플", s)
            s = re.sub(r"(`logs/board_wcet.jsonl` — 주행마다 덧붙는 누적 로그, )[\d,]+ 샘플",
                       rf"\g<1>{wc['samples']:,} 샘플", s)
        # 근거 지도만 고친다 — 주장 문서이지 기록 문서가 아니다
        s = re.sub(r"\d{1,3}(?:,\d{3})*장, 블랙아이스 \*\*\d+\.\d+ %\*\*",
                   f"{st['n']:,}장, 블랙아이스 **{100*st['ice_acc']:.1f} %**", s)
        s = re.sub(r"실사진 \d{1,3}(?:,\d{3})*장으로 말합니다",
                   f"실사진 {st['n']:,}장으로 말합니다", s)
        s = re.sub(r"실사진 \d{1,3}(?:,\d{3})*장 표본별 판정",
                   f"실사진 {st['n']:,}장 표본별 판정", s)
        # 놓친 얼음 사진 비율
        s = re.sub(r"실사진 얼음 \d{1,3}(?:,\d{3})*장 중 \*\*[\d.]+ % \(\d+장\) 를 놓친다\*\*",
                   f"실사진 얼음 {st['ice_n']:,}장 중 "
                   f"**{100*st['miss_rate']:.1f} % ({st['miss_n']}장) 를 놓친다**", s)
        s = re.sub(r"놓친 사진의 얼음확률 [\d.]+~[\d.]+",
                   f"놓친 사진의 얼음확률 0.00~{st['miss_p_max']:.2f}", s)
        # 운영점 경보율
        r_ = st["rates"]
        s = re.sub(r"문턱 [\d.]+ 에서 얼음 [\d.]+ %, 젖음 [\d.]+ %, 포트홀 [\d.]+ %, 정상 [\d.]+ %",
                   f"문턱 {st['th']:.2f} 에서 얼음 {100*r_['black_ice']:.1f} %, "
                   f"젖음 {100*r_['wet']:.1f} %, 포트홀 {100*r_['pothole']:.1f} %, "
                   f"정상 {100*r_['normal']:.1f} %", s)
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
