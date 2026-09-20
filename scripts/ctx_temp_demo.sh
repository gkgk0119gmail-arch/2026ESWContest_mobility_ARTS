#!/usr/bin/env bash
# 맥락 계층이 값을 하는지 **같은 화면으로** 보여준다.
#
# 빙판 없는 젖은 노면(WetNoon) 대조군에서 1차가 노면 확률 0.87 로 얼음이라 단언했다.
# 확인 10프레임을 통과했고, 문턱을 +10 °C 수준까지 올려도 못 막는다.
# 영상만으로는 젖음과 얼음이 안 갈린다는 뜻이다. 그러면 갈라 줄 쪽은 맥락뿐이다.
#
# 완전히 같은 장면을 기온만 바꿔 두 번 돌린다.
#   -3 °C : 얼음이 있을 수 있는 날씨 → 1차가 경보한다 (보수적으로 맞다)
#   +8 °C : 최악으로 잡은 노면 온도도 어는점 위 → 얼음 경보를 내지 않는다
# 영상 두 개를 나란히 놓으면 "맥락이 왜 필요한가"가 한 장면에 담긴다.
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

say "=== 맥락 기온 시연 시작 ==="
carla_ok || restart_carla || exit 1
for T in -3 8; do
  TAG="ctxtemp_$( [ "$T" -lt 0 ] && echo m3 || echo p8 )"
  carla_ok || restart_carla || { say "CARLA 없음 — 중단"; break; }
  ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 무응답 — 중단"; break; }
  say "=== $TAG (기온 ${T}도, 빙판 없음, 젖은 노면) ==="
  timeout 30 ssh -o BatchMode=yes $H5 "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/carla_demo.py \
    --target-kph 40 --weather WetNoon --tag $TAG --views split \
    --fusion n6npu --control-no-ice --ctx-temp $T \
    > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
  sleep 25
  bash "$SP/scripts/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "165.132.135.75" > /dev/null 2>&1
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
  timeout 20 ssh -o BatchMode=yes $H5 "grep -E '\[ctx\]|1차 경고|경보' ~/icepredict/logs/demo_$TAG.log | head -5 | cut -c1-150" | tee -a "$ST"
  timeout 20 ssh -o BatchMode=yes $H5 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
  sleep 3
done
say "=== 회수 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/merge_wcet.py" > /dev/null 2>&1
python3 "$SP/scripts/ctx_temp_report.py" 2>&1 | tail -25 | tee -a "$ST"
say "=== 맥락 기온 시연 완료 ==="
