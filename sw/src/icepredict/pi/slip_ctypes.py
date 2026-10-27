"""보드 펌웨어와 같은 C 코어(slip_core.h)를 ctypes 로 부른다 — CARLA 데모의 --slip-ctypes 경로.

라이브러리는 sw/fw/npu_lib/build/host/ 에 있다. 없거나 헤더보다 오래됐으면 gcc 로 바로 만든다.
variant: "" = 기본 플래그 (보드에 올라가 있는 것과 같다), "mu" = SLIP_MU_EST=1, "gs" = SLIP_MU_EST=1 + SLIP_GAIN_SCHED=1
"""
from __future__ import annotations

import ctypes
import pathlib
import subprocess

LIB_DIR = pathlib.Path(__file__).resolve().parents[3] / "fw" / "npu_lib"
FLAGS = {"": [], "mu": ["-DSLIP_MU_EST=1"], "gs": ["-DSLIP_MU_EST=1", "-DSLIP_GAIN_SCHED=1"],
         "gsw": ["-DSLIP_MU_EST=1", "-DSLIP_GAIN_SCHED=1", "-DSLIP_WDT=1"],
         "mw": ["-DSLIP_MU_EST=1", "-DSLIP_WDT=1"],
         "env": ["-DSLIP_MU_EST=1", "-DSLIP_WDT=1", "-DSLIP_ENVELOPE=1"]}   # + 마찰 인식 제동 시점          # 파이 격리 코어 안전 전용 RTOS와 같은 구성 (sw/fw/pi_island)
MODES = {0: "none", 1: "lane_keep", 2: "evade_left", 3: "evade_right", 4: "hard_stop", 5: "stopped"}
TRIGS = {0: None, 1: "lat_acc", 2: "yaw_rate", 3: "low_mu", 4: "combo"}
_F = ctypes.c_float


def build(variant: str = "", extra: list[str] | None = None) -> pathlib.Path:
    name = "libslipcore" + (f"_{variant}" if variant else "") + ".so"
    out = LIB_DIR / "build" / "host" / name
    deps = [LIB_DIR / "slip_core.h", LIB_DIR / "slip_core_shim.c"] + [LIB_DIR / n for n in ("slip_gain_lut.h", "slip_envelope.h", "slip_env_table.h")
                                                                      if (LIB_DIR / n).exists()]
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

    def set_gains(self, gains: dict | None):
        """제어표 대신 고정 파라미터 (variant 'gs' 에서만 효과). None 이면 표로 돌아간다."""
        from icepredict.pi.gain_lut import PARAM_NAMES
        g = (_F * 10)(*[float(gains[k]) for k in PARAM_NAMES]) if gains else (_F * 10)()
        self.L.sc_set_gains(self.buf, g, 1 if gains else 0)

    def set_host_fresh(self, fresh: bool):
        """SLIP_WDT 빌드: 이번 샘플에 호스트(파이) 패킷이 새로 왔는가."""
        self.L.sc_set_host_fresh(1 if fresh else 0)

    def degraded(self) -> bool:
        self.L.sc_degraded.restype = ctypes.c_int
        return bool(self.L.sc_degraded(self.buf))

    def gains(self):
        from icepredict.pi.gain_lut import PARAM_NAMES
        g = (_F * 10)(); self.L.sc_get_gains(self.buf, g)
        return dict(zip(PARAM_NAMES, list(g)))

    # ---- SLIP_ENVELOPE (variant 'env'): 마찰 인식 제동 시점 ----
    ENV_MODES = {0: "CRUISE", 1: "PROBE", 2: "CAUTION", 3: "PREBRAKE", 4: "BRAKE", 5: "HOLD"}

    def env_reset(self, mu0: float):
        self.L.sc_env_reset.argtypes = [_F]; self.L.sc_env_set_mu.argtypes = [_F]
        self.L.sc_env_step.argtypes = [_F] * 5 + [ctypes.POINTER(_F)]
        self._env = (_F * 9)(); self.L.sc_env_reset(mu0)

    def env_probe(self):
        self.L.sc_env_probe()

    def env_set_mu(self, mu: float):
        self.L.sc_env_set_mu(mu)

    ENV_BLK = {1: "gray", 2: "temp", 4: "speed", 8: "unsteady", 16: "brake", 32: "gap", 64: "cooldown", 128: "mode", 256: "wait"}

    def env_should_probe(self, dt, risk, temp_c, v, ax, ay, brake_cmd, gap, zone=False):
        """탭 판단 (C env_should_probe). → (계기: None / "camera" / "zone", 막은 조건 이름 목록)"""
        self.L.sc_env_should_probe.argtypes = [_F] * 8 + [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        b = ctypes.c_int(0); r = self.L.sc_env_should_probe(dt, risk, temp_c, v, ax, ay, brake_cmd, gap, 1 if zone else 0, ctypes.byref(b))
        return {0: None, 1: "camera", 2: "zone"}[r], [n for k, n in self.ENV_BLK.items() if b.value & k]

    def env_step(self, dt, v, ax, gap, v_lead):
        self.L.sc_env_step(dt, v, ax, gap, v_lead, self._env); o = self._env
        return dict(brake=o[0], thr_cap=o[1], mode=self.ENV_MODES[int(o[2])], mu=o[3], d_stop=o[4], d_need=o[5],
                    v_max=o[6], mu_probe=o[7], a_used=o[8])

    def board(self, ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v):
        for i, v in enumerate((ay, gz, speed, steer, ax, brake_cmd, lane_err, lat_off, gf, gl, gr, ice_off, front_rel_v)):
            self._in[i] = v
        self.L.sc_board(self.buf, self._in, self._out)
        o = self._out
        return dict(slip=int(o[0]), trig=int(o[1]), ay_g=o[2], yaw_err=o[3], emerg=int(o[4]), brake=o[5], steer=o[6],
                    mode=int(o[7]), grip=int(o[8]), mu_hat=o[9], mu_n=int(o[10]), a_stop=o[11])
