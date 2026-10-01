#!/usr/bin/env bash
# slides.html → PDF. chromium 헤드리스로 인쇄한다.
# 왜 chromium 인가: libreoffice 가 이 기계에 없어 pptx→pdf 경로를 쓸 수 없다.
# @page 를 13.333in × 7.5in (16:9) 로 잡아 두어 한 쪽에 한 슬라이드가 정확히 떨어진다.
set -eu
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:-"$ROOT/report/2026ESWContest_모빌리티_ARTS_개발완료보고서.pdf"}
PROF=$(mktemp -d); trap 'rm -rf "$PROF"' EXIT
chromium --headless --disable-gpu --no-sandbox --user-data-dir="$PROF" \
         --no-pdf-header-footer --print-to-pdf-no-header \
         --print-to-pdf="$OUT" "file://$ROOT/report/slides.html" 2>&1 | grep -vE "^\[|GPU|Vulkan|dbus|Fontconfig" || true
echo "저장: $OUT  ($(du -h "$OUT" | cut -f1))"
