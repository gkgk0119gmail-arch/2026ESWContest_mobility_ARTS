#!/usr/bin/env bash
# 속도별 동역학 로그가 모이면 판정 경계 그림을 만든다.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
for i in $(seq 1 300); do pgrep -f "speed_dyn_swee[p].sh" > /dev/null 2>&1 || break; sleep 20; done
sleep 10
python3 scripts/analysis/rule_boundary_figure.py 2>&1 | tail -3 | tee -a /tmp/after_batch_status.txt
python3 scripts/analysis/slip_onset_analysis.py > /dev/null 2>&1 && say "12_저속지연_원인분해.md 갱신"
