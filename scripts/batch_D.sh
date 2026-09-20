#!/usr/bin/env bash
# batch_C 후: v3 펌웨어(저마찰 감지 + 차선 유지 제어)로 60 km/h 인식 주행 재실행 — 경고 늦음 → 빙판 진입 → RTOS 개입
SP=/mnt/ssd/icepredict; ST=/tmp/after_batch_status.txt
until grep -q "=== batch_C 완료 ===" $ST 2>/dev/null; do sleep 15; done
echo "[$(date +%H:%M:%S)] === batch_D: 60 km/h 인식 (v3) + 주변 차량 ===" >> $ST
KPH=60 WEATHERS="ClearNoon WetNoon" SCEN="detect detect_traffic" bash $SP/scripts/demo_batch.sh > /tmp/demo_batchD.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
python3 $SP/scripts/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/organize_media.py >> $ST 2>&1
echo "[$(date +%H:%M:%S)] === batch_D 완료 ===" >> $ST
