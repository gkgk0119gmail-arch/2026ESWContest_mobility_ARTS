#!/usr/bin/env bash
# 반사도 헤드 파이프라인: 수집 완료 대기 → 멀티태스크 학습 → 결과 요약.
# 데스크탑에서 실행한다. 각 단계 실패 시 즉시 멈춘다 (중간 결과로 다음 단계를 돌리지 않는다).
set -u
P=$HOME/icepredict/venv/bin/python
CODE=$HOME/icepredict/code
LOGS=$HOME/icepredict/logs
DATA=${1:-$HOME/icepredict/dataset/carla_v3}

echo "== 1) 수집 완료 대기 =="
# pgrep 패턴을 대괄호로 쪼개 자기 명령줄과 매칭되지 않게 한다 (pkill/pgrep 자기매칭으로 셸이 죽은 적이 있다)
while pgrep -f "carla_collect.p[y].*carla_v3" > /dev/null; do sleep 20; done
if ! grep -q "수집 완료" "$LOGS/collect_v3.log"; then
  echo "!! 수집이 정상 종료되지 않았다 — 중단"; tail -5 "$LOGS/collect_v3.log"; exit 1
fi
grep -A 8 "=== 수집 완료 ===" "$LOGS/collect_v3.log" | sed -E "s/^\s+//"

echo; echo "== 2) 멀티태스크 학습 =="
cd "$CODE" || exit 1
$P -u sw/scripts/model/train_spec.py --carla "$DATA" --epochs 4 --steps-per-epoch 500 --rscd-test-per-class 300 \
   > "$LOGS/train_spec.log" 2>&1
rc=$?
grep -vE "Warning|warn|^\s*$" "$LOGS/train_spec.log" | grep -E "index:|라벨 분포|init:|^== ep|반사도|RSCD acc|CARLA|ONNX|Error|Traceback" | tail -24
[ $rc -ne 0 ] && { echo "!! 학습 실패 (rc=$rc)"; tail -15 "$LOGS/train_spec.log"; exit 2; }
echo "== 완료 =="
