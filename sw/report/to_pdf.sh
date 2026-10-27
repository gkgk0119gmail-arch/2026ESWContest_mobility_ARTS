#!/usr/bin/env bash
# 개발완료보고서 .pptx → 제출용 .pdf (영상은 포스터 그림으로 바꾼 임시 사본을 변환한다)
set -eu
D=$(cd "$(dirname "$0")" && pwd); N=2026ESWContest_모빌리티_ARTS_개발완료보고서
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
python3 "$D/pdf_prep.py" "$D/$N.pptx" "$T/$N.pptx"
soffice --headless --convert-to pdf --outdir "$T" "$T/$N.pptx" >/dev/null
cp "$T/$N.pdf" "$D/$N.pdf"; echo "저장: $D/$N.pdf"
