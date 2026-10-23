"""보드 펌웨어와 같은 C 코어(slip_core.h)를 ctypes 로 부른다 — CARLA 데모의 --slip-ctypes 경로.

라이브러리는 sw/fw/npu_lib/build/host/ 에 있다. 없거나 헤더보다 오래됐으면 gcc 로 바로 만든다.
variant: "" = 기본 플래그 (보드에 올라가 있는 것과 같다), "mu" = SLIP_MU_EST=1, "gs" = SLIP_MU_EST=1 + SLIP_GAIN_SCHED=1
"""
from __future__ import annotations

import ctypes
import pathlib
import subprocess

LIB_DIR = pathlib.Path(__file__).resolve().parents[3] / "fw" / "npu_lib"
FLAGS = {"": [], "mu": ["-DSLIP_MU_EST=1"], "gs": ["-DSLIP_MU_EST=1", "-DSLIP_GAIN_SCHED=1"]}
MODES = {0: "none", 1: "lane_keep", 2: "evade_left", 3: "evade_right", 4: "hard_stop", 5: "stopped"}
TRIGS = {0: None, 1: "lat_acc", 2: "yaw_rate", 3: "low_mu", 4: "combo"}
_F = ctypes.c_float


def build(variant: str = "", extra: list[str] | None = None) -> pathlib.Path:
    name = "libslipcore" + (f"_{variant}" if variant else "") + ".so"
    out = LIB_DIR / "build" / "host" / name
    deps = [LIB_DIR / "slip_core.h", LIB_DIR / "slip_core_shim.c"] + ([LIB_DIR / "slip_gain_lut.h"] if (LIB_DIR / "slip_gain_lut.h").exists() else [])
    if not out.exists() or any(d.stat().st_mtime > out.stat().st_mtime for d in deps) or extra:
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["gcc", "-O2", "-fPIC", "-shared", "-I", str(LIB_DIR), *FLAGS[variant], *(extra or []),
               str(LIB_DIR / "slip_core_shim.c"), "-o", str(out), "-lm"]
        subprocess.run(cmd, check=True)
    return out


class SlipCoreC:
    def __init__(self, dt=0.02, confirm=3, min_speed=2.0, variant: str = "", lib: str | None = None):
        self.L = ctypes.CDLL(str(lib or build(variant)))
        self.L.sc_sizeof.restype = ctypes.c_size_t
        self.L.sc_reset.argtypes = [ctypes.c_void_p, _F, ctypes.c_int, _F]
        self.L.sc_board.argtypes = [ctypes.c_void_p, ctypes.POINTER(_F), ctypes.POINTER(_F)]
        self.L.sc_step2.argtypes = [ctypes.c_void_p] + [_F] * 6 + [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(_F), ctypes.POINTER(_F)]
        self.L.sc_step2.restype = ctypes.c_int
        self.L.sc_control2.argtypes = [ctypes.c_void_p] + [_F] * 11 + [ctypes.POINTER(_F), ctypes.POINTER(_F)]
        self.flags = self.L.sc_flags()
        self.buf = ctypes.create_string_buffer(self.L.sc_sizeof())
        self._in = (_F * 13)(); self._out = (_F * 12)()
        self.reset(dt, confirm, min_speed)

    def reset(self, dt, confirm=3, min_speed=2.0):
        self.L.sc_reset(self.buf, dt, confirm, min_speed)

    def force_emerg(self):
        self.L.sc_force_emerg(self.buf)

    def board(self, ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v):
        for i, v in enumerate((ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v)):
            self._in[i] = v
        self.L.sc_board(self.buf, self._in, self._out)
        o = self._out
        return dict(slip=int(o[0]), trig=int(o[1]), ay_g=o[2], yaw_err=o[3], emerg=int(o[4]), brake=o[5], steer=o[6],
                    mode=int(o[7]), grip=int(o[8]), mu_hat=o[9], mu_n=int(o[10]), a_stop=o[11])
