#!/usr/bin/env bash
# Pi에서 실행: 날씨 × 시나리오 행렬로 데모 영상을 모은다.
#   detect = 카메라(보드 NPU)가 빙판을 인식해 정지 (--fusion n6npu, Pi 브리지 필요)
#   miss   = 카메라가 못 봤다고 가정(1차 끔) → 빙판 진입 → IMU 미끄러짐 감지 → 비상 제어
# 영상은 **지우지 않고** logs/carla_demo(= icepredict_영상) 에 계속 모은다 (사용자 지시 2026-09-19).
# ssh 는 원격 백그라운드 작업이 끝날 때까지 안 돌아오므로 setsid + timeout 으로 보호한다.
set -u
DESK=yax@165.132.135.77
SP=/mnt/ssd/icepredict
BOARD=192.168.50.158
STATUS=/tmp/demo_batch_status.txt
: > "$STATUS"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$STATUS"; }
WEATHERS=${WEATHERS:-"ClearNoon WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight"}
SCEN=${SCEN:-"detect miss"}
KPH=${KPH:-40}
VIEWS=${VIEWS:-split,bev,lidar,lidar_sem}

# CARLA 서버 생존 확인 — 죽어 있으면(세그폴트 2회 경험: 동기 모드 중 클라이언트 강제 종료 직후) 화면 없이 다시 띄운다
ensure_carla() {
  if timeout 15 ssh -o BatchMode=yes "$DESK" 'ss -ltn | grep -q ":2000 "'; then return 0; fi
  say "CARLA 서버 없음 — 재시작"
  timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/Desktop/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Low -RenderOffScreen -nosound > ~/icepredict/logs/carla_server_$(date +%H%M).log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 12); do sleep 10; timeout 15 ssh -o BatchMode=yes "$DESK" 'ss -ltn | grep -q ":2000 "' && { sleep 15; say "CARLA 재시작 완료"; return 0; }; done
  say "CARLA 재시작 실패"; return 1
}

for W in $WEATHERS; do
  for S in $SCEN; do
    ensure_carla || continue
    TAG="${W}_${S}"; [ "$KPH" != 40 ] && TAG="${TAG}_${KPH}kph"
    # detect    : 카메라(보드 NPU) 인식 → 정지
    # miss      : 1차 끔, 2차(IMU)는 로컬 참조 구현
    # miss_rtos : 1차 끔, 2차(IMU 미끄러짐 감지)를 보드(STM32N6 ThreadX)가 판정 — 펌웨어 slip 경로 필요
    case "$S" in
      detect)    EXTRA="--fusion n6npu" ;;
      miss)      EXTRA="--disable-primary --fusion local --slip-local" ;;
      miss_rtos) EXTRA="--disable-primary --fusion n6npu" ;;
      # 주변 차량 + 정차 차량: 1차 미인식 → 빙판(길이 100m, 마찰 0.08)에서 제동 실패(저마찰 감지) → 보드가 앞차 간격·옆 차로를 보고 회피/정지
      miss_rtos_traffic) EXTRA="--disable-primary --fusion n6npu --traffic 8 --lead-stop 70 --friction 0.08 --patch-len 100" ;;
      detect_traffic)    EXTRA="--fusion n6npu --traffic 8" ;;
      # 기준선: 1차·2차 모두 없음 — 같은 조건에서 RTOS가 없으면 어떻게 되는지 (충돌 기록)
      nodefense_traffic) EXTRA="--disable-primary --no-secondary --fusion local --traffic 8 --lead-stop 70 --friction 0.08 --patch-len 100" ;;
      nodefense)         EXTRA="--disable-primary --no-secondary --fusion local" ;;
      *) say "알 수 없는 시나리오 $S"; continue ;;
    esac
    NEED_BOARD=0; case "$S" in detect|miss_rtos|miss_rtos_traffic|detect_traffic) NEED_BOARD=1 ;; esac
    if [ "$NEED_BOARD" = 1 ]; then
      ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "$TAG: 보드 ping 실패 — 건너뜀"; continue; }
    fi
    say "=== $TAG ==="
    timeout 30 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && setsid nohup timeout 900 ~/icepredict/venv/bin/python scripts/carla_demo.py --target-kph $KPH --weather $W --tag $TAG --views $VIEWS $EXTRA > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
    if [ "$NEED_BOARD" = 1 ]; then
      sleep 25
      bash "$SP/scripts/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" > /dev/null 2>&1
    fi
    for i in $(seq 1 90); do
      timeout 20 ssh -o BatchMode=yes "$DESK" "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" && break
      sleep 10
    done
    timeout 20 ssh -o BatchMode=yes "$DESK" "grep -E '경고|정지|2차|비상|진입|판정 주체|timeout|Traceback|Error|내장 빙판|무응답|slip' ~/icepredict/logs/demo_$TAG.log | tail -5 | cut -c1-150" | tee -a "$STATUS"
    timeout 30 ssh -o BatchMode=yes "$DESK" 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
    sleep 3
  done
done
rsync -az "$DESK":~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
say "영상 목록 (지우지 않음):"
ls -la "$SP"/logs/carla_demo/demo_*.mp4 2>/dev/null | awk '{print $5, $9}' | tee -a "$STATUS"
say "=== 배치 완료 ==="
