#!/usr/bin/env bash
# 데스크탑 GPU 여유(≥ 9 GB, 2분 연속)가 생기면 CARLA 를 띄우고 V7e(주변 차량 재실행)를 이어간다. 다른 사람 프로세스는 건드리지 않는다.
# 별도 파일인 이유: 이 이름을 포함한 명령줄에서 pgrep -f 를 쓰면 자기 셸을 죽인다 (실제로 exit 144 로 끊겼다).
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
say "=== GPU 대기: 다른 사용자 작업(hyperpcn, ~20 GB) 종료 대기 중 ==="
ok=0
while true; do
  FREE=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits' 2>/dev/null | head -1)
  if [ -n "$FREE" ] && [ "$FREE" -ge 9000 ]; then ok=$((ok+1)); else ok=0; fi
  [ $ok -ge 4 ] && break
  sleep 30
done
say "GPU 여유 ${FREE} MiB 확보 — CARLA 재시작 후 V7e 진행"
bash $SP/sw/scripts/archive/batch_V7e.sh
