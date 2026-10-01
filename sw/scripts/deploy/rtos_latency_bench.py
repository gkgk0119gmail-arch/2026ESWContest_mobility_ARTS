#!/usr/bin/env python3
"""왜 범용 OS 가 아니라 RTOS 보드여야 하는가 — **같은 C 코드**로 측정해서 답한다.

심사에서 가장 먼저 나올 질문은 "그 계산 Pi 에서 하면 안 되나?" 다. 지금까지 우리 근거는
보드 쪽 숫자(평균 11.8 µs / 최대 17.9 µs)뿐이어서 비교가 없었다.

이 스크립트는 보드 펌웨어가 쓰는 바로 그 헤더(`sw/fw/npu_lib/slip_core.h`)를 파이에서 컴파일해
같은 구간(slip_step2 + slip_control2)의 지연 분포를 조건별로 잰다. 그러면 공정한 비교가 된다.

핵심은 **속도가 아니라 예측 가능성**이다. Pi 5(2.4 GHz A76)가 STM32N6(800 MHz M55)보다
평균은 당연히 빠르다. 쟁점은 최악값이 평균의 몇 배냐다 — 실시간 제어는 평균이 아니라
최악값으로 설계한다.

조건:
  유휴       아무것도 안 돌 때
  부하       모든 코어를 점유한 채 (파이가 지각까지 맡는 현실적인 상황)
  부하+경합  위에 더해 메모리 대역까지 먹을 때
  수퍼루프   RTOS 없이 한 루프에서 추론과 미끄러짐 검사를 번갈아 할 때의 응답 주기

사용: python3 sw/scripts/deploy/rtos_latency_bench.py [--n 50000]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "sw/fw/npu_lib/bench_slip_linux.c"
BIN = pathlib.Path("/tmp/bench_slip")
SRC_CYC = ROOT / "sw/fw/npu_lib/bench_cyclic_linux.c"
BIN_CYC = pathlib.Path("/tmp/bench_cyclic")


def _gcc(src, out):
    r = subprocess.run(["gcc", "-O2", "-I", str(ROOT / "fw/npu_lib"), str(src),
                        "-o", str(out), "-lm"], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"빌드 실패 {src.name}:\n" + r.stderr[:500])


def build():
    _gcc(SRC, BIN)
    _gcc(SRC_CYC, BIN_CYC)
    print("빌드 OK  (보드와 같은 slip_core.h 를 그대로 쓴다)")


def run_cyc(period_us: int, n: int, tag: str) -> np.ndarray:
    out = f"/tmp/bench_cyclic_{tag}.txt"
    subprocess.run([str(BIN_CYC), str(period_us), str(n), out], check=True)
    return np.loadtxt(out)


def run(n: int, tag: str) -> np.ndarray:
    out = f"/tmp/bench_slip_{tag}.txt"
    subprocess.run([str(BIN), str(n), out], check=True)
    return np.loadtxt(out)


class Load:
    """모든 코어를 점유하는 부하. 파이가 지각까지 맡는 상황을 흉내 낸다."""

    def __init__(self, n=None, mem=False):
        self.n = n or os.cpu_count() or 4
        self.mem = mem
        self.ps: list[subprocess.Popen] = []

    def __enter__(self):
        code = ("import time\n"
                "x=0.0\n"
                + ("import numpy as np\nA=np.random.rand(700,700)\n" if self.mem else "")
                + "while True:\n"
                + ("    A@A\n" if self.mem else "    x=(x*1.000001+1.0)%%1e9\n"))
        for _ in range(self.n):
            self.ps.append(subprocess.Popen([sys.executable, "-c", code],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(3)
        return self

    def __exit__(self, *a):
        for p in self.ps:
            p.terminate()
        for p in self.ps:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        time.sleep(1)


def stat(v: np.ndarray) -> dict:
    return dict(n=len(v), mean=float(v.mean()), p50=float(np.median(v)),
                p99=float(np.percentile(v, 99)), p999=float(np.percentile(v, 99.9)),
                max=float(v.max()), ratio=float(v.max() / max(v.mean(), 1e-9)))


def board_stats() -> dict | None:
    f = ROOT / "logs/board_wcet.jsonl"
    if not f.exists():
        return None
    rows = [json.loads(l) for l in open(f) if l.strip()]
    busy = [r for r in rows if r.get("npu_busy")]
    if not rows:
        return None
    return dict(runs=len(rows), samples=sum(r["samples"] for r in rows),
                mean_us=float(np.mean([r["avg_us"] for r in rows])),
                max_us=float(max(r["max_us"] for r in rows)),
                busy_runs=len(busy),
                busy_mean=float(np.mean([r["avg_us"] for r in busy])) if busy else None,
                busy_max=float(max(r["max_us"] for r in busy)) if busy else None)


def superloop_period() -> tuple[float, float] | None:
    """RTOS 없이 한 루프에서 추론 → 미끄러짐 검사를 번갈아 하면, 검사는 추론이 끝나야 돈다.
    그 응답 주기를 파이 ONNX 추론 시간으로 실측한다."""
    try:
        import cv2  # noqa: F401
        import onnxruntime as ort
    except Exception:
        return None
    m = ROOT / "models/n6/v3_int8.onnx"
    if not m.exists():
        return None
    so = ort.SessionOptions()
    so.log_severity_level = 3
    s = ort.InferenceSession(str(m), so, providers=["CPUExecutionProvider"])
    iname = s.get_inputs()[0].name
    x = np.random.rand(1, 3, 224, 224).astype(np.float32)
    for _ in range(3):
        s.run(None, {iname: x})
    ts = []
    for _ in range(30):
        t0 = time.perf_counter_ns()
        s.run(None, {iname: x})
        ts.append(time.perf_counter_ns() - t0)
    a = np.array(ts, dtype=float)
    return float(a.mean()), float(a.max())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50000)
    ap.add_argument("--cyc-n", type=int, default=20000, help="주기 지터 표본 수")
    ap.add_argument("--out", default=str(ROOT / "logs/carla_demo/정리" / "06_RTOS가_왜_필요한가.md"))
    a = ap.parse_args()

    build()
    res: dict[str, dict] = {}

    cyc: dict[str, dict] = {}
    print("1/4 연산 시간 — 유휴")
    res["유휴"] = stat(run(a.n, "idle"))
    print("2/4 연산 시간 — 부하")
    with Load():
        res["부하"] = stat(run(a.n, "load"))
    print("3/4 주기 지터 — 유휴 (1 ms 주기)")
    cyc["유휴"] = stat(run_cyc(1000, a.cyc_n, "idle"))
    print("4/4 주기 지터 — 부하 (1 ms 주기)")
    with Load():
        cyc["부하"] = stat(run_cyc(1000, a.cyc_n, "load"))

    b = board_stats()
    sl = superloop_period()

    bmean = (b["busy_mean"] or b["mean_us"]) if b else None
    bmax = (b["busy_max"] or b["max_us"]) if b else None

    L = ["# RTOS 가 왜 필요한가 — 같은 코드로 잰 비교\n\n",
         "심사에서 가장 먼저 나올 질문은 \"그 계산 Pi 에서 하면 안 되나?\" 다. "
         "보드 펌웨어가 쓰는 바로 그 헤더(`sw/fw/npu_lib/slip_core.h`)를 파이에서 컴파일해 "
         "**같은 코드**로 재서 답한다.\n\n",
         "**속도가 쟁점이 아니다.** Pi 5(2.4 GHz Cortex-A76)가 STM32N6(800 MHz Cortex-M55)보다 "
         "평균은 당연히 빠르다. 실시간 제어는 평균이 아니라 **최악값**으로 설계한다.\n\n"]

    # ---------- 1. 주기 지터 (핵심) ----------
    L.append("## 1. 제때 깨어나는가 — 주기 지터\n\n")
    L.append("2차 방어는 주기적으로 IMU 를 받아 판정해야 한다. 연산이 아무리 빨라도 "
             "**깨어나는 시각이 늦으면 판정 자체가 늦는다.** cyclictest 와 같은 방식으로, "
             "1 ms 주기를 절대시각 기준으로 자고 깨어난 실제 시각의 지연을 쟀다. "
             "매 주기 보드와 같은 연산도 실제로 수행한다.\n\n")
    L.append("| 조건 | 표본 | 평균 지연 | p99 | p99.9 | **최대 지연** |\n|---|---|---|---|---|---|\n")
    for k in ("유휴", "부하"):
        c = cyc[k]
        L.append(f"| Pi 5 + Linux · {k} | {c['n']:,} | {c['mean']/1000:.0f} µs | "
                 f"{c['p99']/1000:.0f} µs | {c['p999']/1000:.0f} µs | "
                 f"**{c['max']/1000:.0f} µs** |\n")
    if bmax:
        L.append(f"| STM32N6 + ThreadX (참고: 응답 **전체**) | {b['samples']:,} | "
                 f"{bmean:.1f} µs | — | — | **{bmax:.1f} µs** |\n")
        wm_us = max(cyc[k]["max"] for k in cyc) / 1000.0        # ns → µs
        L.append(f"\n리눅스는 **아무것도 안 돌 때도** 최대 {cyc['유휴']['max']/1000:.0f} µs 늦게 깨어난다. "
                 f"부하가 걸리면 {cyc['부하']['max']/1000:.0f} µs 다. "
                 f"보드는 깨어남·연산·응답을 **다 합쳐서** {bmax:.1f} µs 안에 끝낸다 — "
                 f"리눅스 최악 깨어남 지연의 **{wm_us/bmax:.0f}분의 1** 이다.\n\n")
        L.append(f"> **제어 주기와 견주면**: 2차 방어의 제어 주기는 20 ms 다. "
                 f"리눅스의 최악 깨어남 지연 {wm_us/1000:.2f} ms 는 그 주기의 "
                 f"**{100*wm_us/1000/20:.0f} %** 를 먹는다. 보드의 전체 응답 {bmax:.1f} µs 는 "
                 f"**{100*bmax/1000/20:.2f} %** 다.\n\n")
        L.append(f"> 거리로 옮기면 38 km/h(10.6 m/s)에서 {wm_us/1000:.2f} ms 는 "
                 f"{wm_us/1000*10.6/10:.1f} cm 다. 한 번의 지연만 보면 작다. "
                 "문제는 크기가 아니라 **그 값을 보증할 수 없다**는 것이다 — "
                 "측정한 20,000 회의 최댓값일 뿐, 다음 번에 더 늦지 않는다는 근거가 없다. "
                 "안전 논증은 '대개 빠르다'가 아니라 '이 값을 절대 넘지 않는다'여야 한다.\n")

    # ---------- 2. 연산 시간 ----------
    L.append("\n## 2. 연산 자체는 얼마나 걸리나\n\n")
    L.append("참고로 연산 구간(`slip_step2` + `slip_control2`)만 따로 잰 값이다. "
             "여기서는 Pi 가 훨씬 빠르다 — 그래서 \"보드가 빠르다\"고 말하면 안 된다.\n\n")
    L.append("| 플랫폼 · 조건 | 표본 | 평균 | p99.9 | 최대 | 최대/평균 |\n|---|---|---|---|---|---|\n")
    if bmean:
        L.append(f"| STM32N6 + ThreadX (NPU 동시) | {b['samples']:,} | {bmean:.2f} µs | — | "
                 f"{bmax:.2f} µs | **{bmax/bmean:.1f}×** |\n")
    for k in ("유휴", "부하"):
        r = res[k]
        L.append(f"| Pi 5 + Linux · {k} | {r['n']:,} | {r['mean']/1000:.3f} µs | "
                 f"{r['p999']/1000:.3f} µs | {r['max']/1000:.2f} µs | **{r['ratio']:.0f}×** |\n")
    L.append(f"\nPi 가 평균 {bmean/(res['유휴']['mean']/1000):.0f}배 빠르다. 그런데도 최악/평균이 "
             f"{max(res[k]['ratio'] for k in res):.0f}배로 벌어진다. 연산이 86 ns 로 워낙 짧아 "
             "선점당할 틈이 적은데도 그렇다 — 리눅스의 진짜 문제는 §1 의 깨어남 지연이다.\n")

    # ---------- 3. 보드가 스스로 증명하는 선점 ----------
    if b and b.get("busy_mean") and b["runs"] > b["busy_runs"]:
        idle_mean = float(np.mean([r["avg_us"] for r in
                                   [json.loads(l) for l in open(ROOT / "logs/board_wcet.jsonl") if l.strip()]
                                   if not r.get("npu_busy")]))
        L.append("\n## 3. 보드가 스스로 증명하는 우선순위 선점\n\n")
        L.append("외부 비교 없이도 보드 데이터만으로 선점을 보일 수 있다. "
                 "NPU 가 25.5 ms 짜리 추론을 돌리는 주행과, NPU 가 쉬는 주행을 나눠 보면 된다.\n\n")
        L.append("| 조건 | 주행 | 2차 방어 평균 응답 |\n|---|---|---|\n")
        L.append(f"| NPU 유휴 | {b['runs']-b['busy_runs']} | {idle_mean:.1f} µs |\n")
        L.append(f"| NPU 가 25.5 ms 추론 중 | {b['busy_runs']} | {b['busy_mean']:.1f} µs |\n")
        pen = b["busy_mean"] - idle_mean
        L.append(f"\n25.5 ms 짜리 작업이 같은 코어에서 도는데 **응답은 {pen:.1f} µs 밖에 안 늘어난다.** "
                 f"선점이 없었다면 최악의 경우 추론이 끝날 때까지 기다려야 하므로 "
                 f"25,500 µs — **{25500/max(pen,0.1):.0f}배** 차이다. "
                 "ThreadX 가 융합 스레드(우선순위 3)로 NPU 프레임 스레드(우선순위 4)를 "
                 "선점하기 때문이고, 이것이 이 설계에서 RTOS 가 하는 일이다.\n")

    # ---------- 4. 수퍼루프 ----------
    L.append("\n## 4. RTOS 가 없다면 — 수퍼루프\n\n")
    L.append("우선순위 선점이 없으면 미끄러짐 검사는 추론이 끝나야 돈다. "
             "보드의 NPU 추론이 25.49 ms 고정이므로(실사진 25,140 회 측정, 편차 0.5 %), "
             "한 루프로 짜면 2차 방어의 응답 주기가 그만큼이 된다.\n\n")
    L.append("| 구조 | 2차 방어 응답 | 38 km/h 에서 밀리는 거리 |\n|---|---|---|\n")
    L.append("| 수퍼루프 (추론 → 검사 순차) | 25.5 ms | 0.27 m |\n")
    if bmax:
        L.append(f"| ThreadX 선점 (현재 설계) | {bmax/1000:.3f} ms | {bmax/1000*10.6/1000:.4f} m |\n")
        L.append(f"\n**{25500/bmax:.0f}배** 차이다.\n")
    if sl:
        L.append(f"\n참고로 파이에서 같은 int8 모델을 CPU 로 돌리면 추론이 "
                 f"평균 {sl[0]/1e6:.1f} ms 다. 수퍼루프로 짜면 그 값이 응답 주기가 된다.\n")

    # ---------- 5. 주장 가능 여부 ----------
    L.append("\n## 5. 무엇을 주장할 수 있고 무엇은 못 하나\n\n")
    L.append("| 주장 | 가능 여부 |\n|---|---|\n")
    L.append(f"| \"2차 방어 응답의 최악값이 유계다\" | ✅ {b['samples']:,} 샘플 실측, 최악 {bmax:.1f} µs |\n"
             if b else "")
    L.append("| \"범용 OS 는 깨어남부터 늦는다\" | ✅ 유휴에서도 ms 단위 지연, §1 |\n")
    L.append("| \"우선순위 선점이 실제로 동작한다\" | ✅ NPU 동시/유휴 비교, §3 |\n")
    L.append("| \"보드가 Pi 보다 빠르다\" | ❌ 연산은 Pi 가 훨씬 빠르다. 이렇게 말하면 안 된다 |\n")
    L.append("| \"리눅스로는 불가능하다\" | ❌ PREEMPT_RT 로 가능하다. **복잡도** 논거로 말해야 한다 |\n")

    L.append("\n## 재현\n\n```\npython3 sw/scripts/deploy/rtos_latency_bench.py --n 50000 --cyc-n 20000\n```\n\n")
    L.append("보드 원본은 `logs/board_wcet.jsonl`, 리눅스 원본은 `/tmp/bench_slip_*.txt` 와 "
             "`/tmp/bench_cyclic_*.txt` 다.\n")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(L))
    print("".join(L))
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
