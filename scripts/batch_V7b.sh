#!/usr/bin/env bash
# v7: 미끄러진 차로 기억·접지 회복·상대속도 반영 제어기 → 주변 차량 시나리오 전체 재실행 → 비교 영상·정리
set -u
SP=/mnt/ssd/icepredict; DESK=yax@165.132.135.77; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
say "=== V7b: v7 펌웨어는 이미 보드에 있음 — 응답 확인 후 재실행 ==="
python3 - <<'PY' | tee -a "$ST"
import socket, struct
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(2.0)
u.sendto(struct.pack("<I15fI", 1, *([0.0]*4), 0.02, 6.94, *([0.0]*2), 999,999,999, 0,0, 0,0, 0x30554D49), ("192.168.50.158", 5557))
try: d,_ = u.recvfrom(64); print("보드 v7 imu_reset 응답", len(d), "B")
except socket.timeout: print("보드 v7 imu 무응답"); raise SystemExit(1)
PY
[ "${PIPESTATUS[0]}" = 0 ] || { say "v7 확인 실패"; exit 1; }
run() { KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/scripts/demo_batch.sh > /tmp/demo_batch_v7.log 2>&1
  grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST; }
say "=== V7-1: 미인식 + 주변 차량 (8종) ==="
run "ClearNoon WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight MidRainyNight Snow" miss_rtos_traffic
say "=== V7-2: 인식 + 주변 차량 (60 km/h 1, 40 km/h 3) ==="
run ClearNoon detect_traffic 60; run "ClearNoon WetNoon ClearSunset" detect_traffic
say "=== V7-3: 비교 영상·정리 ==="
for W in ClearNoon WetNoon ClearNight Snow; do for V in bev split; do
  [ -f $SP/logs/carla_demo/demo_${W}_nodefense_traffic_$V.mp4 ] && python3 $SP/scripts/compare_videos.py ${W}_nodefense_traffic ${W}_miss_rtos_traffic $V > /dev/null 2>&1
done; done
cp -f $SP/logs/carla_demo/compare_*.mp4 "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/" 2>/dev/null
python3 $SP/scripts/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/organize_media.py >> $ST 2>&1; python3 $SP/scripts/make_figures.py >> $ST 2>&1; python3 $SP/scripts/summarize_runs.py > /dev/null 2>&1
say "=== V7b 완료 ==="
