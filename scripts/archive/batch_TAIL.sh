#!/usr/bin/env bash
# ALL 완료 후: 맑음 기준선 재실행(차선 이탈·스핀 지표 포함) → 비교 영상·정리·집계 갱신
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; ST=/tmp/after_batch_status.txt
until grep -q "=== ALL 완료" $ST 2>/dev/null; do sleep 15; done
echo "[$(date +%H:%M:%S)] === TAIL: 맑음 기준선 재실행 (스핀 지표) ===" >> $ST
WEATHERS="ClearNoon" SCEN="nodefense_traffic" bash $SP/scripts/sim/demo_batch.sh > /tmp/demo_batch_tail.log 2>&1
grep -E "^\[[0-9:]+\] ===|진입|충돌|이탈|스핀|통과|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
for V in bev split; do python3 $SP/scripts/sim/compare_videos.py ClearNoon_nodefense_traffic ClearNoon_miss_rtos_traffic $V >> $ST 2>&1; done
cp -f $SP/logs/carla_demo/compare_ClearNoon_*.mp4 "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/" 2>/dev/null
python3 $SP/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/sim/organize_media.py >> $ST 2>&1
python3 $SP/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/scripts/analysis/summarize_runs.py > /dev/null 2>&1
echo "[$(date +%H:%M:%S)] === TAIL 완료 — 정말 끝 ===" >> $ST
