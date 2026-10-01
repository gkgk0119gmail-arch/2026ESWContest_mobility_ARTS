#include "npu_infer.h"
#include <string.h>
#include <stdio.h>
#include "stm32n6xx_hal.h"
#include "stm32n6570_discovery_xspi.h"
#include "ll_aton_runtime.h"
#include "ll_aton_util.h"
#include "npu_cache.h"

LL_ATON_DECLARE_NAMED_NN_INSTANCE_AND_INTERFACE(network);

static int8_t  *s_in  = NULL;
static int8_t  *s_out = NULL;
static uint32_t s_in_bytes = 0, s_out_bytes = 0;
static int      s_ready = 0;

/* hello_world/misc_toolbox.c 에서 가져옴: RISAF 영역을 기본(전체 접근 허용)으로 */
static void set_risaf_default(RISAF_TypeDef *risaf)
{
  RISAF_BaseRegionConfig_t cfg = {0};
  cfg.Filtering = RISAF_FILTER_ENABLE;
  cfg.PrivWhitelist = RIF_CID_MASK;
  cfg.ReadWhitelist = RIF_CID_MASK;
  cfg.WriteWhitelist = RIF_CID_MASK;
  cfg.Secure = RIF_ATTRIBUTE_SEC;
  cfg.StartAddress = 0x0;
  cfg.EndAddress = 0xFFFFFFFF;
  HAL_RIF_RISAF_ConfigBaseRegion(risaf, RISAF_REGION_1, &cfg);
}

static int external_flash_init(void)
{
  /* 우리 FSBL 시스템 초기화가 XSPI2를 리셋하므로 가중치(0x71000000)를 읽으려면 메모리맵을 다시 켠다.
     BSP는 XSPI2 커널 클럭 200MHz를 가정한다 — 우리 클럭 설정(CubeMX)에는 지정이 없어 여기서 잡는다:
     IC3 = PLL1 / N, N = round(PLL1 / 200MHz). PLL1은 CPU 클럭(IC1) 소스라 켜져 있다. */
  uint32_t pll1 = HAL_RCCEx_GetPLL1CLKFreq();
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
  volatile uint32_t *w = (volatile uint32_t *)0x71000000u;   /* 메모리맵이 실제로 동작하는지: 블롭 첫 워드 */
  printf("NPU: weights @0x71000000 = %08lx %08lx\n", (unsigned long)w[0], (unsigned long)w[1]);
  return 0;
}

