#!/usr/bin/env bash
# 실측에서 자동 생성된 근거 자료를 저장소 안으로 복사한다.
#
# 왜 복사인가: 생성기들은 `logs/carla_demo/정리/` 에 쓰도록 되어 있고 logs/ 는 .gitignore 대상이다.
# 그 결과 프로젝트에서 가장 설득력 있는 자료(분석 문서 17개·그림 28장)가 git 밖에 있었다.
# 생성 경로는 건드리지 않고, 공개용 사본만 docs/ 로 가져온다. 생성기를 다시 돌린 뒤 이 스크립트를 실행하면 갱신된다.
#
# events_*.json 을 함께 올리는 것이 핵심이다. CARLA 도 보드도 없는 사람이 저장소만 받아
# `sw/scripts/analysis/summarize_runs.py` 같은 분석기를 돌려 같은 표를 재생성할 수 있다.
set -eu
ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
SRC="$ROOT/logs/carla_demo"
cd "$ROOT"
mkdir -p docs/evidence docs/figures docs/data/events

cp -f "$SRC/정리"/*.md docs/evidence/ 2>/dev/null || true
cp -f "$SRC/정리/00_목록.txt" "$SRC/정리/00_읽어보기.txt" docs/evidence/ 2>/dev/null || true
cp -f "$SRC/figures"/*.jpg docs/figures/ 2>/dev/null || true
cp -f "$SRC"/events_*.json docs/data/events/ 2>/dev/null || true

printf '문서 %s개  그림 %s장  이벤트 %s건  (%s)\n' \
  "$(ls docs/evidence/*.md 2>/dev/null | wc -l)" \
  "$(ls docs/figures/*.jpg 2>/dev/null | wc -l)" \
  "$(ls docs/data/events/*.json 2>/dev/null | wc -l)" \
  "$(du -sh docs | cut -f1)"
