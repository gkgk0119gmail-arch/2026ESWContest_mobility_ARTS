#!/usr/bin/env bash
# 데스크탑에서 실행: 펌웨어만 다시 빌드·서명·굽기 (모델 블롭은 그대로). 개발 모드(BOOT1 오른쪽) + SWD 필요.
# spec_deploy_auto.sh 4)~5) 단계의 펌웨어 부분만 떼어낸 것. 패치 스크립트는 멱등이라 여러 번 돌려도 된다.
set -u
# 툴체인 경로 (setsid/nohup 비대화형 셸에는 PATH가 없다 → arm-none-eabi-gcc not found)
GCC=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
export PATH=$GCC:$PATH
LOGS=$HOME/icepredict/logs; STATUS=$LOGS/fw_redeploy_status.txt; : > "$STATUS"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$STATUS"; }
die() { say "!! 중단: $*"; exit 1; }
P=$HOME/icepredict/venv/bin/python
LIB=$HOME/icepredict/fw/npu_lib
OUR=$HOME/icepredict/fw/n6root/Projects/STM32N6570-DK/Applications/NetXDuo/icepredict_n6
CP=/opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.400.202601091506/tools/bin
APP="$OUR/FSBL/NetXDuo/App/app_netxduo.c"

say "=== 1) 펌웨어 패치 (2차 방어 IMU slip) ==="
cp -n "$APP" "$APP.pre_slip.bak" 2>/dev/null
[ -f "$APP.pre_slip.bak" ] && cp "$APP.pre_slip.bak" "$APP"   # 이전 slip 블록을 지우고 새 버전으로 다시 패치
$P "$LIB/patch_fw_slip.py" "$APP" || die "slip 패치"
grep -q "IP_IMU_MAGIC" "$APP" || die "패치 확인 실패"

say "=== 2) 빌드 ==="
cd "$OUR/STM32CubeIDE/FSBL/Debug" && rm -f Nx_WebServer_FSBL.elf Application/User/NetXDuo/App/app_netxduo.o
make all -j8 > "$LOGS/slip_fw.log" 2>&1 || { grep -m5 -E " error|overflowed" "$LOGS/slip_fw.log" | tee -a "$STATUS"; die "펌웨어 빌드"; }
grep -E "^\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+" "$LOGS/slip_fw.log" | tail -1 | tee -a "$STATUS"
[ "${BUILD_ONLY:-0}" = 1 ] && { say "=== 빌드만 (BUILD_ONLY=1) ==="; exit 0; }

say "=== 3) 보드 굽기 ==="
bash "$HOME/icepredict/code/sw/scripts/deploy/n6_swd_free.sh" > /tmp/swd.log 2>&1
grep -q "Device name" /tmp/swd.log || die "SWD 연결 불가 (BOOT1 오른쪽인지, 잔여 디버거 확인)"
"$CP/STM32_SigningTool_CLI" -bin Nx_WebServer_FSBL.bin -nk -of 0x80000000 -t fsbl \
   -o "$HOME/icepredict/fw/out/icepredict_n6_v3-trusted.bin" -hv 2.3 -align -s > "$LOGS/slip_sign.log" 2>&1 || die "서명"
"$CP/STM32_Programmer_CLI" -c port=SWD mode=UR -hardRst -el "$CP/ExternalLoader/MX66UW1G45G_STM32N6570-DK.stldr" \
   -d "$HOME/icepredict/fw/out/icepredict_n6_v3-trusted.bin" 0x70000000 -v >> "$LOGS/slip_sign.log" 2>&1 || die "펌웨어 굽기"
say "펌웨어 0x70000000 굽기 완료"
APID=1 bash "$HOME/icepredict/fw/n6_fw_load.sh" "" 16 > "$LOGS/slip_load.log" 2>&1
grep -E "NPU: (ready|init failed)|IpAddress|frames listening|fusion listening" "$LOGS/slip_load.log" | cut -c1-110 | tee -a "$STATUS"
say "=== 배포 완료 ==="
