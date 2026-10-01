#!/usr/bin/env python3
"""보드 지연(WCET) 누적 로그를 데스크탑에서 가져와 파이 쪽 보관본에 합친다.

`carla_demo.py` 는 자기가 도는 기계의 `logs/board_wcet.jsonl` 에 한 줄씩 덧붙인다.
그런데 데모는 데스크탑(5090/3090)에서 돌고 배치 회수는 `logs/carla_demo/` 만 rsync 하므로,
그대로 두면 **기록이 두 기계에 갈라진 채 영원히 안 만난다.** 이 스크립트가 그걸 합친다.

같은 주행이 여러 번 들어가지 않도록 (tag, at, samples, max_us) 로 중복을 제거하고,
합친 뒤 요약(관측 WCET)을 찍는다.

사용: python3 sw/scripts/analysis/merge_wcet.py [--host user@render-host] [--also user@desk-host]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics as st
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
LOCAL = ROOT / "logs" / "board_wcet.jsonl"


def read(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def fetch(host: str) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as fh:
        tmp = pathlib.Path(fh.name)
    r = subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         f"{host}:~/icepredict/logs/board_wcet.jsonl", str(tmp)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {host}: 가져오지 못함 ({r.stderr.strip()[:80] or '파일 없음'})")
        return []
    rows = read(tmp)
    tmp.unlink(missing_ok=True)
    print(f"  {host}: {len(rows)}건")
    return rows


def key(r: dict):
    return (r.get("tag"), r.get("at"), r.get("samples"), r.get("max_us"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("RENDER_HOST", "user@render-host"))
    ap.add_argument("--also", action="append", default=[],
                    help="추가로 합칠 호스트 (여러 번 쓸 수 있다)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    local = read(LOCAL)
    print(f"파이 보관본: {len(local)}건")
    merged = {key(r): r for r in local}

    print("원격에서 가져오는 중")
    for host in [a.host] + a.also:
        for r in fetch(host):
            merged.setdefault(key(r), r)

    rows = sorted(merged.values(), key=lambda r: (r.get("at") or "", r.get("tag") or ""))
    added = len(rows) - len(local)
    print(f"\n합친 결과 {len(rows)}건 (새로 추가 {added}건)")

    if not a.dry_run and added > 0:
        LOCAL.parent.mkdir(parents=True, exist_ok=True)
        LOCAL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        print(f"저장: {LOCAL}")
    elif a.dry_run:
        print("(--dry-run 이라 저장하지 않았다)")

    if rows:
        busy = [r for r in rows if r.get("npu_busy")]
        idle = [r for r in rows if not r.get("npu_busy")]
        print()
        for name, g in (("NPU 동시", busy), ("NPU 유휴", idle), ("전체", rows)):
            if not g:
                continue
            print(f"  {name:8s} 주행 {len(g):3d}, 샘플 {sum(r['samples'] for r in g):6d}, "
                  f"평균 {st.mean(r['avg_us'] for r in g):5.1f} us, "
                  f"관측 WCET {max(r['max_us'] for r in g):5.1f} us")


if __name__ == "__main__":
    main()