static void npu_hw_config(void)
{
  /* NPU 활성 풀(AXISRAM3~6)과 CACHEAXI SRAM은 부팅 기본이 셧다운/언클럭이다. NetXDuo 펌웨어는 쓸 일이
     없어 켠 적이 없고, 그 상태로 npuRAM에 쓰면 버스폴트가 난다 (hello_world misc_toolbox.c 이식). */
  printf("NPU: [1a] sram on\n");
  RCC->MEMENR |= RCC_MEMENR_AXISRAM3EN | RCC_MEMENR_AXISRAM4EN | RCC_MEMENR_AXISRAM5EN | RCC_MEMENR_AXISRAM6EN;
  RCC->MEMENR |= RCC_MEMENR_CACHEAXIRAMEN;
  __HAL_RCC_AXISRAM1_MEM_CLK_ENABLE(); __HAL_RCC_AXISRAM2_MEM_CLK_ENABLE();
  __HAL_RCC_FLEXRAM_MEM_CLK_ENABLE();
  __HAL_RCC_RAMCFG_CLK_ENABLE();
  RAMCFG_SRAM2_AXI->CR &= ~RAMCFG_CR_SRAMSD;
  RAMCFG_SRAM3_AXI->CR &= ~RAMCFG_CR_SRAMSD; RAMCFG_SRAM4_AXI->CR &= ~RAMCFG_CR_SRAMSD;
  RAMCFG_SRAM5_AXI->CR &= ~RAMCFG_CR_SRAMSD; RAMCFG_SRAM6_AXI->CR &= ~RAMCFG_CR_SRAMSD;
  MEMSYSCTL->MSCR |= MEMSYSCTL_MSCR_DCACTIVE_Msk | MEMSYSCTL_MSCR_ICACTIVE_Msk;
  /* npuRAM3 첫 워드 쓰기/읽기 — 여기서 죽으면 SRAM 전원 문제 */
  volatile uint32_t *t = (volatile uint32_t *)0x34200000u; *t = 0xA5A55A5Au;
  printf("NPU: [1a'] npuRAM3 rw %s\n", (*t == 0xA5A55A5Au) ? "OK" : "FAIL");

  printf("NPU: [1b] npu clk/reset\n");
  __HAL_RCC_NPU_CLK_ENABLE();
  __HAL_RCC_NPU_CLK_SLEEP_ENABLE();
  __HAL_RCC_NPU_FORCE_RESET();
  __HAL_RCC_NPU_RELEASE_RESET();
#ifdef __HAL_RCC_CACHEAXI_CLK_ENABLE
  __HAL_RCC_CACHEAXI_CLK_ENABLE();
#endif
  printf("NPU: [1c] cache\n");
  npu_cache_enable();
  printf("NPU: [1d] rif\n");
  RIMC_MasterConfig_t mc = {0};
  mc.MasterCID = RIF_CID_1;
  mc.SecPriv   = RIF_ATTRIBUTE_SEC | RIF_ATTRIBUTE_PRIV;
  HAL_RIF_RIMC_ConfigMasterAttributes(RIF_MASTER_INDEX_NPU, &mc);
  HAL_RIF_RISC_SetSlaveSecureAttributes(RIF_RISC_PERIPH_INDEX_NPU, RIF_ATTRIBUTE_PRIV | RIF_ATTRIBUTE_SEC);
  /* NPU 마스터가 SRAM/플래시에 접근할 수 있게 */
  set_risaf_default(RISAF2_S);  /* SRAM1_AXI */
  set_risaf_default(RISAF3_S);  /* SRAM2_AXI */
  set_risaf_default(RISAF4_S);  /* NPU MST0 */
  set_risaf_default(RISAF5_S);  /* NPU MST1 */
  set_risaf_default(RISAF6_S);  /* SRAM3..6 (npuRAM) */
  set_risaf_default(RISAF7_S);  /* FLEXMEM */
  set_risaf_default(RISAF8_S);  /* NPU cache */
  set_risaf_default(RISAF15_S); /* NPU cache cfg */
  set_risaf_default(RISAF12_S); /* OCTOSPI2 0x70000000 (가중치) */
  printf("NPU: [1e] risaf done\n");
}

/* NPU/npuRAM 클럭. 우리 CubeMX 설정은 IC6(NPU)=PLL1/4=300MHz, IC11(AXISRAM3~6)=PLL1/3=400MHz라 검증 펌웨어
   (1GHz/900MHz)보다 느리다. CPU 클럭(UART 보율·ETH 파생)은 건드리지 않고 PLL2=800MHz를 켜 IC6/IC11만 옮긴다 —
   hello_world의 no-overdrive 변형(NPU 800MHz)과 같은 값이라 VOS scale1에서 overdrive 없이 허용된다. */
static void npu_clocks_up(void)
{
  RCC_OscInitTypeDef osc = {0};
  osc.OscillatorType = RCC_OSCILLATORTYPE_NONE;
  osc.PLL1.PLLState = RCC_PLL_NONE; osc.PLL3.PLLState = RCC_PLL_NONE; osc.PLL4.PLLState = RCC_PLL_NONE;
  osc.PLL2.PLLState = RCC_PLL_ON; osc.PLL2.PLLSource = RCC_PLLSOURCE_HSI;
  /* NPU_MHZ: 800(기본) 또는 1000. 1000은 hello_world overdrive 설정과 같은 값(PLL2 M=8 N=125)이다.
     CPU/버스는 건드리지 않으므로 VOS는 그대로 두고, 동작하지 않으면 HAL이 실패를 돌려준다. */
#ifndef NPU_MHZ
#define NPU_MHZ 800
#endif
  osc.PLL2.PLLM = 8; osc.PLL2.PLLN = (NPU_MHZ) / 8; osc.PLL2.PLLP1 = 1; osc.PLL2.PLLP2 = 1; osc.PLL2.PLLFractional = 0;   /* 64/8*N */
  if (HAL_RCC_OscConfig(&osc) != HAL_OK) { printf("NPU: PLL2 config failed - keeping default clocks\n"); return; }
  RCC_ClkInitTypeDef clk = {0}; HAL_RCC_GetClockConfig(&clk);
  clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_CPUCLK;
  clk.IC6Selection.ClockSelection  = RCC_ICCLKSOURCE_PLL2; clk.IC6Selection.ClockDivider  = 1;
  clk.IC11Selection.ClockSelection = RCC_ICCLKSOURCE_PLL2; clk.IC11Selection.ClockDivider = 1;
  if (HAL_RCC_ClockConfig(&clk) != HAL_OK) { printf("NPU: IC6/IC11 config failed\n"); return; }
  /* IC6/IC11 주파수 조회 API가 HAL에 없다 — 설정값으로 출력 */
  printf("NPU: clocks IC6/IC11 = PLL2 %d MHz, cpu %lu Hz\n", (int)NPU_MHZ, (unsigned long)SystemCoreClock);
}

