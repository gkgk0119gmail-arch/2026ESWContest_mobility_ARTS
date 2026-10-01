#!/usr/bin/env bash
# 판정 규칙 A/B — 변동 스윕과 **똑같은 조건**에서 타원과 직사각형을 같은 주행선 위에 나란히 놓는다.
#
# 보드 펌웨어는 재기록에 물리 접근(BOOT1 스위치 + SWD)이 필요해 원격으로 못 바꾼다.
# 대신 호스트가 두 참조 구현을 동시에 돌려 각각의 첫 확정 시각을 기록한다(carla_demo 의 rule_ab).
# 판정·제어 주체는 그대로 보드(=직사각형)이므로, 기록된 "타원" 시각은 같은 궤적 위의 반사실이다.
#
# 조건은 scripts/archive/variation_sweep.sh 와 동일: 주변차량 8, 정차 차량 +45 m, µ 0.08, 빙판 100 m.
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; H5=${RENDER_HOST:-user@render-host}; ST=/tmp/after_batch_status.txt
PY='$HOME/miniconda3/envs/icepredict/bin/python'
BOARD=192.168.50.158
KPHS=${KPHS:-"35 40 45"}
SEEDS=${SEEDS:-"7 23"}
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
carla_ok(){ timeout 15 ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "'; }
restart_carla(){
  timeout 30 ssh -o BatchMode=yes $H5 "pkill -9 -f '[C]arlaUE4' 2>/dev/null; sleep 4; true" || true
  timeout 30 ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 24); do sleep 10; carla_ok && { say "CARLA 재기동 완료"; return 0; }; done
  say "CARLA 재기동 실패"; return 1
}

say "=== 판정 규칙 A/B 스윕 시작 (속도 $KPHS × 시드 $SEEDS) ==="
carla_ok || restart_carla || exit 1
for KPH in $KPHS; do
for SD in $SEEDS; do
  TAG="ab_s${SD}_k${KPH}"
  carla_ok || restart_carla || { say "CARLA 없음 — 중단"; break 2; }
  ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 무응답 — 중단"; break 2; }
  say "=== $TAG ==="
  timeout 30 ssh -o BatchMode=yes $H5 "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/sim/carla_demo.py \
    --target-kph $KPH --weather ClearNoon --tag $TAG --views split \
    --disable-primary --fusion n6npu --traffic 8 --lead-stop 45 \
    --friction 0.08 --patch-len 100 --ice-seed $SD \
    > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
  sleep 25
  bash "$SP/scripts/deploy/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "${RENDER_IP:-render-host}" > /dev/null 2>&1
  DIED=0
  for i in $(seq 1 90); do
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" && break
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE 'TimeoutException|time-out of|terminate called|Segmentation fault' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" \
      && { DIED=1; say "$TAG: CARLA 무응답으로 죽음"; break; }
    if [ "$i" -gt 3 ] && ! timeout 20 ssh -o BatchMode=yes $H5 'pgrep -f "carla_demo.p[y]" > /dev/null'; then
      DIED=1; say "$TAG: 프로세스 소멸"; break
    fi
    sleep 10
  done
  [ "$DIED" = 1 ] && { restart_carla; continue; }
  timeout 20 ssh -o BatchMode=yes $H5 "grep -E '규칙 A/B|미끄러짐 감지' ~/icepredict/logs/demo_$TAG.log | tail -2 | cut -c1-150" | tee -a "$ST"
  timeout 20 ssh -o BatchMode=yes $H5 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
  sleep 3
done; done
say "=== 회수 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/analysis/merge_wcet.py" > /dev/null 2>&1
python3 "$SP/scripts/analysis/rule_ab_report.py" 2>&1 | tail -30 | tee -a "$ST"
say "=== 판정 규칙 A/B 스윕 완료 ==="
