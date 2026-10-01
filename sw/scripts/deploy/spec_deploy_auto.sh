#!/usr/bin/env bash
# 반사도 헤드 학습 완료 후 → 판정 → int8 양자화 → NPU 코드 생성 → 펌웨어 빌드 → 보드 배포 까지 무인 실행.
# 데스크탑에서 돌린다. Pi가 해야 하는 데모/영상은 여기서 하지 않는다 (Pi 쪽 스크립트가 이어받는다).
#
# 설계 원칙
#  - 각 단계는 실패하면 즉시 멈추고 STATUS에 이유를 남긴다. 중간 산출물로 다음 단계를 돌리지 않는다.
#  - 판정 기준(얼음 반사도)을 코드로 박아 사람이 없어도 "되는지/안 되는지"가 남게 한다.
#  - 보드 배포는 개발 모드(BOOT1 오른쪽) 전제. 스위치가 실행 모드면 배포를 건너뛰고 그렇게 기록한다.
set -u
P=$HOME/icepredict/venv/bin/python
CODE=$HOME/icepredict/code
LOGS=$HOME/icepredict/logs
ST=$HOME/icepredict/tools/stedgeai/3.0
S=$ST/Utilities/linux/stedgeai
J=$HOME/icepredict/fw/neuralart_icepredict.json
PROF="icepredict-fsbl3@$J"
GCC=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
CP=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.400.202601091506/tools/bin
OUR=$HOME/icepredict/fw/n6root/Projects/STM32N6570-DK/Applications/NetXDuo/icepredict_n6
LIB=$HOME/icepredict/fw/npu_lib
SPECDIR=$HOME/icepredict/models/roadnet_v3_spec
EQ=$HOME/icepredict/models/roadnet_v3_eq
GEN=$HOME/icepredict/models/n6_gen_v3/out
STATUS=$LOGS/spec_deploy_status.txt
export PATH=$GCC:$PATH
: > "$STATUS"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$STATUS"; }
die() { say "!! 중단: $*"; exit 1; }

say "=== 0) 학습 완료 대기 ==="
while pgrep -f "train_spec.p[y]" > /dev/null || pgrep -f "spec_pipeline.s[h]" > /dev/null; do sleep 20; done
[ -f "$SPECDIR/best.pt" ] || die "학습 산출물 없음 ($SPECDIR/best.pt). train_spec.log 확인"
grep -E "^== ep|반사도 평균|RSCD acc|CARLA black_ice" "$LOGS/train_spec.log" 2>/dev/null | tail -8 | tee -a "$STATUS"

say "=== 1) 판정: 얼음 반사도가 융합에 쓸 만한가 ==="
VERDICT=$($P - "$SPECDIR/metrics.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1])); c = m["carla"]; sm = c["spec_mean"]
ice, wet, nor = sm["black_ice"], sm["wet"], sm["normal"]
# 기준을 '실측 발화율'로 잡는다. 이전 기준(얼음 반사도 >= 0.70)은 구형 p_ice에서 역산한 대리 지표라
# 근거가 약했다 — 실제로 분류기가 좋아지자(ice recall 0.64 -> 0.79) 대리 지표만 FAIL이 났다.
# 반사도는 물리 반사율이라 그대로는 융합(0~1 위험도)에 작아 보인다 → LO/HI 선형 보정 후 평가.
LO, HI = 0.09, 0.40
cal = lambda v: min(1.0, max(0.0, (v - LO) / (HI - LO)))
risk_ice = (0.35 * 0.738 + 0.45 * cal(ice)) / 0.80        # p_ice는 검증셋 실측 평균
risk_wet = (0.35 * 0.067 + 0.45 * cal(wet)) / 0.80
ok = (c["recall"]["black_ice"] >= 0.70) and (risk_ice >= 0.50) and (risk_wet <= 0.35) and (m["rscd"]["acc"] >= 0.88)
print(f"ice_recall={c['recall']['black_ice']:.3f} rscd_acc={m['rscd']['acc']:.4f} "
      f"spec ice {ice:.3f}->{cal(ice):.2f} wet {wet:.3f}->{cal(wet):.2f} | "
      f"risk 얼음 {risk_ice:.3f} 젖음 {risk_wet:.3f} (임계 0.441) -> {'PASS' if ok else 'FAIL'}")
PY
) || die "metrics.json 해석 실패"
say "$VERDICT"
case "$VERDICT" in
  *PASS*) say "판정 PASS — 배포 진행" ;;
  *) say "판정 FAIL — 배포하지 않는다"; exit 0 ;;
