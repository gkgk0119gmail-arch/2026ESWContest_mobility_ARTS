#!/usr/bin/env bash
# 변동 스윕이 끝나면 확정 조건(연속 8프레임)을 포함한 대조군을 한 번 더 돌려 닫는다.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
# 주의: 앞 단계가 **아직 시작도 안 했을 때** 이 대기가 즉시 통과한다.
# 2026-09-21 03:30 에 그렇게 돼서 대조군이 변동 스윕보다 먼저 돌았다.
# 그래서 (1) 먼저 시작을 기다리고 (2) 그다음 종료를 기다린다.
WAIT_FOR=${WAIT_FOR:-"variation_sweep.sh"}
say "=== 후속: '$WAIT_FOR' 시작 대기 (최대 10분) ==="
STARTED=0
for i in $(seq 1 20); do
  pgrep -f "$WAIT_FOR" > /dev/null 2>&1 && { STARTED=1; break; }
  sleep 30
done
[ "$STARTED" = 0 ] && say "앞 단계가 시작되지 않았다 — 그냥 진행한다"
say "=== 후속: 종료 대기 ==="
for i in $(seq 1 180); do
  pgrep -f "$WAIT_FOR|verify_v2.sh|demo_batch_5090.sh" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "variation_sweep.sh|verify_v2.sh|demo_batch_5090.sh" > /dev/null 2>&1 && { say "미종료 — 중단"; exit 1; }
say "=== 확정 조건 포함 대조군 재측정 (4종) ==="
DESK=${RENDER_HOST:-user@render-host} WEATHERS="ClearNoon WetNoon ClearNight Snow" \
  SCEN=control VIEWS=split bash scripts/sim/demo_batch_5090.sh > /tmp/v3_control.log 2>&1
rsync -az ${RENDER_HOST:-user@render-host}:~/icepredict/logs/carla_demo/ logs/carla_demo/ 2>/dev/null
python3 scripts/analysis/merge_wcet.py > /dev/null 2>&1
python3 scripts/analysis/analyze_detection.py > /dev/null 2>&1 && say "01_탐지성능.md 갱신"
python3 - <<'PY' | tee -a /tmp/after_batch_status.txt
import json, glob, os
print("[v3] 확정 조건 포함 대조군")
for f in sorted(glob.glob("/mnt/ssd/icepredict/logs/carla_demo/events_*control*.json")):
    tag = os.path.basename(f)[7:-5]
    d = json.load(open(f)); ev = d.get("events", [])
    pw = [e for e in ev if e["event"] == "primary_warning"]
    ss = [e for e in ev if e["event"] == "secondary_slip"]
    print(f"  {tag:28s} 1차 {len(pw)}건  2차 {len(ss)}건" + (f"  risk={pw[0].get('risk')}" if pw else ""))
PY
say "=== 후속 완료 ==="
