#!/usr/bin/env bash
# README 에 바로 보이는 미디어를 만든다. 원본 영상(7.8 GB)은 저장소에 못 넣으므로
# 핵심 장면만 애니메이션 WebP(인라인 재생) + 작은 MP4(내려받기용) 로 줄인다.
#
# WebP 를 쓰는 이유: 같은 화질에서 GIF 의 1/4 크기다 (720px·10fps·6s 기준 1.2 MB 대 4.3 MB).
# GIF 는 256색이라 노면 같은 사진 장면에서 띠가 보인다. GitHub 은 .webp 를 인라인으로 렌더한다.
# 혹시 애니메이션이 안 보이면 GIF=1 로 다시 돌려 .gif 를 만들고 README 의 확장자만 바꾸면 된다.
#
# 사용: bash scripts/build_readme_media.sh [GIF=1]
set -eu
ROOT=/mnt/ssd/icepredict
SRC="$ROOT/logs/carla_demo/정리"
OUT="$ROOT/media"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"
GIF=${GIF:-0}

# 이름|소스 영상|시작(s)|길이(s)|가로폭|설명
CLIPS="
hero_compare|$SRC/G_비교_방어없음_vs_RTOS/compare_ClearNoon_nodefense_traffic_vs_ClearNoon_miss_rtos_traffic_bev.mp4|6|8|760|왼쪽 방어 없음 vs 오른쪽 RTOS 2차 방어
primary_stop|$SRC/A_1차방어_카메라인식_정지/맑은낮_40kmh_카메라인식_빙판24m앞정지/영상_1인칭+조감.mp4|4.2|5.0|720|1차 방어: 카메라가 보고 빙판 24 m 앞 정지
secondary_rtos|$SRC/B_2차방어_카메라미인식_IMU개입/맑은낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0|8.0|720|2차 방어: 미끄러짐 확정 후 보드가 차선 유지하며 정지
nodefense_spin|$SRC/F_방어없음_기준선/맑은낮_40kmh_방어없음_빙판진입_스핀_제어상실_주변차량/영상_조감.mp4|9.5|8.0|680|방어 없음: 차선 이탈 뒤 스핀
lidar_semantic|$SRC/B_2차방어_카메라미인식_IMU개입/맑은낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_라이다_클래스색.mp4|5.5|7.0|680|시맨틱 라이다: 빙판(청록)·차량 3D 박스
snow_night|$SRC/B_2차방어_카메라미인식_IMU개입/눈_40kmh_카메라미인식_빙판진입_RTOS2차방어/영상_1인칭+조감.mp4|5.0|7.0|720|눈 내리는 날 같은 시나리오
limit_60kph|$SRC/C_2차방어_카메라경고늦음_IMU개입/맑은낮_60kmh_카메라경고늦음_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|4.5|8.0|720|60 km/h 한계: 2차가 개입해도 앞차와 충돌
false_alarm|$SRC/D_카메라오경보/맑은밤_40kmh_카메라오경보_빙판41m밖에서정지/영상_1인칭+조감.mp4|2.5|6.0|720|1차 한계: 빙판이 안 보이는 거리에서 오경보
"

echo "== 애니메이션 만들기 =="
echo "$CLIPS" | while IFS='|' read -r name src ss dur w desc; do
  [ -z "${name:-}" ] && continue
  if [ ! -f "$src" ]; then echo "  건너뜀 (원본 없음): $name"; continue; fi
  ffmpeg -nostdin -v error -ss "$ss" -t "$dur" -i "$src" \
         -vf "fps=10,scale=$w:-2:flags=lanczos" -c:v libwebp -lossless 0 -q:v 55 -loop 0 -an \
         -y "$OUT/$name.webp"
  if [ "$GIF" = 1 ]; then
    ffmpeg -nostdin -v error -ss "$ss" -t "$dur" -i "$src" -vf "fps=8,scale=$((w-80)):-2:flags=lanczos,palettegen=stats_mode=diff" -y "$TMP/p.png"
    ffmpeg -nostdin -v error -ss "$ss" -t "$dur" -i "$src" -i "$TMP/p.png" \
           -lavfi "fps=8,scale=$((w-80)):-2:flags=lanczos,paletteuse=dither=bayer:bayer_scale=4" -y "$OUT/$name.gif"
  fi
  echo "  $name.webp  $(du -h "$OUT/$name.webp" | cut -f1)  — $desc"
done

echo "== 전체 영상 (작게 다시 인코딩, 내려받기용) =="
mkdir -p "$OUT/clips"
echo "$CLIPS" | while IFS='|' read -r name src ss dur w desc; do
  [ -z "${name:-}" ] && continue
  [ -f "$src" ] || continue
  ffmpeg -nostdin -v error -i "$src" -vf "scale=960:-2" -c:v libx264 -crf 30 -preset slow -an -movflags +faststart \
         -y "$OUT/clips/$name.mp4"
  echo "  clips/$name.mp4  $(du -h "$OUT/clips/$name.mp4" | cut -f1)"
done

echo "== 합계: $(du -sh "$OUT" | cut -f1) =="
