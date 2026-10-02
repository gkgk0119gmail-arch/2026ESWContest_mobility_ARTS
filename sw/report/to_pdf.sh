#!/usr/bin/env bash
# PPT → PDF. 제출은 PDF 지만 원본은 .pptx 한 벌이다.
# 예전에는 HTML 을 chromium 으로 인쇄했는데, 그러면 PPT 와 PDF 가 서로 다른 원본에서 나와
# 한쪽만 고치면 조용히 어긋난다. 지금은 항상 .pptx 를 변환한다.
# 영상이 든 pptx 를 그대로 변환하면 LibreOffice 가 영상을 PDF 에 묻어 27 MB 가 되므로,
# pdf_prep.py 로 영상을 포스터 그림으로 바꾼 임시 사본을 변환한다.
set -eu
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PPTX="$HERE/2026ESWContest_모빌리티_ARTS_개발완료보고서.pptx"
PROF=$(mktemp -d); trap 'rm -rf "$PROF"' EXIT
command -v soffice >/dev/null || { echo "libreoffice 가 없다: sudo apt-get install -y --no-install-recommends libreoffice-impress"; exit 1; }
TMP="$PROF/$(basename "$PPTX")"
python3 "$HERE/pdf_prep.py" "$PPTX" "$TMP"
soffice --headless -env:UserInstallation="file://$PROF/lo" --convert-to pdf --outdir "$PROF" "$TMP" >/dev/null 2>&1
PDF="${PPTX%.pptx}.pdf"
mv "${TMP%.pptx}.pdf" "$PDF"
echo "저장: $PDF  ($(du -h "$PDF" | cut -f1), $(pdfinfo "$PDF" | awk '/^Pages/{print $2}')쪽)"
