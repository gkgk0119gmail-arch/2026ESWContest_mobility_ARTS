#!/usr/bin/env bash
# Pi에서 실행: 날씨 × 시나리오 행렬로 데모 영상을 모은다.
#   detect = 카메라(보드 NPU)가 빙판을 인식해 정지 (--fusion n6npu, Pi 브리지 필요)
#   miss   = 카메라가 못 봤다고 가정(1차 끔) → 빙판 진입 → IMU 미끄러짐 감지 → 비상 제어
# 영상은 **지우지 않고** logs/carla_demo(= icepredict_영상) 에 계속 모은다 (사용자 지시 2026-09-19).
# ssh 는 원격 백그라운드 작업이 끝날 때까지 안 돌아오므로 setsid + timeout 으로 보호한다.
set -u
DESK=${DESK:-${RENDER_HOST:-user@render-host}}      # 기본: RTX 5090 (GPU 여유). 3090 을 쓰려면 DESK=${DESK_HOST:-user@desk-host}
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
PY=${PY:-\$HOME/miniconda3/envs/icepredict/bin/python}   # ~ 는 파이에서 펼쳐져 /home/pi 가 된다. 원격에서 펼치도록 $HOME 를 문자열로 넘긴다.
CARLA_SH=${CARLA_SH:-~/CARLA/CarlaUE4.sh}
DESK_IP=${DESK#*@}
BOARD=192.168.50.158
STATUS=/tmp/demo_batch_status.txt
: > "$STATUS"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$STATUS"; }
WEATHERS=${WEATHERS:-"ClearNoon WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight"}
SCEN=${SCEN:-"detect miss"}
KPH=${KPH:-40}
VIEWS=${VIEWS:-split,bev,lidar,lidar_sem}

# CARLA 서버 생존 확인 — 죽어 있으면(세그폴트 2회 경험: 동기 모드 중 클라이언트 강제 종료 직후) 화면 없이 다시 띄운다
# CARLA 는 오래 돌면 응답을 멈춘다 (2026-09-21: 8시간 32분 만에 13 GB 까지 늘고 60초 타임아웃).
# 포트가 열려 있어도 죽어 있을 수 있으므로, 살아 있다고 **응답으로** 확인한다.
CARLA_MAX_AGE_MIN=${CARLA_MAX_AGE_MIN:-180}

carla_alive() {
  timeout 15 ssh -o BatchMode=yes "$DESK" 'ss -ltn | grep -q ":2000 "' || return 1
  # 나이도 본다 — 너무 오래된 서버는 포트가 열려 있어도 미리 갈아엎는다
  local age
  age=$(timeout 15 ssh -o BatchMode=yes "$DESK" "ps -o etimes= -p \$(pgrep -f '[C]arlaUE4-Linux-Shipping' | head -1) 2>/dev/null | tr -d ' '" 2>/dev/null)
  [ -n "$age" ] && [ "$age" -gt $((CARLA_MAX_AGE_MIN * 60)) ] && {
    say "CARLA 가 $((age/60))분째 — ${CARLA_MAX_AGE_MIN}분 넘어 선제 재기동"; return 1; }
  return 0
}

kill_carla() {
  # 주의: 패턴이 명령줄에 그대로 있으면 pkill 이 자기 셸을 죽인다 → 브래킷 트릭
  timeout 30 ssh -o BatchMode=yes "$DESK" "pkill -9 -f '[C]arlaUE4' 2>/dev/null; sleep 4; true" || true
}

ensure_carla() {
  if carla_alive; then return 0; fi
  kill_carla
  say "CARLA 서버 없음 — 재시작"
  # CARLA_SH 는 로컬에서 펼쳐야 한다 (원격에는 이 변수가 없다)
  timeout 30 ssh -o BatchMode=yes "$DESK" "cd \$(dirname $CARLA_SH) && (DISPLAY= setsid nohup ./\$(basename $CARLA_SH) -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server_\$(date +%H%M).log 2>&1 < /dev/null &)" || true
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
      # 음성 대조군: 빙판이 아예 없는 같은 경로. 여기서 나오는 1차 경보는 전부 오경보이므로
      # 시뮬 오경보율을 처음으로 잴 수 있다. 영상은 필요 없으니 split 만 찍는다.
      control)           EXTRA="--fusion n6npu --control-no-ice" ;;
      control_traffic)   EXTRA="--fusion n6npu --control-no-ice --traffic 8" ;;
      *) say "알 수 없는 시나리오 $S"; continue ;;
    esac
    NEED_BOARD=0; case "$S" in detect|miss_rtos|miss_rtos_traffic|detect_traffic|control|control_traffic) NEED_BOARD=1 ;; esac
    if [ "$NEED_BOARD" = 1 ]; then
      ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "$TAG: 보드 ping 실패 — 건너뜀"; continue; }
    fi
    say "=== $TAG ==="
    timeout 30 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/sim/carla_demo.py --target-kph $KPH --weather $W --tag $TAG --views $VIEWS $EXTRA > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
    if [ "$NEED_BOARD" = 1 ]; then
      sleep 25
      bash "$SP/scripts/deploy/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "$DESK_IP" > /dev/null 2>&1
    fi
    # 주행이 조용히 죽는 경우가 있다 — CARLA 타임아웃은 C++ 예외로 끝나서
    # '=== 요약' 도 'Traceback' 도 안 남긴다. 그러면 이 루프가 15분을 헛기다린다
    # (2026-09-21 ClearNight 가 19분을 그렇게 썼다). 죽음의 서명도 함께 본다.
    DIED=0
    for i in $(seq 1 90); do
      if timeout 20 ssh -o BatchMode=yes "$DESK" \
           "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null"; then break; fi
      if timeout 20 ssh -o BatchMode=yes "$DESK" \
           "grep -qE 'TimeoutException|time-out of|terminate called|Segmentation fault' ~/icepredict/logs/demo_$TAG.log 2>/dev/null"; then
        DIED=1; say "$TAG: 주행이 죽었다 (CARLA 무응답) — CARLA 재기동 후 다음으로"; break
      fi
      # 프로세스가 사라졌는데 요약도 없으면 죽은 것이다
      if [ "$i" -gt 3 ] && ! timeout 20 ssh -o BatchMode=yes "$DESK" 'pgrep -f "carla_demo.p[y]" > /dev/null'; then
        DIED=1; say "$TAG: 프로세스가 사라졌는데 요약이 없다 — 죽은 것으로 본다"; break
      fi
      sleep 10
    done
    if [ "$DIED" = 1 ]; then
      kill_carla
      ensure_carla || say "$TAG 이후 CARLA 재기동 실패"
      continue
    fi
    timeout 20 ssh -o BatchMode=yes "$DESK" "grep -E '경고|정지|2차|비상|진입|판정 주체|timeout|Traceback|Error|내장 빙판|무응답|slip' ~/icepredict/logs/demo_$TAG.log | tail -5 | cut -c1-150" | tee -a "$STATUS"
    timeout 30 ssh -o BatchMode=yes "$DESK" 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
    sleep 3
  done
done
rsync -az "$DESK":~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
say "영상 목록 (지우지 않음):"
ls -la "$SP"/logs/carla_demo/demo_*.mp4 2>/dev/null | awk '{print $5, $9}' | tee -a "$STATUS"
say "=== 배치 완료 ==="
