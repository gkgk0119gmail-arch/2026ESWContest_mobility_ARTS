#!/usr/bin/env bash
# 문턱 교정(0.441 → prior 기반 0.58~0.75) 검증.
# 확인할 것: (1) 예전에 오경보가 났던 폭우·야간에서 사라지는가
#            (2) 맑은 날 탐지가 유지되는가
#            (3) 빙판 없는 대조군 오경보가 줄었는가
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; H5=${RENDER_HOST:-user@render-host}; ST=/tmp/after_batch_status.txt
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' || {
  say "CARLA 재기동"
  ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 18); do sleep 10; ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' && break; done
}

say "=== 문턱 검증 1/2: 예전 오경보 날씨 + 맑은 날 (detect) ==="
DESK=$H5 WEATHERS="HardRainNoon HardRainSunset ClearNight ClearNoon WetNoon CloudyNoon" \
  SCEN=detect VIEWS=split bash "$SP/scripts/sim/demo_batch_5090.sh" > /tmp/vth_detect.log 2>&1

say "=== 문턱 검증 2/2: 빙판 없는 대조군 재측정 ==="
DESK=$H5 WEATHERS="HardRainNoon ClearNight WetNoon ClearNoon" \
  SCEN=control VIEWS=split bash "$SP/scripts/sim/demo_batch_5090.sh" > /tmp/vth_control.log 2>&1

say "=== 회수·분석 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/analysis/merge_wcet.py" > /dev/null 2>&1
python3 "$SP/scripts/analysis/analyze_detection.py" > /dev/null 2>&1 && say "01_탐지성능.md 갱신"
python3 "$SP/scripts/analysis/summarize_runs.py" > /dev/null 2>&1

python3 - <<'PY' | tee -a "$ST"
import json, glob, os
print("[문턱검증] 주행별 1차 경보")
for f in sorted(glob.glob("/mnt/ssd/icepredict/logs/carla_demo/events_*.json")):
    tag = os.path.basename(f)[7:-5]
    if not (tag.endswith("_detect") or tag.endswith("_control")): continue
    d = json.load(open(f)); a = d.get("args", {}); ev = d.get("events", [])
    pw = [e for e in ev if e["event"] == "primary_warning"]
    ctrl = " [대조군]" if a.get("control_no_ice") else ""
    if pw:
        e = pw[0]
        print(f"  {tag:34s}{ctrl} t={e['t']:.2f}s risk={e.get('risk')} 가장자리={e.get('dist_to_edge_m')}m")
    else:
        print(f"  {tag:34s}{ctrl} 경보 없음")
PY
say "=== 문턱 검증 완료 ==="
