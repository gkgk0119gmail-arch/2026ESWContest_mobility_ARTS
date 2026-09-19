#!/usr/bin/env bash
# 우리 FSBL 펌웨어(icepredict_n6)를 개발 모드에서 GDB로 RAM(0x34180400)에 올려 실행하고 UART를 캡처한다.
# 플래시를 굽지 않으므로 반복이 빠르다. 가중치 블롭은 이미 0x71000000에 있어야 한다.
# 사용: APID=1 n6_fw_load.sh [ELF] [UART초]
set -u
ELF=${1:-$HOME/icepredict/fw/n6root/Projects/STM32N6570-DK/Applications/NetXDuo/icepredict_n6/STM32CubeIDE/FSBL/Debug/Nx_WebServer_FSBL.elf}
SECS=${2:-25}; APID=${APID:-1}
CP=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.400.202601091506/tools/bin
GS=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.stlink-gdb-server.linux64_2.2.400.202601091506/tools/bin/ST-LINK_gdbserver
GCC=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
LOG=$HOME/icepredict/logs/fwload; mkdir -p "$LOG"
fuser -k 61234/tcp 61235/tcp 2>/dev/null; pkill -f "ST-LINK_gdbserve[r]" 2>/dev/null; pkill -f "arm-none-eabi-gdb -q" 2>/dev/null; sleep 1
"$GS" -p 61234 -d -s -k -m $APID --halt -e -l 1 -cp "$CP" > "$LOG/gdbserver.log" 2>&1 &
sleep 4
if grep -qiE "Error in initializing|Failed to halt|no device" "$LOG/gdbserver.log"; then echo "!! gdbserver 실패"; tail -3 "$LOG/gdbserver.log"; exit 3; fi
stty -F /dev/ttyACM0 115200 raw -echo          # 우리 펌웨어 UART 115200
( timeout $SECS cat /dev/ttyACM0 > "$LOG/uart.txt" 2>/dev/null ) & CAT=$!
nohup "$GCC/arm-none-eabi-gdb" -q -batch -ex "set pagination off" -ex "set confirm off" \
  -ex "target extended-remote localhost:61234" -ex "load" -ex "info registers pc sp" -ex "continue" "$ELF" > "$LOG/gdb.log" 2>&1 &
wait $CAT
echo "-- gdb --"; grep -E "Start address|Transfer rate|^pc|rror|SIG" "$LOG/gdb.log" | head -4
echo "-- UART (${SECS}s) --"; tr -cd "\11\12\15\40-\176" < "$LOG/uart.txt" | grep -vE "^\s*$" | head -40
