#!/usr/bin/env bash
# 체인 완료 후 밤 배경 detect 재실행 (워밍업 전 오탐 수정 반영본으로)
until grep -q "=== 체인 완료 ===" /tmp/after_batch_status.txt 2>/dev/null; do sleep 15; done
WEATHERS="ClearNight" SCEN="detect" bash /mnt/ssd/icepredict/scripts/demo_batch.sh > /tmp/demo_batch4.log 2>&1
grep -E "===|경고|정지|Traceback" /tmp/demo_batch_status.txt | cut -c1-140 >> /tmp/after_batch_status.txt
echo "[$(date +%H:%M:%S)] === 밤 재실행 완료 ===" >> /tmp/after_batch_status.txt
