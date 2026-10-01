#!/usr/bin/env bash
# 기준선(방어 없음) 주행을 다시 찍는다.
#
# 왜 다시 찍나: 제어를 잃은 뒤에도 자율주행이 붙어 있어, 스핀으로 역방향을 본 차가 다시 가속해
# 빙판으로 유턴해 들어갔다(실측 ClearNoon_nodefense_traffic: 14 s 에 빠져나갔다가 17 s 에 되돌아가
# 19.3 s 에 충돌). 그 충돌은 빙판이 아니라 역주행 탓이라 기준선 통계를 오염시킨다.
# carla_demo.py 를 고쳐 제어 상실 시 자율주행을 떼고 관성에 맡기며, 결판 뒤 주행을 끝낸다.
set -u
SP=/mnt/ssd/icepredict
H5=dlab27@165.132.135.75
ST=/tmp/rerun_baseline_status.txt
: > "$ST"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

say "=== 1) 데모 동기화 ==="
scp -q "$SP/scripts/carla_demo.py" "$H5":~/icepredict/code/scripts/carla_demo.py || { say "scp 실패"; exit 1; }

say "=== 2) CARLA 새로 띄우기 ==="
# 재사용하지 않고 반드시 새로 띄운다. 오래 돈 서버는 응답이 멈추고(기록: 8시간이면 멈춤),
# 중간에 죽인 주행의 유령 차량이 도로에 남아 자율주행이 그 뒤에 서 버린다 — 빙판에 닿지도 못하고
# 24초 시간 초과로 끝나는데 로그만 보면 성공처럼 보인다.
timeout 60 ssh -o BatchMode=yes "$H5" 'for p in $(pgrep -f "CarlaUE[45]"); do kill $p; done; sleep 5; for p in $(pgrep -f "CarlaUE[45]"); do kill -9 $p; done' 2>/dev/null || true
sleep 3
if ! timeout 15 ssh -o BatchMode=yes "$H5" 'ss -ltn | grep -q ":2000 "'; then
  say "CARLA 기동"
  timeout 30 ssh -o BatchMode=yes "$H5" \
    'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -RenderOffScreen -nosound > ~/icepredict/logs/carla_rerun.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 18); do
    sleep 10
    timeout 15 ssh -o BatchMode=yes "$H5" 'ss -ltn | grep -q ":2000 "' && { sleep 20; say "CARLA 기동 완료"; break; }
    [ "$i" = 18 ] && { say "CARLA 기동 실패"; exit 1; }
  done
else
  say "CARLA 이미 떠 있음"
fi

say "=== 2b) 월드 확인 (유령 차량 없어야 한다) ==="
timeout 60 ssh -o BatchMode=yes "$H5" '$HOME/miniconda3/envs/icepredict/bin/python -c "
import carla
c=carla.Client(\"127.0.0.1\",2000); c.set_timeout(20); w=c.get_world()
print(\"차량\", len(w.get_actors().filter(\"vehicle.*\")), \"센서\", len(w.get_actors().filter(\"sensor.*\")))
"' 2>&1 | grep -v Warning | tee -a "$ST"

say "=== 3) 기준선 재실행 ==="
WEATHERS="${WEATHERS:-ClearNoon WetNoon ClearNight Snow}" SCEN="${SCEN:-nodefense_traffic nodefense}" \
  bash "$SP/scripts/demo_batch_5090.sh" > /tmp/rerun_baseline.log 2>&1
grep -E "^\[[0-9:]+\] ===|진입|제어 상실|스핀|이탈|충돌|통과|Traceback" /tmp/demo_batch_status.txt | tail -40 | cut -c1-140 >> "$ST"

say "=== 4) 정리·집계·미디어 갱신 ==="
python3 "$SP/scripts/extract_photos.py"   >> "$ST" 2>&1
python3 "$SP/scripts/organize_media.py"   >> "$ST" 2>&1
python3 "$SP/scripts/summarize_runs.py"   > /dev/null 2>&1
python3 "$SP/scripts/make_figures.py"     >> "$ST" 2>&1
bash    "$SP/scripts/build_readme_media.sh" >> "$ST" 2>&1
bash    "$SP/scripts/sync_evidence.sh"    >> "$ST" 2>&1
say "=== 기준선 재실행 완료 ==="
