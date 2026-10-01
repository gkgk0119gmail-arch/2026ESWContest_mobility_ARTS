#!/usr/bin/env bash
# after_varsweep2 가 끝나면 속도별 동역학 스윕을 돌리고 원인 분석까지 간다.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속3: after_varsweep2 종료 대기 ==="
for i in $(seq 1 240); do
  pgrep -f "after_varsweep2.sh" > /dev/null 2>&1 || break
  sleep 30
done
for i in $(seq 1 40); do
  pgrep -f "demo_batch_5090.sh" > /dev/null 2>&1 || break
  sleep 30
done
bash scripts/archive/speed_dyn_sweep.sh
python3 scripts/analysis/slip_onset_analysis.py 2>&1 | tail -40 | tee -a /tmp/after_batch_status.txt
say "=== 후속3 완료 ==="
