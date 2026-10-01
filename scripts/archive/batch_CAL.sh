#!/usr/bin/env bash
# POST2 완료 후: 특성속도 보정 주행(빙판 마찰 1.0 = 영향 없음) → 맞춤 → 20 m/s 와 크게 다르면 v6 굽기 + 60 km/h 재실행
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== POST2 완료" $ST 2>/dev/null; do sleep 15; done
say "=== CAL-1: 동역학 로그 (60·40 km/h, 마찰 1.0, 방어 없음) ==="
scp -q $SP/scripts/sim/carla_demo.py "$DESK":~/icepredict/code/scripts/sim/carla_demo.py
for K in 60 40; do
  timeout 400 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && timeout 380 ~/icepredict/venv/bin/python scripts/sim/carla_demo.py --weather ClearNoon --tag dyncal_$K --target-kph $K --disable-primary --no-secondary --fusion local --slip-local --no-video --friction 1.0 --no-native-ice --max-steps 900 --log-dyn ~/icepredict/logs/dyn_$K.csv 2>&1 | grep -E '스폰|Traceback|Error' | head -2"
  scp -q "$DESK":~/icepredict/logs/dyn_$K.csv $SP/logs/ && say "dyn_$K.csv $(wc -l < $SP/logs/dyn_$K.csv)줄"
done
say "=== CAL-2: 특성속도 맞춤 ==="
OUT=$(python3 $SP/scripts/analysis/fit_vch.py $SP/logs/dyn_60.csv $SP/logs/dyn_40.csv 2>&1); echo "$OUT" | tee -a $ST
VCH=$(echo "$OUT" | grep -oE "^VCH=[0-9.]+" | cut -d= -f2)
CUR=$(grep -oE "SLIP_V_CH_MPS +[0-9.]+" $SP/fw/npu_lib/slip_core.h | grep -oE "[0-9.]+$")
if [ -n "$VCH" ] && python3 -c "import sys; sys.exit(0 if abs(float('$VCH')-float('$CUR'))>3 else 1)"; then
  sed -i -E "s|#define SLIP_V_CH_MPS +[0-9.]+f|#define SLIP_V_CH_MPS      ${VCH}f|" $SP/fw/npu_lib/slip_core.h
  sed -i -E "s|v_ch_mps: float = [0-9.]+|v_ch_mps: float = ${VCH}|" $SP/src/icepredict/pi/imu_slip.py
  python3 $SP/fw/npu_lib/test_slip_core.py 2>&1 | tail -1 | tee -a $ST
  say "v_ch $CUR → $VCH m/s: v6 굽기"
  scp -q $SP/fw/npu_lib/slip_core.h $SP/fw/npu_lib/patch_fw_slip.py "$DESK":~/icepredict/fw/npu_lib/; scp -q $SP/src/icepredict/pi/imu_slip.py "$DESK":~/icepredict/code/src/icepredict/pi/imu_slip.py
  timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash scripts/deploy/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
  for i in $(seq 1 60); do R=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null'); case "$R" in *"배포 완료"*|*"중단"*) break;; esac; sleep 10; done
  echo "$R" | tail -1 | cut -c1-120 | tee -a $ST; sleep 5
  run() { KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/scripts/sim/demo_batch.sh > /tmp/demo_batch_cal.log 2>&1
    grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST; }
  run "ClearNoon WetNoon" detect 60; run ClearNoon detect_traffic 60; run ClearNoon miss_rtos_traffic
  python3 $SP/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/sim/organize_media.py >> $ST 2>&1; python3 $SP/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/scripts/analysis/summarize_runs.py > /dev/null 2>&1
else
  say "v_ch 변경 불필요 또는 맞춤 실패 (현재 $CUR, 맞춤 ${VCH:-없음}) — 재굽기 생략"
fi
say "=== CAL 완료 — 최종 ==="