esac

say "=== 2) 균등화 + int8 양자화 (스템 절벽 처리는 동일) ==="
cd "$CODE" || die "코드 디렉터리 없음"
$P -u sw/scripts/model/stem_equalize.py --model "$SPECDIR/roadnet_spec.onnx" --out "$EQ/roadnet_eq_fp32.onnx" \
   >> "$LOGS/v3_quant.log" 2>&1 || die "균등화 실패 (v3_quant.log)"
grep -E "동치 검증" "$LOGS/v3_quant.log" | tail -1 | tee -a "$STATUS"
MULS=$(grep -oE "/features/features.0/stem_eq/Mul_s" "$LOGS/v3_quant.log" | head -1)
$P -u sw/scripts/model/ptq_int8_n6.py --model "$EQ/roadnet_eq_fp32.onnx" --calib 200 --eval-per-class 300 \
   --activation uint8 --override-tensor "/features/features.0/features.0.0/Conv_output_0_unscaled:-3:3" \
   --out "$EQ/ptq" >> "$LOGS/v3_quant.log" 2>&1 || die "PTQ 실패 (v3_quant.log)"
grep -E "RSCD  acc|CARLA black" "$LOGS/v3_quant.log" | tail -2 | tee -a "$STATUS"
$P sw/scripts/model/qdq_u8_to_i8.py "$EQ/ptq/roadnet_int8_uint8.onnx" "$EQ/ptq/roadnet_int8_signed.onnx" --check 8 \
   >> "$LOGS/v3_quant.log" 2>&1 || die "int8 변환/검증 실패"
grep -E "검증 8장" "$LOGS/v3_quant.log" | tail -1 | tee -a "$STATUS"

say "=== 3) NPU 코드 생성 ==="
rm -rf "$(dirname "$GEN")"; mkdir -p "$GEN"; cd "$(dirname "$GEN")" || die "생성 디렉터리"
echo n | timeout 900 "$S" generate --model "$EQ/ptq/roadnet_int8_signed.onnx" --type onnx --target stm32n6 \
   --st-neural-art "$PROF" --output "$GEN" > "$LOGS/v3_generate.log" 2>&1 || die "generate 실패"
grep -E "pure software|pure hardware" "$GEN/network_generate_report.txt" | sed -E "s/^\s+//" | tee -a "$STATUS"
OUTN=$($P - "$GEN/network_c_info.json" <<'PY'
import json, sys
j = json.load(open(sys.argv[1])); g = j["graphs"][0]; b = {x["id"]: x for x in j["buffers"]}
o = b[g["outputs"][0]]; print(o["shape"][-1], o.get("intq", {}).get("scales", [0])[0], o.get("intq", {}).get("offsets", [0])[0])
PY
) || die "출력 정보 해석 실패"
set -- $OUTN; N=$1; OSCALE=$2; OZP=$3
say "모델 출력 $N개 (scale $OSCALE zp $OZP)"
[ "$N" -ge 5 ] || die "출력이 $N개 — 반사도 헤드가 ONNX에 없다"

say "=== 4) 펌웨어 빌드 (NPU_OUT_N=$N) ==="
$P - "$LIB/npu_infer.h" "$N" "$OSCALE" "$OZP" <<'PY'
import re, sys
p, n, sc, zp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
s = open(p).read()
s = re.sub(r"#define NPU_OUT_N\s+\S+", f"#define NPU_OUT_N      {n}u", s)
s = re.sub(r"#define NPU_OUT_SCALE\s+\S+", f"#define NPU_OUT_SCALE  {float(sc):.10g}f", s)
s = re.sub(r"#define NPU_OUT_ZP\s+\S+", f"#define NPU_OUT_ZP     ({int(float(zp))})", s)
open(p, "w").write(s); print("npu_infer.h 갱신")
PY
cp "$LIB/npu_infer.h" "$OUR/FSBL/Core/Inc/" || die "헤더 복사"
grep -q "NPU_OUT_N >= 5" "$OUR/FSBL/NetXDuo/App/app_netxduo.c" || \
  $P "$LIB/patch_fw_spec.py" "$OUR/FSBL/NetXDuo/App/app_netxduo.c" || die "펌웨어 반사도 패치"
