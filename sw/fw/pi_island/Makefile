# 라즈베리파이 격리 코어 (안전 전용 RTOS 대체) — 보드와 같은 C 코어 (SLIP_MU_EST + SLIP_WDT + SLIP_ENVELOPE, 제어표 끔)
CC ?= gcc
CFLAGS = -O2 -Wall -Wextra -Werror -std=gnu11 -I../npu_lib -DSLIP_MU_EST=1 -DSLIP_WDT=1 -DSLIP_ENVELOPE=1

safety_island: safety_island.c ../npu_lib/slip_core.h ../npu_lib/slip_core_shim.c ../npu_lib/slip_envelope.h ../npu_lib/slip_env_table.h
	$(CC) $(CFLAGS) -o $@ safety_island.c -lm

clean:
	rm -f safety_island
.PHONY: clean
