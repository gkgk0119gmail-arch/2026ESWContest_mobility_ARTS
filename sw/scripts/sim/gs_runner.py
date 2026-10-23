#!/usr/bin/env python3
"""여러 CARLA 서버에 데모 주행을 나눠 돌리는 실행기 (게인 스케줄링 4·6·8단계 공용, 5090 에서 setsid 로 띄운다).

작업 목록(JSON): [{"tag": "...", "args": ["--friction", "0.08", ...]}, ...]
  · 포트마다 작업자 하나. 작업 = carla_demo.py 한 번 (timeout). 끝나면 events_<tag>.json 이 있는지 본다.
  · 실패하면 그 서버를 재기동하고 한 번 더 시도한다. 두 번 실패하면 failed 로 기록하고 넘어간다.
  · CARLA 는 오래 돌면 응답을 멈춘다(8시간 32분에 13 GB, 60 s 타임아웃 — 2026-09-21). 서버 나이가 --max-age-min 을
    넘으면 작업 사이에 선제 재기동한다.
  · 이미 events_<tag>.json 이 있으면 건너뛴다 (중단 후 이어 돌리기).
  · 진행 상황을 --status 파일에 한 줄씩 덧붙인다.
사용: setsid nohup python gs_runner.py jobs.json --ports 2000,2010 --out ~/icepredict/logs/gain_sched/runs/X &
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import subprocess
import threading
import time

ap = argparse.ArgumentParser()
ap.add_argument("jobs")
ap.add_argument("--ports", default="2000,2010")
ap.add_argument("--out", required=True)
ap.add_argument("--carla", default=os.path.expanduser("~/CARLA/CarlaUE4.sh"))
ap.add_argument("--py", default=os.path.expanduser("~/miniconda3/envs/icepredict/bin/python"))
ap.add_argument("--code", default=os.path.expanduser("~/icepredict/code"))
ap.add_argument("--timeout", type=int, default=420)
ap.add_argument("--max-age-min", type=int, default=150)
ap.add_argument("--quality", default="Low")
ap.add_argument("--status", default="/tmp/gain_sched_status.txt")
ap.add_argument("--video", action="store_true", help="영상 녹화 (기본은 --no-video)")
a = ap.parse_args()
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
(out / "logs").mkdir(exist_ok=True)
jobs = json.loads(pathlib.Path(a.jobs).read_text())
lock = threading.Lock()
queue = list(jobs)
done = {"ok": 0, "fail": 0, "skip": 0}


def say(msg):
    line = f"{time.strftime('%m-%d %H:%M:%S')} [runner {out.name}] {msg}"
    print(line, flush=True)
    with lock:
        with open(a.status, "a") as f: f.write(line + "\n")


def port_open(p):
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", p)) == 0


def server_pid(p):
    r = subprocess.run(["pgrep", "-f", f"[C]arlaUE4-Linux-Shipping.*-carla-rpc-port={p}( |$)"], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def server_age_min(p):
    pids = server_pid(p)
    if not pids: return None
    r = subprocess.run(["ps", "-o", "etimes=", "-p", str(pids[0])], capture_output=True, text=True)
    try: return int(r.stdout.strip()) / 60
    except ValueError: return None


def restart(p):
    for pid in server_pid(p):
        try: os.kill(pid, 9)
        except ProcessLookupError: pass
    time.sleep(5)
    log = open(out / "logs" / f"carla_{p}.log", "a")
    subprocess.Popen([a.carla, f"-carla-rpc-port={p}", f"-quality-level={a.quality}", "-RenderOffScreen", "-nosound"],
                     cwd=os.path.dirname(a.carla), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     env={**os.environ, "DISPLAY": ""}, start_new_session=True)
    for _ in range(36):
        time.sleep(5)
        if port_open(p): time.sleep(15); say(f"서버 {p} 재기동 완료"); return True
    say(f"서버 {p} 재기동 실패"); return False


def run_one(p, job):
    tag = job["tag"]
    ev = out / f"events_{tag}.json"
    cmd = [a.py, "sw/scripts/sim/carla_demo.py", "--port", str(p), "--out", str(out), "--tag", tag, *([] if a.video else ["--no-video"]), *job["args"]]
    # 트래픽 매니저 포트도 서버마다 달라야 한다 (같은 8000 을 두 서버가 쓰면 서로의 차를 몬다)
    env = {**os.environ, "ICEPRED_TM_PORT": str(8000 + (p - 2000))}
    with open(out / "logs" / f"{tag}.log", "w") as lf:
        try:
            r = subprocess.run(cmd, cwd=a.code, stdout=lf, stderr=subprocess.STDOUT, timeout=a.timeout, env=env)
            rc = r.returncode
        except subprocess.TimeoutExpired:
            rc = -9
    return ev.exists() and rc == 0, rc


def worker(p):
    if not port_open(p): restart(p)
    while True:
        with lock:
            if not queue: return
            job = queue.pop(0)
        if (out / f"events_{job['tag']}.json").exists():
            done["skip"] += 1; continue
        age = server_age_min(p)
        if age is not None and age > a.max_age_min:
            say(f"서버 {p} 가 {age:.0f}분째 — 선제 재기동"); restart(p)
        ok, rc = run_one(p, job)
        if not ok:
            say(f"{job['tag']} 실패(rc {rc}) — 서버 {p} 재기동 후 재시도"); restart(p)
            ok, rc = run_one(p, job)
        with lock:
            done["ok" if ok else "fail"] += 1
            n = done["ok"] + done["fail"] + done["skip"]
        say(f"{'완료' if ok else '실패'} {job['tag']} ({n}/{len(jobs)})")


say(f"시작: 작업 {len(jobs)}개, 서버 {a.ports}")
ths = [threading.Thread(target=worker, args=(int(p),)) for p in a.ports.split(",")]
for t in ths: t.start()
for t in ths: t.join()
say(f"끝: 성공 {done['ok']}, 실패 {done['fail']}, 건너뜀 {done['skip']}")
(out / "runner_done.json").write_text(json.dumps(done))
