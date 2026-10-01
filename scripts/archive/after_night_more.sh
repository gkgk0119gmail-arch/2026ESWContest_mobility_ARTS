#!/usr/bin/env bash
# 밤 재실행까지 끝난 뒤: 추가 날씨 배치 + 60km/h(경고가 늦어 RTOS가 잡는 장면) + 사진 추출
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; ST=/tmp/after_batch_status.txt
until grep -q "=== 밤 재실행 완료 ===" $ST 2>/dev/null; do sleep 15; done
echo "[$(date +%H:%M:%S)] === 추가 날씨 배치 (비 오는 해질녘·젖은 흐린 해질녘·비 오는 밤·보슬비) ===" >> $ST
WEATHERS="HardRainSunset WetCloudySunset MidRainyNight SoftRainNoon" SCEN="detect miss_rtos" bash $SP/scripts/sim/demo_batch.sh > /tmp/demo_batch5.log 2>&1
grep -E "===|경고|정지|판정 주체|2차|비상|Traceback" /tmp/demo_batch_status.txt | cut -c1-140 >> $ST
echo "[$(date +%H:%M:%S)] === 60 km/h: 카메라 경고가 늦어 빙판 진입 → RTOS 2차 방어 ===" >> $ST
KPH=60 WEATHERS="ClearNoon WetNoon" SCEN="detect" bash $SP/scripts/sim/demo_batch.sh > /tmp/demo_batch6.log 2>&1
grep -E "===|경고|정지|판정 주체|2차|비상|진입|Traceback" /tmp/demo_batch_status.txt | cut -c1-140 >> $ST
python3 $SP/scripts/sim/extract_photos.py >> $ST 2>&1
echo "[$(date +%H:%M:%S)] 영상 $(ls $SP/logs/carla_demo/demo_*.mp4 | wc -l)개, 사진 $(ls $SP/logs/carla_demo/photos/*.jpg 2>/dev/null | wc -l)장" >> $ST
echo "[$(date +%H:%M:%S)] === 전체 완료 ===" >> $ST
