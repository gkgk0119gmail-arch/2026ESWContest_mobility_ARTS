#!/usr/bin/env bash
# 본 배치(batch_5090.sh)가 끝나면 이어서 돌린다:
#  1) 음성 대조군 — 빙판이 없는 같은 경로. 여기서 나오는 1차 경보는 전부 오경보이므로
#     시뮬 오경보율을 처음으로 잴 수 있다 (지금까지 모든 주행에 빙판이 있어 잰 적이 없다).
#  2) 회피 시나리오 재확인 — 앞차 간격이 999 가 아닌 값으로 나오는지.
#  3) 분석 재생성.
# 실행: setsid nohup bash sw/scripts/archive/after_5090_control.sh > /tmp/after_control.log 2>&1 &
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; H5=${RENDER_HOST:-user@render-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

say "=== 후속: 본 배치 종료 대기 ==="
for i in $(seq 1 240); do
  pgrep -f "batch_5090.sh" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "batch_5090.sh" > /dev/null 2>&1 && { say "본 배치가 2시간 넘게 안 끝났다 — 후속 중단"; exit 1; }
say "본 배치 종료 확인"

# CARLA 생존 확인
ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' || {
  say "CARLA 없음 — 재기동"
  ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 18); do sleep 10; ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' && break; done
}

say "=== 음성 대조군: 빙판 없는 주행 8종 ==="
DESK=$H5 WEATHERS="ClearNoon WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight MidRainyNight Snow" \
  SCEN=control VIEWS=split bash "$SP/sw/scripts/sim/demo_batch_5090.sh" > /tmp/demo_control.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|Traceback" /tmp/demo_batch_status.txt | tail -20 | cut -c1-150 | tee -a "$ST"

say "=== 회피 재확인: 주변차량 포함 대조군 2종 ==="
DESK=$H5 WEATHERS="ClearNoon WetNoon" SCEN=control_traffic VIEWS=split \
  bash "$SP/sw/scripts/sim/demo_batch_5090.sh" > /tmp/demo_control_traffic.log 2>&1

say "=== 회수 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null

say "=== 분석 재생성 ==="
python3 "$SP/sw/scripts/analysis/analyze_detection.py" > /dev/null 2>&1 && say "01_탐지성능.md 갱신"
python3 "$SP/sw/scripts/analysis/summarize_runs.py"   > /dev/null 2>&1 && say "00_집계.md 갱신"

# 대조군에서 1차 경보가 몇 번 났는지 — 이 숫자가 시뮬 오경보율이다
python3 - <<'PY' | tee -a "$ST"
import json, glob, os
fs = sorted(glob.glob("/mnt/ssd/icepredict/logs/carla_demo/events_*control*.json"))
n = fa = 0
rows = []
for f in fs:
    d = json.load(open(f))
    ev = d.get("events", []) if isinstance(d, dict) else d
    a = d.get("args", {}) if isinstance(d, dict) else {}
    if not a.get("control_no_ice"):
        continue
    n += 1
    w = [e for e in ev if e.get("event") == "primary_warning"]
    if w:
        fa += 1
        rows.append((os.path.basename(f)[7:-5], w[0].get("t"), w[0].get("risk")))
print(f"[대조군] 빙판 없는 주행 {n}건 중 1차 경보 {fa}건 → 시뮬 오경보율 {100*fa/max(n,1):.0f}%")
for t, tt, r in rows:
    print(f"  {t}: t={tt}s risk={r}")
PY

say "=== 후속 체인 완료 ==="