int npu_init(void)
{
  npu_clocks_up();
  int r = external_flash_init();
  if (r) return r;
  printf("NPU: [1] hw_config\n");
  npu_hw_config();
  printf("NPU: [2] rt init\n");
  LL_ATON_RT_RuntimeInit();
  printf("NPU: [3] net init\n");
  LL_ATON_RT_Init_Network(&NN_Instance_network);
  printf("NPU: [4] buffers\n");
  const LL_Buffer_InfoTypeDef *ib = LL_ATON_Input_Buffers_Info(&NN_Instance_network);
  const LL_Buffer_InfoTypeDef *ob = LL_ATON_Output_Buffers_Info(&NN_Instance_network);
  if (!ib || !ob) return -3;
  s_in  = (int8_t *)LL_Buffer_addr_start(&ib[0]);
  s_out = (int8_t *)LL_Buffer_addr_start(&ob[0]);
  s_in_bytes  = LL_Buffer_len(&ib[0]);
  s_out_bytes = LL_Buffer_len(&ob[0]);
  printf("NPU: in %p (%lu B) out %p (%lu B)\n", (void *)s_in, (unsigned long)s_in_bytes, (void *)s_out, (unsigned long)s_out_bytes);
  if (s_in_bytes != NPU_IN_BYTES || s_out_bytes != NPU_OUT_N) return -4;   /* 생성물과 헤더 상수 불일치 */
  s_ready = 1;
  return 0;
}

const int8_t *npu_input_buffer(void) { return s_in; }

int npu_infer_s8(const int8_t *in, int8_t out[NPU_OUT_N], uint32_t *infer_us)
{
  if (!s_ready) return -1;
  if (in != s_in) memcpy(s_in, in, NPU_IN_BYTES);
  /* 두 캐시를 모두 손봐야 한다.
     - CPU D-cache: memcpy 결과가 아직 캐시에만 있으면 NPU가 헌 값을 읽는다
     - **NPU 캐시**: 이걸 빼먹으면 NPU가 첫 추론 때 채운 라인을 계속 재사용해 입력이 바뀌어도
       출력이 고정된다. 실제로 보드가 서로 다른 이미지 3장에 자가진단과 똑같은 값을 돌려줬다. */
  SCB_CleanDCache_by_Addr((uint32_t *)((uintptr_t)s_in & ~31u), (int32_t)(NPU_IN_BYTES + 64));
  npu_cache_invalidate();
  uint32_t t0 = DWT->CYCCNT;
  LL_ATON_RT_Reset_Network(&NN_Instance_network);
  LL_ATON_RT_RetValues_t st;
  do {
    st = LL_ATON_RT_RunEpochBlock(&NN_Instance_network);
    if (st == LL_ATON_RT_WFE) LL_ATON_OSAL_WFE();
  } while (st != LL_ATON_RT_DONE);
  uint32_t t1 = DWT->CYCCNT;
  npu_cache_invalidate();                                                                          /* NPU가 쓴 출력을 메모리로 */
  SCB_InvalidateDCache_by_Addr((uint32_t *)((uintptr_t)s_out & ~31u), 64);                         /* CPU가 헌 캐시를 읽지 않게 */
  memcpy(out, s_out, NPU_OUT_N);
  if (infer_us) *infer_us = (uint32_t)(((uint64_t)(t1 - t0) * 1000000ULL) / SystemCoreClock);
  return 0;
}
