#!/usr/bin/env bash
# 조향 지연 보정(τ=0.06 s)을 **끝에서 확인한다**.
#
# 왜 필요했나: 빙판 없는 대조군 50 km/h 주행에서 2차 방어가 발화했다. 조향이 80 ms 만에
# 0.07 → 0.66 으로 튀자 자전거 모델이 yaw 1.02 rad/s 를 기대했는데 실제는 0.35 였다.
# 미끄러진 게 아니라 아직 안 돌아간 것뿐인데 그 지연이 잔차로 잡혔다.
#
# 고친 뒤 확인할 것 두 가지.
#   ① 같은 대조군 50 km/h 에서 이제 안 터지나
#   ② 빙판 탐지가 크게 늦어지지 않았나 (오프라인 예측 35 km/h +0.56 s)
set -u
SP=/mnt/ssd/icepredict; H5=dlab27@165.132.135.75; ST=/tmp/after_batch_status.txt
PY='$HOME/miniconda3/envs/icepredict/bin/python'
BOARD=192.168.50.158
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
carla_ok(){ timeout 15 ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "'; }
restart_carla(){
  timeout 30 ssh -o BatchMode=yes $H5 "pkill -9 -f '[C]arlaUE4' 2>/dev/null; sleep 4; true" || true
  timeout 30 ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 24); do sleep 10; carla_ok && { say "CARLA 재기동 완료"; return 0; }; done
  say "CARLA 재기동 실패"; return 1
}

say "=== 조향 지연 보정 확인: 코드 동기화 ==="
rsync -az "$SP/scripts/" $H5:~/icepredict/code/scripts/ > /dev/null 2>&1
rsync -az "$SP/src/"     $H5:~/icepredict/code/src/     > /dev/null 2>&1

carla_ok || restart_carla || exit 1
run(){  # $1 태그  $2 속도  $3 추가인자
  local TAG=$1 KPH=$2; shift 2
  carla_ok || restart_carla || return 1
  ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 무응답"; return 1; }
  say "=== $TAG (${KPH} km/h) ==="
  timeout 30 ssh -o BatchMode=yes $H5 "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/carla_demo.py \
    --target-kph $KPH --weather ClearNoon --tag $TAG --views split \
    --disable-primary --fusion n6npu --traffic 0 --ice-seed 7 $* \
    > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
  sleep 25
  bash "$SP/scripts/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "165.132.135.75" > /dev/null 2>&1
  for i in $(seq 1 90); do
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" && break
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE 'TimeoutException|terminate called' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" \
      && { say "$TAG: CARLA 무응답"; restart_carla; return 1; }
    if [ "$i" -gt 3 ] && ! timeout 20 ssh -o BatchMode=yes $H5 'pgrep -f "carla_demo.p[y]" > /dev/null'; then
      say "$TAG: 프로세스 소멸"; restart_carla; return 1
    fi
    sleep 10
  done
  timeout 20 ssh -o BatchMode=yes $H5 "grep -E '빙판 진입|미끄러짐 감지|규칙 A/B' ~/icepredict/logs/demo_$TAG.log | tail -3 | cut -c1-150" | tee -a "$ST"
  timeout 20 ssh -o BatchMode=yes $H5 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
  sleep 3
}

# ① 예전에 2차가 헛발질한 대조군
run lag_ctrl_k50 50 --control-no-ice --log-dyn '~/icepredict/logs/dync2_k50.csv'
# ② 탐지가 얼마나 늦어졌나
run lag_ice_k35 35 --friction 0.08 --patch-len 140 --log-dyn '~/icepredict/logs/dyn2_k35.csv'
run lag_ice_k50 50 --friction 0.08 --patch-len 140 --log-dyn '~/icepredict/logs/dyn2_k50.csv'

say "=== 회수 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
rsync -az $H5:~/icepredict/logs/dyn2_k*.csv $H5:~/icepredict/logs/dync2_k*.csv "$SP/logs/" 2>/dev/null
python3 "$SP/scripts/merge_wcet.py" > /dev/null 2>&1
python3 "$SP/scripts/yawlag_report.py" 2>&1 | tail -25 | tee -a "$ST"
say "=== 조향 지연 보정 확인 완료 ==="
