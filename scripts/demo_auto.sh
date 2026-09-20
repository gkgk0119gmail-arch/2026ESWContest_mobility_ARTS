#!/usr/bin/env bash
# Pi에서 무인 실행: 보드 배포 완료를 기다렸다가 → 보드 상태 확인 → CARLA 데모(보드 NPU 경유) →
# 영상 회수 → **이전 영상 삭제** → 요약.
# 데모는 데스크탑에서 bind, Pi 브리지가 connect 하는 구조라 순서를 지켜야 한다 (학교망이 반대 방향을 막는다).
set -u
DESK=yax@165.132.135.77
SP=/mnt/ssd/icepredict
LOG=/tmp/demo_auto.log
STATUS=/tmp/demo_auto_status.txt
BOARD=192.168.50.158
: > "$STATUS"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$STATUS"; }

say "=== 0) 데스크탑 배포 완료 대기 (최대 60분) ==="
# 프로세스 부재로 판단하면 안 된다 — 체인이 아직 시작 전이거나 재시작 중이면 곧바로 오판한다
# (실제로 그렇게 조기 종료했다). 상태 파일의 완료/중단 표식만 믿는다.
DONE=""
for i in $(seq 1 180); do
  ST=$(ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/spec_deploy_status.txt 2>/dev/null')
  case "$ST" in
    *"배포 완료"*)   DONE=ok;   break ;;
    *"판정 FAIL"*)   DONE=fail; break ;;
    *"!! 중단"*)     DONE=abort; break ;;
  esac
  sleep 20
done
say "데스크탑 결과: ${DONE:-timeout}"
[ "$DONE" = ok ] || { say "배포가 완료되지 않았다 — 데모를 돌리지 않는다"; \
  ssh -o BatchMode=yes "$DESK" 'tail -6 ~/icepredict/logs/spec_deploy_status.txt 2>/dev/null' | tee -a "$STATUS"; exit 0; }

say "=== 1) 보드 생존 확인 ==="
ping -c 2 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 ping 실패 — 중단"; exit 1; }
python3 "$SP/scripts/n6_frame_client.py" --ip "$BOARD" --repeat 1 2>/dev/null | tail -1 | tee -a "$STATUS"

say "=== 2) 데모 (데스크탑 bind → Pi 브리지 connect) ==="
# ssh 는 원격 백그라운드 작업이 끝날 때까지 돌아오지 않았다 (실제로 브리지를 못 띄워 교착) → setsid 로 세션 분리 + timeout 보호
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup timeout 1500 ~/icepredict/venv/bin/python scripts/carla_demo.py --target-kph 40 --fusion n6npu > ~/icepredict/logs/demo_v3.log 2>&1 < /dev/null &' || true
sleep 25
bash "$SP/scripts/n6_bridge_restart.sh" 150 /tmp/n6b_v3.log > /dev/null 2>&1
say "브리지 연결: $(tail -1 /tmp/n6b_v3.log | cut -c1-80)"
for i in $(seq 1 60); do
  ssh -o BatchMode=yes "$DESK" 'grep -qE "정지 완료|융합 경로|=== 요약|Traceback" ~/icepredict/logs/demo_v3.log' && break
  sleep 10
done
ssh -o BatchMode=yes "$DESK" 'grep -vE "Warning|warn|대기 중" ~/icepredict/logs/demo_v3.log | tail -12' | tee -a "$STATUS"

say "=== 3) 영상 회수 + 이전 영상 삭제 ==="
rsync -az "$DESK":~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
NEW=$(ls -t "$SP"/logs/carla_demo/demo_*n6npu*.mp4 2>/dev/null | head -1)
if [ -z "$NEW" ]; then say "새 영상이 없다 — 이전 영상은 그대로 둔다"; exit 0; fi
say "새 영상: $(basename "$NEW") ($(stat -c %s "$NEW") B)"
for f in "$SP"/logs/carla_demo/demo*.mp4; do
  [ "$f" = "$NEW" ] && continue
  rm -f "$f" && say "이전 영상 삭제: $(basename "$f")"
done
rm -rf "$SP/logs/carla_demo/_old"
# 바탕화면 전달 폴더도 새 영상 하나만 남긴다 (사용자 지시: 새 영상 만들면 이전 영상은 지운다)
DEST="$SP/icepredict_영상"; mkdir -p "$DEST"
rm -f "$DEST"/*.mp4   # (find 는 심링크 폴더를 안 따라간다)
if [ "$(readlink -f "$DEST")" = "$(readlink -f "$SP/logs/carla_demo")" ]; then
  say "전달: $DEST 는 logs/carla_demo 의 심링크 — 같은 파일 $(basename "$NEW")"
else
  cp "$NEW" "$DEST/" && say "전달: $DEST/$(basename "$NEW")"
fi
say "남은 영상: $(ls "$SP"/logs/carla_demo/*.mp4 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"
say "=== 완료 ==="