# Makefile의 NET 경로를 새 생성물로. 공백 개수에 의존하지 않게 -E 로 느슨하게 잡는다
#  (공백 2개로 썼다가 실제 파일이 3개라 치환이 조용히 실패 → 예전 4출력 모델로 빌드된 적이 있다)
sed -i -E "s|^NET[[:space:]]*\\?=.*|NET ?= $GEN|" "$LIB/Makefile"
grep -qF "$GEN" "$LIB/Makefile" || die "Makefile NET 경로 치환 실패"
cd "$LIB" && make -j8 NPU_MHZ=1000 \
  EXTRA_SRCS="$HOME/STM32CubeN6/Drivers/STM32N6xx_HAL_Driver/Src/stm32n6xx_hal_xspi.c $HOME/STM32CubeN6/Drivers/STM32N6xx_HAL_Driver/Src/stm32n6xx_hal_cacheaxi.c" \
  > "$LOGS/v3_lib.log" 2>&1 || { grep -m3 " error" "$LOGS/v3_lib.log"; die "라이브러리 빌드"; }
cd "$OUR/STM32CubeIDE/FSBL/Debug" && rm -f Nx_WebServer_FSBL.elf Application/User/NetXDuo/App/app_netxduo.o
make all -j8 > "$LOGS/v3_fw.log" 2>&1 || { grep -m3 -E " error|overflowed" "$LOGS/v3_fw.log"; die "펌웨어 빌드"; }
grep -E "^\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+" "$LOGS/v3_fw.log" | tail -1 | tee -a "$STATUS"

say "=== 5) 보드 배포 (개발 모드 필요) ==="
"$CP/STM32_Programmer_CLI" -c port=SWD mode=UR > /tmp/swd.log 2>&1
if ! grep -q "Device name" /tmp/swd.log; then
  say "SWD 연결 불가 — BOOT1이 실행 모드로 보인다. 펌웨어는 빌드됐고 배포만 남았다"; exit 0
fi
# Programmer CLI는 .raw 확장자를 거부한다 (.bin/.hex/.s19만) — .bin 사본으로 굽는다
cp "$GEN/network_atonbuf.xSPI2.raw" "$HOME/icepredict/fw/out/network_v3_weights.bin" || die "블롭 사본"
"$CP/STM32_Programmer_CLI" -c port=SWD mode=UR -hardRst -el "$CP/ExternalLoader/MX66UW1G45G_STM32N6570-DK.stldr" \
   -d "$HOME/icepredict/fw/out/network_v3_weights.bin" 0x71000000 -v > "$LOGS/v3_blob.log" 2>&1 \
   || { grep -iE "error" "$LOGS/v3_blob.log" | head -2; die "가중치 블롭 굽기"; }
say "가중치 블롭 0x71000000 굽기 완료"
cd "$OUR/STM32CubeIDE/FSBL/Debug" || die "빌드 디렉터리"
"$CP/STM32_SigningTool_CLI" -bin Nx_WebServer_FSBL.bin -nk -of 0x80000000 -t fsbl \
   -o "$HOME/icepredict/fw/out/icepredict_n6_v3-trusted.bin" -hv 2.3 -align -s > "$LOGS/v3_sign.log" 2>&1 \
   || die "서명"
"$CP/STM32_Programmer_CLI" -c port=SWD mode=UR -hardRst -el "$CP/ExternalLoader/MX66UW1G45G_STM32N6570-DK.stldr" \
   -d "$HOME/icepredict/fw/out/icepredict_n6_v3-trusted.bin" 0x70000000 -v >> "$LOGS/v3_sign.log" 2>&1 \
   || die "펌웨어 굽기"
say "펌웨어 0x70000000 굽기 완료 (BOOT0/BOOT1 왼쪽이면 독립 부팅)"
APID=1 bash "$HOME/icepredict/fw/n6_fw_load.sh" "" 16 > "$LOGS/v3_load.log" 2>&1
grep -E "NPU: (clocks|ready|init failed)|IpAddress|frames listening" "$LOGS/v3_load.log" | cut -c1-110 | tee -a "$STATUS"
say "=== 배포 완료 — Pi에서 데모 가능 ==="
