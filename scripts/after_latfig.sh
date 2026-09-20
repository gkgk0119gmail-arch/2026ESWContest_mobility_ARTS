#!/usr/bin/env bash
# 표본별 보드 지연이 쌓이면 실시간성 그림을 다시 만든다.
set -u
cd /mnt/ssd/icepredict
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
for i in $(seq 1 60); do pgrep -f "after_ctxtemp.sh" > /dev/null 2>&1 && break; sleep 10; done
for i in $(seq 1 300); do pgrep -f "after_ctxtemp.sh" > /dev/null 2>&1 || break; sleep 30; done
rsync -az dlab27@165.132.135.75:~/icepredict/logs/carla_demo/ logs/carla_demo/ 2>/dev/null
python3 scripts/latency_figure.py 2>&1 | grep -E '저장|건너뜀' | tee -a /tmp/after_batch_status.txt
say "=== 실시간성 그림 갱신 ==="
