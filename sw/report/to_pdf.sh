#!/usr/bin/env bash
# 개발완료보고서 → 제출용 .pdf. PDF 에서는 영상이 재생되지 않으므로 REPORT_PDF=1 로 영상 자리에 장면 캡처 4컷을 넣은 사본을 만들어 변환한다.
set -eu
D=$(cd "$(dirname "$0")" && pwd); N=2026ESWContest_모빌리티_ARTS_개발완료보고서_슬라이드
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
REPORT_PDF=1 python3 "$D/build_pptx.py" >/dev/null
cp "${TMPDIR:-/tmp}/IcePredict_report_pdfbuild.pptx" "$T/$N.pptx"
soffice --headless --convert-to pdf --outdir "$T" "$T/$N.pptx" >/dev/null
cp "$T/$N.pdf" "$D/$N.pdf"; echo "저장: $D/$N.pdf"
