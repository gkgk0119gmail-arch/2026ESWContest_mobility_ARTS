#!/usr/bin/env bash
# SWD를 붙잡고 있는 잔여 디버거를 정리하고 연결을 확인한다 (데스크탑에서 실행).
# 별도 파일인 이유: pkill -f 패턴이 호출한 셸의 명령줄과도 매칭돼 ssh 세션을 죽인다 (여러 번 겪음).
set -u
CP=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.400.202601091506/tools/bin
fuser -k 61234/tcp 61235/tcp 2>/dev/null
for pat in "ST-LINK_gdbserve[r]" "arm-none-eabi-gd[b] -q"; do
  for pid in $(pgrep -f "$pat"); do kill "$pid" 2>/dev/null && echo "  kill $pid ($pat)"; done
done
sleep 2
"$CP/STM32_Programmer_CLI" -c port=SWD mode=UR 2>&1 | grep -iE "voltage|device name|DEV_CONNECT|No STM32" | head -2
