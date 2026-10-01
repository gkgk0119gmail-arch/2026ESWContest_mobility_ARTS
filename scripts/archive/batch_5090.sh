#!/usr/bin/env bash
# RTX 5090 에서 CARLA 를 돌려 남은 시나리오를 마무리한다 (3090 은 다른 연구자 작업으로 GPU 포화).
# 보드 굽기는 3090 의 USB(ST-LINK)로 하므로 손대지 않는다. 데모/서버만 5090.
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; H5=${RENDER_HOST:-user@render-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
say "=== 5090: CARLA 다운로드·압축해제 대기 ==="
for i in $(seq 1 120); do
  ssh -o BatchMode=yes $H5 'grep -q "압축 해제 완료" ~/dl_carla.log' 2>/dev/null && break
  ssh -o BatchMode=yes $H5 'grep -qE "중단|크기 불일치" ~/dl_carla.log' 2>/dev/null && { say "다운로드 실패"; ssh -o BatchMode=yes $H5 'tail -3 ~/dl_carla.log'; exit 1; }
  sleep 30
done
ssh -o BatchMode=yes $H5 'tail -3 ~/dl_carla.log | cut -c1-120' | tee -a $ST
say "=== 5090: CARLA 서버 기동 ==="
# 이미 떠 있으면 다시 띄우지 않는다. 두 번째 서버는 2000 포트를 못 잡고 GPU만 먹는다.
if ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "'; then say "CARLA 이미 기동됨 — 재기동 생략"; else
ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
fi
for i in $(seq 1 18); do sleep 10; ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' && break; done
ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' || { say "CARLA 기동 실패"; ssh -o BatchMode=yes $H5 'tail -5 ~/icepredict/logs/carla_server.log | cut -c1-140'; exit 1; }
sleep 20; say "CARLA 5090 기동 완료 (포트 2000)"
say "=== 5090: 검증 주행 1회 (맑음, 미인식+주변차량) ==="
DESK=$H5 WEATHERS=ClearNoon SCEN=miss_rtos_traffic bash $SP/scripts/sim/demo_batch_5090.sh > /tmp/demo_5090_first.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback" /tmp/demo_batch_status.txt | tail -8 | cut -c1-150 | tee -a $ST
ssh -o BatchMode=yes $H5 'grep -E "정차 차량 위치|traffic\]" ~/icepredict/logs/demo_ClearNoon_miss_rtos_traffic.log | head -3 | cut -c1-140' | tee -a $ST
if ! ssh -o BatchMode=yes $H5 'grep -q "=== 요약" ~/icepredict/logs/demo_ClearNoon_miss_rtos_traffic.log'; then say "검증 주행 실패 — 중단"; exit 1; fi
say "=== 5090: 본 실행 — 미인식+주변차량 8종 ==="
run5() { DESK=$H5 KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/scripts/sim/demo_batch_5090.sh > /tmp/demo_5090.log 2>&1
  grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST; }
run5 "WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight MidRainyNight Snow" miss_rtos_traffic
say "=== 5090: 인식+주변차량 (40·60) + 기준선(주변차량) ==="
run5 "ClearNoon WetNoon ClearSunset" detect_traffic; run5 ClearNoon detect_traffic 60
run5 "ClearNoon WetNoon ClearNight Snow" nodefense_traffic
say "=== 회수·비교·정리 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ $SP/logs/carla_demo/ 2>/dev/null
for W in ClearNoon WetNoon ClearNight Snow; do for V in bev split; do
  [ -f $SP/logs/carla_demo/demo_${W}_nodefense_traffic_$V.mp4 ] && [ -f $SP/logs/carla_demo/demo_${W}_miss_rtos_traffic_$V.mp4 ] && \
    python3 $SP/scripts/sim/compare_videos.py ${W}_nodefense_traffic ${W}_miss_rtos_traffic $V > /dev/null 2>&1
done; done
cp -f $SP/logs/carla_demo/compare_*.mp4 "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/" 2>/dev/null
python3 $SP/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/sim/organize_media.py >> $ST 2>&1
python3 $SP/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/scripts/analysis/summarize_runs.py > /dev/null 2>&1
say "영상 $(ls $SP/logs/carla_demo/demo_*.mp4 | wc -l)개, 사진 $(ls $SP/logs/carla_demo/photos/*.jpg | wc -l)장, 그림 $(ls $SP/logs/carla_demo/figures/*.jpg | wc -l)장"
say "=== 5090 체인 완료 ==="
