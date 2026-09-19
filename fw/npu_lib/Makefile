# libicepredict_npu.a — ll_aton 런타임 + 생성된 network.c + BSP XSPI NOR + 글루.
# CubeIDE가 생성한 우리 FSBL makefile을 건드리지 않고 objects.mk의 LIBS 한 줄로 끼우기 위한 별도 빌드.
GCC   ?= /opt/st/stm32cubeide_2.1.1/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.linux64_1.0.100.202602081740/tools/bin
ST    ?= $(HOME)/icepredict/tools/stedgeai/3.0
CUBE  ?= $(HOME)/STM32CubeN6
OUR   ?= $(HOME)/icepredict/fw/n6root/Projects/STM32N6570-DK/Applications/NetXDuo/icepredict_n6/FSBL
NET   ?= $(HOME)/icepredict/models/n6_gen_eq3_v3/out
OSAL  ?= LL_ATON_OSAL_THREADX
ATON  = $(ST)/Middlewares/ST/AI/Npu/ll_aton
DEV   = $(ST)/Middlewares/ST/AI/Npu/Devices/STM32N6xx
BSP   = $(ST)/Projects/STM32N6570-DK/Applications/Drivers/BSP
CC    = $(GCC)/arm-none-eabi-gcc
AR    = $(GCC)/arm-none-eabi-ar
SRCS  = $(addprefix $(ATON)/, ll_aton.c ll_aton_cipher.c ll_aton_dbgtrc.c ll_aton_debug.c ll_aton_lib.c ll_aton_lib_sw_operators.c \
          ll_aton_rt_main.c ll_aton_runtime.c ll_aton_util.c ll_sw_float.c ll_sw_integer.c ecloader.c) \
        $(DEV)/npu_cache.c $(DEV)/mcu_cache.c \
        $(BSP)/STM32N6570-DK/stm32n6570_discovery_xspi.c $(BSP)/Components/mx66uw1g45g/mx66uw1g45g.c \
        $(NET)/network.c npu_infer.c $(EXTRA_SRCS)
ifeq ($(OSAL),LL_ATON_OSAL_THREADX)
SRCS += $(ATON)/ll_aton_osal_threadx.c
endif
OBJS  = $(addprefix build/, $(notdir $(SRCS:.c=.o)))
VPATH = $(sort $(dir $(SRCS)))
DEFS  = -DSTM32N657xx -DUSE_HAL_DRIVER -DUSE_FULL_LL_DRIVER -DTX_INCLUDE_USER_DEFINE_FILE -DTX_SINGLE_MODE_SECURE=1 \
        -DLL_ATON_PLATFORM=LL_ATON_PLAT_STM32N6 -DLL_ATON_OSAL=$(OSAL) -DLL_ATON_RT_MODE=LL_ATON_RT_ASYNC \
        -DLL_ATON_SW_FALLBACK -DLL_ATON_DBG_BUFFER_INFO_EXCLUDED=1 -DUSE_NPU_CACHE
INCS  = -I. -Iinc -I$(NET) -I$(ATON) -I$(DEV) -I$(ST)/Middlewares/ST/AI/Inc \
        -I$(OUR)/Core/Inc -I$(OUR)/AZURE_RTOS/App \
        -I$(CUBE)/Drivers/STM32N6xx_HAL_Driver/Inc -I$(CUBE)/Drivers/STM32N6xx_HAL_Driver/Inc/Legacy \
        -I$(CUBE)/Drivers/CMSIS/Device/ST/STM32N6xx/Include -I$(CUBE)/Drivers/CMSIS/Include \
        -I$(CUBE)/Middlewares/ST/threadx/common/inc -I$(CUBE)/Middlewares/ST/threadx/ports/cortex_m55/gnu/inc \
        -I$(BSP)/STM32N6570-DK -I$(BSP)/Components/mx66uw1g45g -I$(BSP)/Components/Common
CFLAGS = -include stm32n6xx_hal.h -mcpu=cortex-m55 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard -mcmse -std=gnu11 -Os -g -ffunction-sections -fdata-sections \
         -Wall -Wno-unused-function -Wno-unused-variable $(DEFS) $(INCS)
all: libicepredict_npu.a
build/%.o: %.c | build
	$(CC) $(CFLAGS) -c $< -o $@
build:
	mkdir -p build
libicepredict_npu.a: $(OBJS)
	$(AR) rcs $@ $^
	$(GCC)/arm-none-eabi-size -t $@ | tail -1
clean:
	rm -rf build libicepredict_npu.a
