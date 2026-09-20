#!/usr/bin/env bash
set -u
cd /mnt/ssd/icepredict
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속5: after_ruleab 종료 대기 ==="
for i in $(seq 1 60); do pgrep -f "after_ruleab.sh" > /dev/null 2>&1 && break; sleep 10; done
for i in $(seq 1 300); do pgrep -f "after_ruleab.sh" > /dev/null 2>&1 || break; sleep 30; done
bash scripts/ctx_temp_demo.sh
say "=== 후속5 완료 ==="
