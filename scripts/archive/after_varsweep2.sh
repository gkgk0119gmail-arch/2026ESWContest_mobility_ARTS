#!/usr/bin/env bash
# 변동 스윕이 끝나면 **확정 계층까지 적용된** 대조군을 돌려 3층 방어를 닫는다.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속2: 변동 스윕 종료 대기 ==="
for i in $(seq 1 200); do
  pgrep -f "variation_swee[p].sh" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "variation_swee[p].sh" > /dev/null 2>&1 && { say "변동 스윕 미종료 — 중단"; exit 1; }
# 자식 배치가 남아 있을 수 있다
for i in $(seq 1 40); do
  pgrep -f "demo_batch_5090.sh" > /dev/null 2>&1 || break
  sleep 30
done
say "=== 확정 계층 포함 대조군 (4종) ==="
DESK=${RENDER_HOST:-user@render-host} WEATHERS="ClearNoon WetNoon ClearNight Snow" \
  SCEN=control VIEWS=split bash scripts/sim/demo_batch_5090.sh > /tmp/v4_control.log 2>&1
rsync -az ${RENDER_HOST:-user@render-host}:~/icepredict/logs/carla_demo/ logs/carla_demo/ 2>/dev/null
python3 scripts/analysis/merge_wcet.py > /dev/null 2>&1
python3 scripts/analysis/variation_report.py > /dev/null 2>&1 && say "11_변동스윕.md 생성"
python3 scripts/analysis/analyze_detection.py > /dev/null 2>&1 && say "01_탐지성능.md 갱신"
python3 scripts/analysis/summarize_runs.py > /dev/null 2>&1
python3 - <<'PY' | tee -a /tmp/after_batch_status.txt
import json, glob, os
print("[v4] 확정 계층 포함 대조군 (빙판 없음 → 경보는 전부 오경보)")
n=fa=0
for f in sorted(glob.glob("/mnt/ssd/icepredict/logs/carla_demo/events_*control*.json")):
    d=json.load(open(f)); a=d.get("args",{})
    if not a.get("control_no_ice"): continue
    tag=os.path.basename(f)[7:-5]; ev=d.get("events",[])
    pw=[e for e in ev if e["event"]=="primary_warning"]
    ss=[e for e in ev if e["event"]=="secondary_slip"]
    k=a.get("alarm_confirm","?")
    n+=1; fa+=1 if pw else 0
    print(f"  {tag:28s} confirm={k}  1차 {len(pw)}건  2차 {len(ss)}건" + (f"  risk={pw[0].get('risk')}" if pw else ""))
print(f"  → 대조군 {n}건 중 1차 오경보 {fa}건 ({100*fa/max(n,1):.0f}%)")
PY
say "=== 후속2 완료 ==="
