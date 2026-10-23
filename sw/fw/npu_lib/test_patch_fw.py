#!/usr/bin/env python3
"""patch_fw_slip.py 를 펌웨어와 같은 앵커를 가진 가짜 app_netxduo.c 에 적용하고, 들어간 정의 블록을 호스트 gcc 로 문법 검사한다.
보드용 실제 빌드(arm-none-eabi, STM32CubeIDE)는 툴체인이 있는 데스크탑에서만 된다 — 보드 복귀 후 fw_redeploy.sh 로 확인한다.
세 구성(고정 게인 / --mu-est / --gain-sched)을 다 본다."""
import pathlib, subprocess, sys, tempfile
HERE = pathlib.Path(__file__).resolve().parent
FAKE = """/* USER CODE BEGIN PD */
/* USER CODE END PD */
     28 bytes -> ip_infer_t  : 추론 확률 -> 융합 -> 판정 응답 (ip_verdict_t)
void loop(void) {
    if (len == sizeof(ip_infer_t) &&
}
"""
ok = True
for flags in ([], ["--mu-est"], ["--gain-sched"]):
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "app_netxduo.c"; f.write_text(FAKE)
        subprocess.run([sys.executable, str(HERE / "patch_fw_slip.py"), str(f), *flags], check=True, capture_output=True)
        s = f.read_text()
        blk = s[s.index("/* ===================== IcePredict 2차 방어"):s.index("static slipdet_t g_slip;") + len("static slipdet_t g_slip;")]
        test = pathlib.Path(td) / "blk.c"
        test.write_text("#include <stdint.h>\n" + blk + "\nint main(void){ slipdet_t d; slip_reset(&d,0.02f,3,2.f); float b,s2; uint8_t t; float a1,a2;"
                        " slip_step2(&d,0,0,10,0,0,0,&t,&a1,&a2); d.emerg=1; slip_control2(&d,10,0,0,0,999,999,999,0,0.5f,0,0,&b,&s2); return (int)b; }\n")
        r = subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Werror", str(test), "-o", str(pathlib.Path(td) / "a"), "-lm"],
                           capture_output=True, text=True)
        good = r.returncode == 0 and (("#define SLIP_GAIN_SCHED 1" in s) == ("--gain-sched" in flags)) and ("SLIP_GS_TABLE" in s) == ("--gain-sched" in flags or True)
        ok &= good
        print(f"[{'OK ' if good else 'FAIL'}] 패치 {' '.join(flags) or '(고정 게인)'}: 정의 블록 gcc -Werror 컴파일 {'성공' if r.returncode == 0 else '실패'}"
              + ("" if r.returncode == 0 else "\n" + r.stderr[:800]))
sys.exit(0 if ok else 1)
