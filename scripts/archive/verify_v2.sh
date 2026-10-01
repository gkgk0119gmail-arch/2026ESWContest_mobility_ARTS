#!/usr/bin/env bash
# 문턱 재교정 + 강수 게이트 통합 검증.
# 확인: (1) 맑음·야간·젖음에서 오경보가 사라지는가  (2) 폭우에서 1차가 스스로 꺼지는가
#       (3) 맑은 날 탐지는 유지되는가              (4) 빙판 없는 대조군 오경보가 줄었는가
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; H5=${RENDER_HOST:-user@render-host}; ST=/tmp/after_batch_status.txt
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

carla_up(){ ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "'; }
carla_up || { say "CARLA 없음 — 중단"; exit 1; }

say "=== v2 검증 1/2: detect 6종 ==="
DESK=$H5 WEATHERS="ClearNoon WetNoon ClearNight CloudyNoon HardRainNoon HardRainSunset" \
  SCEN=detect VIEWS=split bash "$SP/scripts/sim/demo_batch_5090.sh" > /tmp/v2_detect.log 2>&1

carla_up || { say "CARLA 죽음 — 대조군 생략"; exit 1; }
say "=== v2 검증 2/2: 대조군 4종 (빙판 없음) ==="
DESK=$H5 WEATHERS="ClearNoon WetNoon ClearNight HardRainNoon" \
  SCEN=control VIEWS=split bash "$SP/scripts/sim/demo_batch_5090.sh" > /tmp/v2_control.log 2>&1

say "=== 회수·분석 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/analysis/merge_wcet.py" > /dev/null 2>&1
python3 "$SP/scripts/analysis/analyze_detection.py" > /dev/null 2>&1 && say "01_탐지성능.md 갱신"
python3 "$SP/scripts/analysis/summarize_runs.py" > /dev/null 2>&1

python3 - <<'PY' | tee -a "$ST"
import json, glob, os
print("[v2] 주행별 결과 (문턱 재교정 + 강수 게이트)")
for f in sorted(glob.glob("/mnt/ssd/icepredict/logs/carla_demo/events_*.json")):
    tag = os.path.basename(f)[7:-5]
    if not (tag.endswith("_detect") or tag.endswith("_control")): continue
    d = json.load(open(f)); a = d.get("args", {}); ev = d.get("events", [])
    if not ev: continue
    pw = [e for e in ev if e["event"] == "primary_warning"]
    ctrl = "[대조군] " if a.get("control_no_ice") else ""
    off  = "[1차 꺼짐] " if a.get("disable_primary") else ""
    if pw:
        e = pw[0]
        print(f"  {tag:30s} {ctrl}{off}경보 t={e['t']:.2f}s risk={e.get('risk')} 가장자리={e.get('dist_to_edge_m')}m")
    else:
        print(f"  {tag:30s} {ctrl}{off}경보 없음")
PY
say "=== v2 검증 완료 ==="
