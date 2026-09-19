#!/usr/bin/env bash
# STM32N6570-DK 개발 모드에서 NPU 검증 펌웨어를 RAM에 적재해 실행하고 stedgeai validate로 실측한다.
# 사용: n6_npu_validate.sh D|hyb|hybfsbl     (BOOT1 오른쪽 = 개발/프로그래밍 모드에서만 동작)
set -u
M=${1:-hyb}
CP=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.400.202601091506/tools/bin
GS=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.stlink-gdb-server.linux64_2.2.400.202601091506/tools/bin/ST-LINK_gdbserver
GCC=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
R=$HOME/icepredict/tools/stedgeai/3.0; S=$R/Utilities/linux/stedgeai; PROF="n6-allmems-O3@$R/scripts/N6_scripts/user_neuralart.json"
EL=$CP/ExternalLoader/MX66UW1G45G_STM32N6570-DK.stldr
ELF=$HOME/icepredict/fw/npuval_$M.elf; RAW=$HOME/icepredict/fw/npuval_${M}_weights.bin
[ -f "$RAW" ] || cp "$HOME/icepredict/fw/npuval_${M}_weights.raw" "$RAW"   # CLI는 .raw 확장자를 거부한다
RESET=${RESET:-0}   # 1이면 gdbserver -k(리셋 후 halt): 개발 부팅 모드(BOOT1 오른쪽)에서만 성립
case $M in
  D)   ONNX=$HOME/icepredict/models/mp_D_min/roadnet_int8_int8.onnx ;;
  hyb) ONNX=$HOME/icepredict/models/roadnet_v2_hybrid_plain/roadnet_int8_signed.onnx ;;
  hybfsbl2) ONNX=$HOME/icepredict/models/roadnet_v2_hybrid_plain/roadnet_int8_signed.onnx
           PROF="icepredict-fsbl2@$HOME/icepredict/fw/neuralart_icepredict.json" ;;
  hybfsbl) ONNX=$HOME/icepredict/models/roadnet_v2_hybrid_plain/roadnet_int8_signed.onnx
           PROF="icepredict-fsbl@$HOME/icepredict/fw/neuralart_icepredict.json" ;;   # FSBL 회피 풀 (npuRAM+hyperRAM)
  *) echo "D|hyb|hybfsbl"; exit 2 ;;
esac
LOG=$HOME/icepredict/logs/npuval_$M; mkdir -p "$LOG"
# comm은 15자로 잘려 pkill -x "ST-LINK_gdbserver"가 절대 안 맞는다 → 포트/cmdline 기준으로 정리
fuser -k 61234/tcp 61235/tcp 2>/dev/null; pkill -f "ST-LINK_gdbserver" 2>/dev/null; pkill -f "arm-none-eabi-gdb -q" 2>/dev/null; sleep 1

echo "== 1) 가중치 블롭 → 외부 플래시 0x71000000 ($(stat -c %s "$RAW") B)"
"$CP/STM32_Programmer_CLI" -c port=SWD mode=UR -hardRst -el "$EL" -d "$RAW" 0x71000000 -v 2>&1 | grep -iE "voltage|verified|error" | head -3

echo "== 2) GDB 서버 (persistent, halt)"
KFLAG=""; [ "$RESET" = 1 ] && KFLAG="-k"
APFLAG=""; [ -n "${APID:-}" ] && APFLAG="-m $APID"   # N6는 AP가 여러 개 — CM55 AP를 지정해야 halt가 된다
"$GS" -p 61234 -d -s $KFLAG $APFLAG --halt -e -l 1 -cp "$CP" > "$LOG/gdbserver.log" 2>&1 &
sleep 4; grep -iE "listen|waiting|error|halt|device" "$LOG/gdbserver.log" | head -5
if grep -qiE "Error in initializing|Failed to halt" "$LOG/gdbserver.log"; then echo "!! gdbserver halt 실패 — 중단 (RESET=$RESET). 개발 모드(BOOT1 오른쪽)에서 RESET=1로 재시도"; pkill -f "ST-LINK_gdbserver"; exit 3; fi

echo "== 3) ELF 적재 + 실행 (UART 배너 12초 캡처)"
stty -F /dev/ttyACM0 921600 raw -echo
( timeout 12 cat /dev/ttyACM0 > "$LOG/uart_boot.txt" 2>/dev/null ) &
CAT=$!
nohup "$GCC/arm-none-eabi-gdb" -q -batch \
   -ex "set pagination off" -ex "set confirm off" \
   -ex "target extended-remote localhost:61234" \
   -ex "load" -ex "info registers pc sp" -ex "continue" "$ELF" > "$LOG/gdb.log" 2>&1 &
wait $CAT
echo "-- gdb --"; grep -E "Loading|Start address|Transfer rate|^pc|^sp|rror|SIG" "$LOG/gdb.log" | head -6
echo "-- UART 부팅 배너 --"; tr -cd "\11\12\15\40-\176" < "$LOG/uart_boot.txt" | head -c 700; echo
if ! grep -qE "Start address|Transfer rate" "$LOG/gdb.log"; then echo "!! ELF 적재 실패 — 중단"; sed -n 1,12p "$LOG/gdb.log"; exit 4; fi

echo "== 4) stedgeai validate --mode target (3 샘플)"
cd "$LOG" && echo n | timeout 1500 "$S" validate --model "$ONNX" --type onnx --target stm32n6 \
   --st-neural-art "$PROF" --mode target --desc serial:/dev/ttyACM0:921600 -b 3 2>&1 | tee validate.log \
   | grep -iE "device|runtime|firmware|inference time|duration|ms|cycles|macc|l2r|rmse|mean|acc|error|not supported|HW|timeout|epoch" \
   | grep -vE "PASS:" | head -40
echo "== 로그: $LOG"
