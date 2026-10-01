#!/usr/bin/env python3
"""npu_infer.c: (1) BSP 초기화 전에 XSPI2 커널 클럭을 IC3=PLL1/N≈200MHz로 설정, (2) 상세 에러코드/클럭 출력.
BSP(stm32n6570_discovery_xspi.c)는 'XSPI clock = 200MHz'를 가정하고 MspInit에서 GPIO만 잡는다 — 클럭 트리는
앱 몫이다. hello_world는 system_clock_config.c가 하고, 우리 CubeMX 설정에는 XSPI2 지정이 없어 BSP 초기화가 실패했다."""
import sys, re
p = sys.argv[1]
s = open(p).read()

old_start = s.index("static int external_flash_init(void)")
old_end = s.index("static void npu_hw_config(void)")
new_block = r'''static int external_flash_init(void)
{
  /* 우리 FSBL 시스템 초기화가 XSPI2를 리셋하므로 가중치(0x71000000)를 읽으려면 메모리맵을 다시 켠다.
     BSP는 XSPI2 커널 클럭 200MHz를 가정한다 — 우리 클럭 설정(CubeMX)에는 지정이 없어 여기서 잡는다:
     IC3 = PLL1 / N, N = round(PLL1 / 200MHz). PLL1은 CPU 클럭(IC1) 소스라 켜져 있다. */
  uint32_t pll1 = HAL_RCC_GetPLL1CLKFreq();
  uint32_t div = (pll1 + 100000000u) / 200000000u; if (div < 1) div = 1; if (div > 256) div = 256;
  RCC_PeriphCLKInitTypeDef pc = {0};
  pc.PeriphClockSelection = RCC_PERIPHCLK_XSPI2;
  pc.Xspi2ClockSelection  = RCC_XSPI2CLKSOURCE_IC3;
  pc.ICSelection[RCC_IC3].ClockSelection = RCC_ICCLKSOURCE_PLL1;
  pc.ICSelection[RCC_IC3].ClockDivider   = div;
  if (HAL_RCCEx_PeriphCLKConfig(&pc) != HAL_OK) return -50;
  printf("NPU: PLL1 %lu Hz -> IC3 /%lu -> XSPI2 %lu Hz\n", (unsigned long)pll1, (unsigned long)div,
         (unsigned long)HAL_RCCEx_GetPeriphCLKFreq(RCC_PERIPHCLK_XSPI2));

  BSP_XSPI_NOR_Init_t f;
  f.InterfaceMode = MX66UW1G45G_OPI_MODE;
  f.TransferRate  = MX66UW1G45G_DTR_TRANSFER;
  int32_t b = BSP_XSPI_NOR_Init(0, &f);
  if (b != BSP_ERROR_NONE) return -100 + b;                 /* -104 periph(HAL_XSPI_Init) / -105 component(flash ID·config) */
  b = BSP_XSPI_NOR_EnableMemoryMappedMode(0);
  if (b != BSP_ERROR_NONE) return -200 + b;
  /* 메모리맵이 실제로 동작하는지: 가중치 블롭 첫 워드 읽기 (전부 0xFF/0x00이면 의심) */
  volatile uint32_t *w = (volatile uint32_t *)0x71000000u;
  printf("NPU: weights @0x71000000 = %08lx %08lx\n", (unsigned long)w[0], (unsigned long)w[1]);
  return 0;
}

'''
s = s[:old_start] + new_block + s[old_end:]
if "#include <stdio.h>" not in s:
    s = s.replace('#include <string.h>', '#include <string.h>\n#include <stdio.h>', 1)
# 이전 실패 패치 잔재 제거 (있다면)
s = re.sub(r'\n  printf\("NPU: xspi2 kernel clk[^\n]*\n[^\n]*\n', '\n', s)
open(p, "w").write(s)
print("glue patched: XSPI2 kernel clock + detailed error codes")
