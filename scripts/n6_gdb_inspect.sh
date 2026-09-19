#!/usr/bin/env bash
# 실행 중(또는 멈춘) 타깃에 붙어 PC/LR/폴트 레지스터를 읽는다. gdbserver(61234)는 떠 있어야 한다.
# 별도 파일인 이유: 같은 셸 명령줄에 'arm-none-eabi-gdb -q' 문자열이 있으면 pkill -f 가 자기 자신을 죽인다.
ELF=${1:-$HOME/icepredict/fw/n6root/Projects/STM32N6570-DK/Applications/NetXDuo/icepredict_n6/STM32CubeIDE/FSBL/Debug/Nx_WebServer_FSBL.elf}
GCC=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
pkill -f "arm-none-eabi-gdb -q" 2>/dev/null; sleep 1
timeout 45 "$GCC/arm-none-eabi-gdb" -q -batch -ex "set pagination off" -ex "target extended-remote localhost:61234" \
  -ex "interrupt" -ex "info registers pc lr sp xpsr" -ex "bt 10" -ex "x/3i \$pc" \
  -ex "printf \"CFSR=%08x HFSR=%08x BFAR=%08x MMFAR=%08x\\n\", *(unsigned*)0xE000ED28, *(unsigned*)0xE000ED2C, *(unsigned*)0xE000ED38, *(unsigned*)0xE000ED34" \
  -ex "info threads" -ex "detach" "$ELF" 2>&1 | grep -vE "^\s*$|warning:" | cut -c1-150 | head -30
