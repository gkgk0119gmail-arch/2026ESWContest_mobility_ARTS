#!/usr/bin/env python3
"""실제 노면 사진(RSCD)을 실제 보드(STM32N6 NPU + 융합)에 넣어 판정 통계를 낸다 — '실물 데이터 인-더-루프' 평가.
시뮬레이션 화면이 아니라 실제 도로 사진으로 카메라 모델과 보드 판정 경로를 검증한다.
클래스별 N장: 경보율(alarm), 평균 위험도, 4클래스 예측 분포, 반사도, 보드 추론 시간.
사용: python3 sw/scripts/deploy/rscd_in_the_loop.py --n 200 [--ip 192.168.50.158] [--split vali_20k]
결과: logs/rscd_in_the_loop_<시각>.json + 표 출력"""
import argparse, json, pathlib, socket, struct, time, sys, collections
import numpy as np, cv2
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.common.rscd import parse

FR_HDR, FR_VD, FR_CHUNK = "<IHHHH", "<I4fffBBHII", 1400
NPU_IN_SCALE, NPU_IN_ZP = 0.018658448, -14
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1); STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
CLS = ("normal", "wet", "black_ice", "pothole")

def quantize(bgr):
    im = cv2.resize(bgr, (224, 224), interpolation=cv2.INTER_AREA)
    x = (im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - MEAN) / STD
    return np.clip(np.round(x / NPU_IN_SCALE) + NPU_IN_ZP, -128, 127).astype(np.int8).tobytes()

def board_infer(sock, ip, port, q, fid, gap_us=150):
    n = (len(q) + FR_CHUNK - 1) // FR_CHUNK
    for i in range(n):
        pl = q[i * FR_CHUNK:(i + 1) * FR_CHUNK]
        sock.sendto(struct.pack(FR_HDR, fid, i, n, len(pl), 0) + pl, (ip, port))
        t = time.perf_counter() + gap_us / 1e6
        while time.perf_counter() < t: pass
    try: d, _ = sock.recvfrom(128)
    except socket.timeout: return None
    f, l0, l1, l2, l3, spec, risk, alarm, level, _p, us, tot = struct.unpack(FR_VD, d)
    if f != fid: return None
    lg = np.array([l0, l1, l2, l3]); e = np.exp(lg - lg.max()); p = e / e.sum()
    return dict(p=p, spec=spec, risk=risk, alarm=bool(alarm), level=level, us=us)

ap = argparse.ArgumentParser()
ap.add_argument("--ip", default="192.168.50.158"); ap.add_argument("--port", type=int, default=5559)
ap.add_argument("--n", type=int, default=200, help="4클래스 각각 이 장수")
ap.add_argument("--split", default="vali_20k"); ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
root = ROOT / "dataset/rscd/rscd" / a.split
by = collections.defaultdict(list)
for f in sorted(root.glob("*.jpg")):
    lab = parse(f.name)
    if lab: by[lab.cls4].append(f)
rng = np.random.default_rng(a.seed)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.settimeout(2.0)
fid = int(time.time()) & 0xFFFF; res = {}
print(f"보드 {a.ip}:{a.port}  클래스별 {a.n}장  ({a.split})")
for cls in CLS:
    files = by.get(cls, [])
    if not files: print(f"  {cls}: 사진 없음"); continue
    pick = [files[i] for i in rng.choice(len(files), min(a.n, len(files)), replace=False)]
    rows = []; fails = 0
    for f in pick:
        fid = (fid + 1) & 0xFFFFFFFF
        r = board_infer(sock, a.ip, a.port, quantize(cv2.imread(str(f))), fid)
        if r is None: fails += 1; continue
        rows.append(r)
    if not rows: print(f"  {cls}: 응답 없음 ({fails} 실패)"); continue
    P = np.stack([r["p"] for r in rows]); pred = P.argmax(1)
    res[cls] = dict(n=len(rows), fail=fails, alarm_rate=float(np.mean([r["alarm"] for r in rows])),
                    risk_mean=float(np.mean([r["risk"] for r in rows])), spec_mean=float(np.mean([r["spec"] for r in rows])),
                    pred_dist={c: float(np.mean(pred == i)) for i, c in enumerate(CLS)},
                    p_mean={c: float(P[:, i].mean()) for i, c in enumerate(CLS)},
                    infer_us_mean=float(np.mean([r["us"] for r in rows])), infer_us_max=int(max(r["us"] for r in rows)))
    d = res[cls]
    print(f"  {cls:9s} n={d['n']:3d}  경보율 {d['alarm_rate']*100:5.1f}%  위험도 {d['risk_mean']:.2f}  반사도 {d['spec_mean']:.2f}  "
          f"예측: " + " ".join(f"{c[:4]} {d['pred_dist'][c]*100:4.0f}%" for c in CLS) + f"  NPU {d['infer_us_mean']/1000:.1f}ms")
out = ROOT / "logs" / f"rscd_in_the_loop_{time.strftime('%Y%m%d_%H%M')}.json"
out.write_text(json.dumps(dict(args=vars(a), results=res), indent=1, ensure_ascii=False))
print("저장:", out)
