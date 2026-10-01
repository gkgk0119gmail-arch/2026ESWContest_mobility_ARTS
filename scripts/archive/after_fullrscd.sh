#!/usr/bin/env bash
# 마지막 단계: 실사진 **전량**을 실물 보드에 넣는다.
#
# 지금까지 69,360 장 중 25,140 장(36 %)만 보드로 판정했다. 나머지 44,220 장을 마저 돌리면
# "실사진 전량을 실물 보드로 평가했다"가 된다. 대회 주제가 기존 데이터셋 활용이니
# 표집이 아니라 전량이라는 사실 자체가 근거가 된다.
#
# 보드는 CARLA 브리지와 공유하므로 반드시 주행 체인이 다 끝난 뒤에 돈다.
# 한 장에 약 50 ms (추론 25.5 + 전송) → 44,220 장이면 약 37 분.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속6: after_ctxtemp 종료 대기 ==="
for i in $(seq 1 60); do pgrep -f "after_ctxtem[p].sh" > /dev/null 2>&1 && break; sleep 10; done
for i in $(seq 1 300); do pgrep -f "after_ctxtem[p].sh" > /dev/null 2>&1 || break; sleep 30; done
# 브리지가 보드를 붙잡고 있으면 안 된다
for p in $(pgrep -f "n6_bridge.p[y]"); do kill "$p" 2>/dev/null; done
sleep 3
ping -c 1 -W 2 192.168.50.158 > /dev/null 2>&1 || { say "보드 무응답 — 중단"; exit 1; }

for SP in vali_20k test_50k; do
  say "=== 실사진 전량 평가: $SP ==="
  python3 scripts/deploy/rscd_board_eval.py --split "$SP" --all --resume 2>&1 | tail -8 | tee -a /tmp/after_batch_status.txt
done

say "=== 보고서 다시 생성 ==="
python3 scripts/deploy/rscd_board_eval.py --report-only > /dev/null 2>&1 && say "05_실사진_대규모평가.md 갱신"
python3 scripts/analysis/rscd_breakdown.py       > /dev/null 2>&1 && say "07_노면조건별_분해.md 갱신"
python3 scripts/model/spec_head_transfer.py   > /dev/null 2>&1 && say "08_반사도헤드_전이검증.md 갱신"
python3 scripts/analysis/board_verdict_figures.py > /dev/null 2>&1 && say "발표 그림 갱신"
python3 scripts/analysis/schedule_figure.py      > /dev/null 2>&1 && say "스케줄 그림 갱신"
python3 - <<'PY' | tee -a /tmp/after_batch_status.txt
import json, collections
c=collections.Counter(); sp=collections.Counter()
for l in open("/mnt/ssd/icepredict/logs/rscd_board_samples.jsonl"):
    if not l.strip(): continue
    r=json.loads(l); c[r["cls"]]+=1; sp[r.get("split","?")]+=1
print(f"[전량평가] 누적 {sum(c.values()):,}장  클래스 {dict(c)}  스플릿 {dict(sp)}")
PY
say "=== 후속6 완료 ==="
