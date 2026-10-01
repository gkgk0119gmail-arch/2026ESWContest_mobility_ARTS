#!/usr/bin/env bash
# 보드 대규모 평가가 끝나면 이어서: RTOS 지연 비교 → 실사진 판정 그림.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*"; }
say "=== 보드 평가 종료 대기 ==="
for i in $(seq 1 240); do
  pgrep -f "run_board_eval.sh|rscd_board_eval.py" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "run_board_eval.sh|rscd_board_eval.py" > /dev/null 2>&1 && { say "안 끝남 — 중단"; exit 1; }
say "보드 평가 종료 확인 ($(wc -l < logs/rscd_board_samples.jsonl)장)"
sleep 20    # 파이가 조용해지길 기다린다
say "=== RTOS 지연 비교 ==="
python3 scripts/deploy/rtos_latency_bench.py --n 50000 > /tmp/rtos_bench.log 2>&1 && say "06_RTOS가_왜_필요한가.md 생성" || say "지연 비교 실패"
say "=== 실사진 판정 그림 ==="
python3 scripts/analysis/board_verdict_figures.py > /tmp/verdict_fig.log 2>&1 && say "그림 생성" || say "그림 생성 건너뜀"
say "=== 후속 완료 ==="
