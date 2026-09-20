#!/usr/bin/env bash
set -u
cd /mnt/ssd/icepredict
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속7: after_fullrscd 종료 대기 ==="
for i in $(seq 1 60); do pgrep -f "after_fullrsc[d].sh" > /dev/null 2>&1 && break; sleep 10; done
for i in $(seq 1 400); do pgrep -f "after_fullrsc[d].sh" > /dev/null 2>&1 || break; sleep 30; done
bash scripts/yawlag_verify.sh
say "=== 후속7 완료 ==="
