#!/usr/bin/env bash
# Pi에서 무인 실행: 1차 배치가 끝나기를 기다렸다가 → 보드에 slip 펌웨어 굽기 → 데모 동기화 →
# 배치 스크립트 교체 → 2차 배치(miss_rtos 전 날씨 + 폭우 detect 재실행).
# 보드를 굽는 동안 detect 주행이 돌면 안 되므로 반드시 1차 배치 완료 후에만 진행한다.
set -u
DESK=yax@165.132.135.77
SP=/mnt/ssd/icepredict
STATUS=/tmp/after_batch_status.txt
: > "$STATUS"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$STATUS"; }

say "=== 0) 1차 배치 완료 대기 ==="
until grep -q "=== 배치 완료 ===" /tmp/demo_batch_status.txt 2>/dev/null; do sleep 15; done
say "1차 배치 완료"

say "=== 1) 보드에 slip 펌웨어 굽기 ==="
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash scripts/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
for i in $(seq 1 60); do
  ST=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null')
  case "$ST" in *"배포 완료"*|*"중단"*) break ;; esac
  sleep 10
done
echo "$ST" | tail -5 | cut -c1-140 | tee -a "$STATUS"
case "$ST" in *"배포 완료"*) ;; *) say "펌웨어 배포 실패 — 2차 배치는 하지 않는다"; exit 1 ;; esac

say "=== 2) 보드 slip 경로 확인 ==="
sleep 5
python3 - <<'EOF' | tee -a "$STATUS"
import socket, struct
IMU_FMT, SLIP_FMT = "<I6fI", "<IBBHffIff"
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(2.0)
u.sendto(struct.pack(IMU_FMT, 1, 0, 0, 0, 0, 0.02, 6.94, 0x30554D49), ("192.168.50.158", 5557))
try:
    d, _ = u.recvfrom(64); print("보드 imu_reset 응답", len(d), "B", struct.unpack(SLIP_FMT, d)[:3])
except socket.timeout:
    print("보드 imu 무응답 — 펌웨어에 slip 경로가 없다"); raise SystemExit(1)
EOF
[ "${PIPESTATUS[0]}" = 0 ] || { say "slip 경로 확인 실패 — 중단"; exit 1; }

say "=== 3) 데모 동기화 + 배치 스크립트 교체 ==="
scp -q "$SP/scripts/carla_demo.py" "$DESK":~/icepredict/code/scripts/carla_demo.py || { say "scp 실패"; exit 1; }
[ -f "$SP/scripts/demo_batch.sh.new" ] && mv "$SP/scripts/demo_batch.sh.new" "$SP/scripts/demo_batch.sh"

say "=== 4) 2차 배치: miss_rtos 전 날씨 + 폭우 detect 재실행 ==="
WEATHERS="ClearNoon WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight" SCEN="miss_rtos" bash "$SP/scripts/demo_batch.sh" > /tmp/demo_batch2.log 2>&1
grep -E "===|판정 주체|2차|비상|정지|Traceback" /tmp/demo_batch_status.txt | cut -c1-140 | tee -a "$STATUS"
WEATHERS="HardRainNoon" SCEN="detect" bash "$SP/scripts/demo_batch.sh" > /tmp/demo_batch3.log 2>&1
grep -E "===|경고|정지|Traceback" /tmp/demo_batch_status.txt | cut -c1-140 | tee -a "$STATUS"
say "영상 개수: $(ls "$SP"/logs/carla_demo/demo_*.mp4 2>/dev/null | wc -l)"
say "=== 체인 완료 ==="
