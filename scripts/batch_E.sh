#!/usr/bin/env bash
# batch_D 후: 방어 없음 기준선 (RTOS 없을 때) — 주변 차량 있음/없음, 그리고 최종 정리·집계
SP=/mnt/ssd/icepredict; ST=/tmp/after_batch_status.txt; DESK=yax@165.132.135.77
until grep -q "=== batch_D 완료 ===" $ST 2>/dev/null; do sleep 15; done
echo "[$(date +%H:%M:%S)] === batch_E: 방어 없음 기준선 ===" >> $ST
[ -f $SP/scripts/demo_batch.sh.new ] && mv $SP/scripts/demo_batch.sh.new $SP/scripts/demo_batch.sh
scp -q $SP/scripts/carla_demo.py "$DESK":~/icepredict/code/scripts/carla_demo.py
WEATHERS="ClearNoon WetNoon ClearNight" SCEN="nodefense_traffic nodefense" bash $SP/scripts/demo_batch.sh > /tmp/demo_batchE.log 2>&1
grep -E "^\[[0-9:]+\] ===|충돌|통과|진입|정지|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
python3 $SP/scripts/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/organize_media.py >> $ST 2>&1; python3 $SP/scripts/summarize_runs.py > /dev/null 2>&1
echo "[$(date +%H:%M:%S)] === batch_E 완료 (최종 정리·집계 갱신) ===" >> $ST
