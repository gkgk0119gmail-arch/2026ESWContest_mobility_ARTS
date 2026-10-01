#!/usr/bin/env bash
# 우리 FSBL 펌웨어(icepredict_n6)를 개발 모드에서 GDB로 RAM(0x34180400)에 올려 실행하고 UART를 캡처한다.
# 플래시를 굽지 않으므로 반복이 빠르다. 가중치 블롭은 이미 0x71000000에 있어야 한다.
# 사용: APID=1 [INSPECT=1] n6_fw_load.sh [ELF] [UART초]
#   INSPECT=1: UART 창이 끝난 뒤 **같은 gdb 세션**이 타깃을 멈추고 PC/LR/폴트 레지스터/백트레이스를 찍는다.
#   (두 번째 gdb를 붙이면 ST-LINK gdbserver가 꼬여 'vMustReplyEmpty timeout'이 난다 — 그래서 한 세션 안에서 한다.)
# 주의: 이 파일을 별도 스크립트로 두는 이유 — 같은 셸 명령줄에 'arm-none-eabi-gdb -q' 문자열이 있으면
#   pkill -f 가 그 셸 자신을 죽인다 (ssh 세션이 exit 255로 끊기던 원인).
set -u
ELF=${1:-$HOME/icepredict/fw/n6root/Projects/STM32N6570-DK/Applications/NetXDuo/icepredict_n6/STM32CubeIDE/FSBL/Debug/Nx_WebServer_FSBL.elf}
SECS=${2:-25}; APID=${APID:-1}; INSPECT=${INSPECT:-0}
CP=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.400.202601091506/tools/bin
GS=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.stlink-gdb-server.linux64_2.2.400.202601091506/tools/bin/ST-LINK_gdbserver
GCC=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
LOG=$HOME/icepredict/logs/fwload; mkdir -p "$LOG"
fuser -k 61234/tcp 61235/tcp 2>/dev/null; pkill -f "ST-LINK_gdbserver" 2>/dev/null; pkill -f "arm-none-eabi-gdb -q" 2>/dev/null; sleep 1
"$GS" -p 61234 -d -s -k -m $APID --halt -e -l 1 -cp "$CP" > "$LOG/gdbserver.log" 2>&1 &
sleep 4
if grep -qiE "Error in initializing|Failed to halt|no device" "$LOG/gdbserver.log"; then echo "!! gdbserver 실패"; tail -3 "$LOG/gdbserver.log"; exit 3; fi
stty -F /dev/ttyACM0 115200 raw -echo          # 우리 펌웨어 UART 115200
( timeout $SECS cat /dev/ttyACM0 > "$LOG/uart.txt" 2>/dev/null ) & CAT=$!
GDBCMDS=( -ex "set pagination off" -ex "set confirm off" -ex "target extended-remote localhost:61234" -ex "load" -ex "info registers pc" )
if [ "$INSPECT" = 1 ]; then
  GDBCMDS+=( -ex "continue &" -ex "shell sleep $((SECS + 1))" -ex "interrupt" -ex "shell sleep 1"
             -ex "info registers pc lr sp xpsr" -ex "x/3i \$pc" -ex "bt 12"
             -ex "printf \"CFSR=%08x HFSR=%08x BFAR=%08x MMFAR=%08x\\n\", *(unsigned*)0xE000ED28, *(unsigned*)0xE000ED2C, *(unsigned*)0xE000ED38, *(unsigned*)0xE000ED34"
             -ex "detach" )
  "$GCC/arm-none-eabi-gdb" -q -batch "${GDBCMDS[@]}" "$ELF" > "$LOG/gdb.log" 2>&1
else
  GDBCMDS+=( -ex "continue" )
  nohup "$GCC/arm-none-eabi-gdb" -q -batch "${GDBCMDS[@]}" "$ELF" > "$LOG/gdb.log" 2>&1 &
fi
wait $CAT 2>/dev/null
echo "-- gdb --"; grep -E "Start address|Transfer rate|rror|SIG" "$LOG/gdb.log" | head -3
echo "-- UART (${SECS}s) --"; tr -cd "\11\12\15\40-\176" < "$LOG/uart.txt" | grep -vE "^\s*$" | head -40
if [ "$INSPECT" = 1 ]; then echo "-- 정지 시점 레지스터/백트레이스 --"; sed -n "/^pc /,\$p" "$LOG/gdb.log" | grep -vE "^\s*$|warning:" | cut -c1-140 | head -24; fi
