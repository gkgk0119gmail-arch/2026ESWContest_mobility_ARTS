#!/usr/bin/env bash
# 보고서 PPT 에 '임베드' 할 영상을 만든다 (PowerPoint 가 바로 재생하는 H.264 MP4 + 포스터 프레임).
# ffmpeg 는 -nostdin 이 필수다. while read 루프 안에서 stdin(목록)을 ffmpeg 가 먹어 다음 줄이 사라진다.
# 원본은 logs/carla_demo/정리/ 의 주행 영상이라 저장소 밖에 있다. 만든 결과물(assets/video/)은 저장소에 함께 둔다.
#   bash sw/report/make_media.sh            # 전부
#   bash sw/report/make_media.sh v_compare  # 하나만
set -eu
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
SRC=${SRC:-$ROOT/logs/carla_demo/정리}
OUT="$HERE/assets/video"; mkdir -p "$OUT"
ONLY=${1:-}

# 이름 | 원본 | 시작(s) | 길이(s, 0=끝까지) | 가로 px(0=원본) | 포스터 시각(s, 원본 기준) | 설명
CLIPS=$(cat <<'T'
v_compare|G_비교_방어없음_vs_RTOS/compare_ClearNoon_nodefense_traffic_vs_ClearNoon_miss_rtos_traffic_split.mp4|0|0|1920|15.0|같은 조건 — 왼쪽 방어 없음 vs 오른쪽 STM32N6 RTOS 2차 방어
v_compare_bev|G_비교_방어없음_vs_RTOS/compare_ClearNoon_nodefense_traffic_vs_ClearNoon_miss_rtos_traffic_bev.mp4|0|0|0|15.0|같은 조건 조감 비교 — 왼쪽 방어 없음 vs 오른쪽 RTOS 2차 방어
v_primary|A_1차방어_카메라인식_정지/맑은낮_40kmh_카메라인식_빙판24m앞정지/영상_1인칭+조감.mp4|0|0|0|6.1|1차 방어 — 카메라가 보고 빙판 23.6 m 앞 정지
v_secondary|B_2차방어_카메라미인식_IMU개입/맑은낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|0|0|0|7.62|2차 방어 — 미끄러짐 확정 후 차선 유지 정지
v_nodefense|F_방어없음_기준선/맑은낮_40kmh_방어없음_빙판진입_wall충돌_12kmh/영상_1인칭+조감.mp4|0|0|0|12.3|방어 없음 — 차선 이탈 뒤 방호벽 충돌 12 km/h
v_lidar|B_2차방어_카메라미인식_IMU개입/맑은낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_라이다_클래스색.mp4|0|0|0|6.0|시맨틱 라이다 — 빙판(청록)·차량 3D 박스
T
)
command -v ffmpeg >/dev/null || { echo "ffmpeg 가 없다"; exit 1; }
while IFS='|' read -r name rel ss dur w poster desc; do
  [ -z "$name" ] && continue
  [ -n "$ONLY" ] && [ "$ONLY" != "$name" ] && continue
  in="$SRC/$rel"; [ -f "$in" ] || { echo "없음: $in"; exit 1; }
  vf="fps=30,format=yuv420p"
  [ "$w" != "0" ] && vf="scale=$w:-2:flags=lanczos,$vf"
  vf="$vf,scale=trunc(iw/2)*2:trunc(ih/2)*2"
  t=(); [ "$dur" != "0" ] && t=(-t "$dur")
  ffmpeg -nostdin -v error -y -ss "$ss" -i "$in" "${t[@]}" -an -vf "$vf" \
    -c:v libx264 -profile:v high -level 4.1 -preset medium -crf 23 -movflags +faststart "$OUT/$name.mp4"
  # 포스터: 재생 전에 보이는 정지 화면 (PDF 에도 이 그림이 들어간다)
  ffmpeg -nostdin -v error -y -ss "$poster" -i "$in" -frames:v 1 -q:v 2 "$OUT/$name.jpg"
  printf "%-16s %6s  %s  — %s\n" "$name" "$(du -h "$OUT/$name.mp4" | cut -f1)" \
    "$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "$OUT/$name.mp4")" "$desc"
done <<< "$CLIPS"
echo "합계: $(du -sh "$OUT" | cut -f1)"

# ── 전방 카메라만 16:9 로 자른 판 (19쪽 한계 카드) ──
if [ -z "$ONLY" ] || [ "$ONLY" = "v_limit60_cam" ]; then
  in="$SRC/C_2차방어_카메라경고늦음_IMU개입/맑은낮_60kmh_카메라경고늦음_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4"
  ffmpeg -nostdin -v error -y -i "$in" -an -vf "crop=640:360:0:60,fps=30,format=yuv420p" \
    -c:v libx264 -profile:v high -level 4.1 -preset medium -crf 23 -movflags +faststart "$OUT/v_limit60_cam.mp4"
  ffmpeg -nostdin -v error -y -ss 7.4 -i "$in" -frames:v 1 -vf "crop=640:360:0:60" -q:v 2 "$OUT/v_limit60_cam.jpg"
  echo "v_limit60_cam $(du -h "$OUT/v_limit60_cam.mp4" | cut -f1)"
fi

# ── 정지 화면: 같은 빙판, 다른 날씨 (12쪽 맥락 설명용). 1인칭 왼쪽 절반(640×480) ──
if [ -z "$ONLY" ] || [ "$ONLY" = "stills" ]; then
  STILLS=$(cat <<'T'
w_clear|B_2차방어_카메라미인식_IMU개입/맑은낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_rainnight|B_2차방어_카메라미인식_IMU개입/비오는밤_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_snow|B_2차방어_카메라미인식_IMU개입/눈_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_heavyrain|B_2차방어_카메라미인식_IMU개입/폭우_낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_sunset|B_2차방어_카메라미인식_IMU개입/맑은해질녘_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_night|B_2차방어_카메라미인식_IMU개입/맑은밤_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_wet|B_2차방어_카메라미인식_IMU개입/젖은노면_낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
w_cloudy|B_2차방어_카메라미인식_IMU개입/흐린낮_40kmh_1차끔_빙판진입_RTOS2차방어_주변차량/영상_1인칭+조감.mp4|5.0
T
)
  while IFS='|' read -r name rel t; do
    [ -z "$name" ] && continue
    in="$SRC/$rel"; [ -f "$in" ] || { echo "없음: $in"; exit 1; }
    ffmpeg -nostdin -v error -y -ss "$t" -i "$in" -frames:v 1 -vf "crop=640:480:0:0" -q:v 2 "$HERE/assets/$name.jpg"
    echo "still $name"
  done <<< "$STILLS"
fi

# ── 날씨 4종 비교 영상의 마지막 장면 (17쪽 작은 그림) ──
if [ -z "$ONLY" ] || [ "$ONLY" = "stills" ]; then
  for pair in c_clearnoon:ClearNoon c_clearnight:ClearNight c_wetnoon:WetNoon c_snow:Snow; do
    name=${pair%%:*}; wx=${pair##*:}
    in="$SRC/G_비교_방어없음_vs_RTOS/compare_${wx}_nodefense_traffic_vs_${wx}_miss_rtos_traffic_bev.mp4"
    [ -f "$in" ] || { echo "없음: $in"; exit 1; }
    ffmpeg -nostdin -v error -y -sseof -0.5 -i "$in" -frames:v 1 -q:v 2 "$HERE/assets/$name.jpg"
    echo "still $name"
  done
fi
