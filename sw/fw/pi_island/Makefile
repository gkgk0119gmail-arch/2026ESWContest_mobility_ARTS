# 라즈베리파이 격리 코어 안전 섬 — 보드와 같은 C 코어 (SLIP_MU_EST + SLIP_WDT, 제어표 끔)
CC ?= gcc
CFLAGS = -O2 -Wall -Wextra -Werror -std=gnu11 -I../npu_lib -DSLIP_MU_EST=1 -DSLIP_WDT=1

safety_island: safety_island.c ../npu_lib/slip_core.h ../npu_lib/slip_core_shim.c
	$(CC) $(CFLAGS) -o $@ safety_island.c -lm

clean:
	rm -f safety_island
.PHONY: clean
