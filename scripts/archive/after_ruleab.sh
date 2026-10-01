#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속4: after_speeddyn 종료 대기 ==="
for i in $(seq 1 60); do pgrep -f "after_speeddy[n].sh" > /dev/null 2>&1 && break; sleep 10; done
for i in $(seq 1 300); do pgrep -f "after_speeddy[n].sh" > /dev/null 2>&1 || break; sleep 30; done
bash scripts/archive/rule_ab_sweep.sh
say "=== 후속4 완료 ==="
