#!/usr/bin/env bash
# 크래시 수정 후 재개: 밤 인식 재실행 + 추가 날씨 인식 + 60km/h 인식 (보드 NPU). 미인식(RTOS) 계열은 v3 펌웨어 이후 batch_B.
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; ST=/tmp/after_batch_status.txt
echo "[$(date +%H:%M:%S)] === batch_A: 인식 계열 재개 ===" >> $ST
WEATHERS="ClearNight HardRainSunset WetCloudySunset MidRainyNight SoftRainNoon" SCEN="detect" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchA1.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-140 >> $ST
KPH=60 WEATHERS="ClearNoon WetNoon" SCEN="detect" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchA2.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|진입|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-140 >> $ST
echo "[$(date +%H:%M:%S)] === batch_A 완료 ===" >> $ST
