#!/usr/bin/env bash
until grep -q "=== 전체 완료 ===" /tmp/after_batch_status.txt 2>/dev/null; do sleep 15; done
python3 "$SP"/scripts/sim/organize_media.py >> /tmp/after_batch_status.txt 2>&1
echo "[$(date +%H:%M:%S)] === 정리 완료 ===" >> /tmp/after_batch_status.txt
