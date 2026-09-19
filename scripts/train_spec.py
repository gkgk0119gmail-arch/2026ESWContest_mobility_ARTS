#!/usr/bin/env python3
"""RoadNet에 **반사도 헤드**를 붙여 멀티태스크로 학습한다 (융합 가중치의 45%를 채운다).

왜
  [ctx] 가중치는 (alpha=0.35 분류, beta=0.45 반사도, gamma=0.20 차선)인데 반사도 헤드가 없어
  지금까지 risk = p_ice 였다 — 이름만 베이지안 융합이었다. 실제로 2026-09-19 보드 NPU 데모에서
  p_ice 최대 0.364 < 임계 0.441 이라 1차 경고가 나지 않았다. 반사도가 있으면
  risk = 0.35·p_ice + 0.45·spec + 0.20·(1-lane) 이라 얼음의 강한 정반사가 판정을 끌어올린다.

라벨
  CARLA 수집기가 프레임마다 ROI 평균 반사도를 meta.json에 남긴다:
    - 얼음: composite_ice가 **실제로 쓴** alpha·R(프레넬×specular×두께변조) 평균 — 우리가 정의한 물리량 그대로
    - 젖음: 엔진 렌더라 R을 모른다 → 0.35 · wetness/100 (거친 표면 가정, 문서화된 근사)
    - 마름: 0.02 (아스팔트 기본 반사율)
  RSCD 실사진에는 라벨이 없다 → has_spec=0으로 손실에서 제외 (분류 손실만 받는다).

주의
  반사도는 CARLA 합성물에서만 지도된다. 실차 일반화 근거가 아니며, 융합에서 beta를 쓰는 것은
  "시뮬레이션 안에서 설계대로 동작함"을 보이는 것이다. 발표 시 이 한계를 분류 헤드(RSCD 실사진)와
  구분해 말해야 한다.

예)
  python scripts/train_spec.py --smoke
  python scripts/train_spec.py --epochs 4 --steps-per-epoch 500
"""
import argparse, csv, json, math, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.data import DataLoader, WeightedRandomSampler
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import (build_index, build_carla_spec_index, split_items, with_spec,
                                   SpecDataset, finetune_tf, eval_tf, subsample, class_counts)
from icepredict.train.model import RoadNet, export_onnx

ap = argparse.ArgumentParser()
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v3"))
ap.add_argument("--init", default=os.path.expanduser("~/icepredict/models/roadnet_v2/best.pt"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v3_spec"))
ap.add_argument("--epochs", type=int, default=4)
ap.add_argument("--steps-per-epoch", type=int, default=500)
ap.add_argument("--batch", type=int, default=128)
ap.add_argument("--carla-frac", type=float, default=0.5)
ap.add_argument("--lr", type=float, default=2e-4)
ap.add_argument("--backbone-lr-mult", type=float, default=0.2)
ap.add_argument("--spec-weight", type=float, default=1.0, help="반사도 손실 가중치")
ap.add_argument("--workers", type=int, default=12)
ap.add_argument("--size", type=int, default=224)
ap.add_argument("--val-frac", type=float, default=0.3)
ap.add_argument("--rscd-test-per-class", type=int, default=0)
ap.add_argument("--smoke", action="store_true")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
if a.smoke:
    a.epochs, a.steps_per_epoch, a.batch, a.workers = 1, 25, 64, 4
    a.rscd_test_per_class = 100
    a.out = a.out.rstrip("/") + "_smoke"
torch.manual_seed(a.seed)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
dev = "cuda" if torch.cuda.is_available() else "cpu"
SPEC_I = len(ROAD_CLASSES)          # 출력 마지막 열이 반사도 로짓

# ---- 데이터 ---------------------------------------------------------------
t0 = time.time()
rscd_tr = with_spec(build_index(Path(a.rscd), "train"))
rscd_te = build_index(Path(a.rscd), "test_50k")
if a.rscd_test_per_class:
    rscd_te = subsample(rscd_te, a.rscd_test_per_class, a.seed)
rscd_te = with_spec(rscd_te)
carla_all = build_carla_spec_index(Path(a.carla))
labeled = sum(1 for _, _, _, h in carla_all if h > 0)
c_tr4, c_va4, va_keys = split_items([(p, c) for p, c, _, _ in carla_all], val_frac=a.val_frac, seed=a.seed)
spec_of = {p: (s, h) for p, _, s, h in carla_all}
carla_tr = [(p, c, *spec_of[p]) for p, c in c_tr4]
carla_va = [(p, c, *spec_of[p]) for p, c in c_va4]
print(f"index: rscd_train={len(rscd_tr)} rscd_test={len(rscd_te)} carla_train={len(carla_tr)} "
      f"carla_val={len(carla_va)} (반사도 라벨 {labeled}/{len(carla_all)})  ({time.time()-t0:.0f}s)")
sv = np.array([s for _, c, s, h in carla_tr if h > 0 and c == 2])
wv = np.array([s for _, c, s, h in carla_tr if h > 0 and c == 1])
nv = np.array([s for _, c, s, h in carla_tr if h > 0 and c == 0])
for nm, v in (("black_ice", sv), ("wet", wv), ("normal", nv)):
    if len(v): print(f"  라벨 분포 {nm:10s} n={len(v):5d} 평균 {v.mean():.3f} 중앙 {np.median(v):.3f} p90 {np.percentile(v,90):.3f}")

def cbw(items):
    n = class_counts([(p, c) for p, c, _, _ in items])
    return torch.tensor([1.0 / max(n[c], 1) for _, c, _, _ in items], dtype=torch.double)

mixed = SpecDataset(rscd_tr + carla_tr, finetune_tf(a.size))
w_r, w_c = cbw(rscd_tr), cbw(carla_tr)
w_r = w_r / w_r.sum() * (1.0 - a.carla_frac); w_c = w_c / w_c.sum() * a.carla_frac
g = torch.Generator().manual_seed(a.seed)
sampler = WeightedRandomSampler(torch.cat([w_r, w_c]), num_samples=a.steps_per_epoch * a.batch,
                                replacement=True, generator=g)
dl_kw = dict(batch_size=a.batch, num_workers=a.workers, pin_memory=True, persistent_workers=a.workers > 0)
tr_dl = DataLoader(mixed, sampler=sampler, drop_last=True, **dl_kw)
carla_va_dl = DataLoader(SpecDataset(carla_va, eval_tf(a.size)), shuffle=False, **dl_kw)
rscd_te_dl = DataLoader(SpecDataset(rscd_te, eval_tf(a.size)), shuffle=False, **dl_kw)

# ---- 모델: v2 가중치 + 새 반사도 헤드 ------------------------------------
model = RoadNet(pretrained=False, spec_head=True).to(dev).to(memory_format=torch.channels_last)
ck = torch.load(a.init, map_location="cpu")
missing, unexpected = model.load_state_dict(ck["model"], strict=False)
assert not unexpected, f"예상 못한 키: {unexpected[:5]}"
assert all(k.startswith("head_spec") for k in missing), f"빠진 키가 반사도 헤드만이 아니다: {missing[:5]}"
print(f"init: {a.init} (epoch {ck.get('epoch')}) — 새로 학습하는 파라미터: {len(missing)}개 (반사도 헤드)")

opt = torch.optim.AdamW([
    {"params": model.features.parameters(), "lr": a.lr * a.backbone_lr_mult},
    {"params": model.head_cls.parameters(), "lr": a.lr},
    {"params": model.head_spec.parameters(), "lr": a.lr * 5},      # 새 헤드는 빠르게
], weight_decay=0.02)
steps = a.epochs * len(tr_dl); warm = max(1, int(0.1 * steps))
sched = torch.optim.lr_scheduler.LambdaLR(
    opt, lambda s: s / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))
scaler = torch.amp.GradScaler(dev)

@torch.no_grad()
def evaluate(dl, present=ROAD_CLASSES):
    model.eval(); C = len(ROAD_CLASSES); cm = torch.zeros(C, C, dtype=torch.long)
    se, n_spec = 0.0, 0
    per_cls = {c: [] for c in range(C)}
    for x, y, sp, hs in dl:
        x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last)
        with torch.autocast(dev, dtype=torch.float16):
            o = model(x).float().cpu()
        cm += torch.bincount(y * C + o[:, :C].argmax(1), minlength=C * C).view(C, C)
        pred = torch.sigmoid(o[:, SPEC_I])
        m = hs > 0
        if m.any():
            se += float(((pred[m] - sp[m]) ** 2).sum()); n_spec += int(m.sum())
            for c in range(C):
                mm = m & (y == c)
                if mm.any(): per_cls[c] += pred[mm].tolist()
    model.train()
    acc = cm.diag().sum().item() / max(cm.sum().item(), 1)
    rec = (cm.diag() / cm.sum(1).clamp(min=1)).tolist()
    idx = [ROAD_CLASSES.index(c) for c in present]
    return {"acc": acc, "macro_recall": sum(rec[i] for i in idx) / len(idx),
            "recall": dict(zip(ROAD_CLASSES, rec)),
            "spec_rmse": (se / n_spec) ** 0.5 if n_spec else float("nan"),
            "spec_mean": {ROAD_CLASSES[c]: (float(np.mean(v)) if v else float("nan")) for c, v in per_cls.items()},
            "cm": cm.tolist()}

CARLA_PRESENT = ("normal", "wet", "black_ice")
log = open(out / "train_log.csv", "a", newline=""); w = csv.writer(log)
if log.tell() == 0:
    w.writerow(["epoch", "loss", "loss_cls", "loss_spec", "rscd_acc", "carla_acc", "carla_ice", "spec_rmse", "sep", "elapsed_s"])
best, t0 = -1e9, time.time()
for ep in range(a.epochs):
    run = {"all": 0.0, "cls": 0.0, "spec": 0.0}; n = 0
    for x, y, sp, hs in tr_dl:
        x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last)
        y = y.to(dev, non_blocking=True); sp = sp.to(dev, non_blocking=True); hs = hs.to(dev, non_blocking=True)
        with torch.autocast(dev, dtype=torch.float16):
            o = model(x)
            l_cls = F.cross_entropy(o[:, :len(ROAD_CLASSES)], y, label_smoothing=0.05)
            # 라벨 있는 표본만: BCE(로짓, 연속 타깃) — 반사도는 0~1 연속량이라 회귀로 본다
            l_sp = F.binary_cross_entropy_with_logits(o[:, SPEC_I], sp.clamp(0, 1), reduction="none")
            l_spec = (l_sp * hs).sum() / hs.sum().clamp(min=1)
            loss = l_cls + a.spec_weight * l_spec
        opt.zero_grad(set_to_none=True); scaler.scale(loss).backward(); scaler.unscale_(opt)
        nn.utils.clip_grad_norm_(model.parameters(), 5.0); scaler.step(opt); scaler.update(); sched.step()
        run["all"] += loss.item(); run["cls"] += l_cls.item(); run["spec"] += float(l_spec); n += 1
        if n % 100 == 0:
            print(f"  ep{ep} step{n}/{len(tr_dl)} loss {run['all']/n:.4f} (cls {run['cls']/n:.4f} spec {run['spec']/n:.4f}) {time.time()-t0:.0f}s", flush=True)
    cv = evaluate(carla_va_dl, CARLA_PRESENT); rv = evaluate(rscd_te_dl)
    sm = cv["spec_mean"]; sep = sm["black_ice"] - max(sm["wet"], sm["normal"])   # 얼음이 나머지보다 얼마나 높은가
    score = cv["macro_recall"] + sep - 3.0 * max(0.0, 0.8868 - rv["acc"])
    print(f"== ep{ep} loss {run['all']/max(n,1):.4f} | RSCD acc {rv['acc']:.4f} | CARLA acc {cv['acc']:.4f} ice {cv['recall']['black_ice']:.4f} "
          f"| spec rmse {cv['spec_rmse']:.4f} 평균(ice/wet/normal) {sm['black_ice']:.3f}/{sm['wet']:.3f}/{sm['normal']:.3f} 분리 {sep:+.3f} | score {score:.4f}", flush=True)
    w.writerow([ep, f"{run['all']/max(n,1):.4f}", f"{run['cls']/max(n,1):.4f}", f"{run['spec']/max(n,1):.4f}",
                f"{rv['acc']:.4f}", f"{cv['acc']:.4f}", f"{cv['recall']['black_ice']:.4f}",
                f"{cv['spec_rmse']:.4f}", f"{sep:.4f}", f"{time.time()-t0:.0f}"]); log.flush()
    torch.save({"model": model.state_dict(), "epoch": ep, "carla": cv, "rscd": rv, "args": vars(a)}, out / "last.pt")
    if score > best:
        best = score; best_cv, best_rv, best_ep = cv, rv, ep
        torch.save({"model": model.state_dict(), "epoch": ep, "carla": cv, "rscd": rv, "args": vars(a)}, out / "best.pt")

model.load_state_dict(torch.load(out / "best.pt", map_location=dev)["model"])
export_onnx(model, str(out / "roadnet_spec.onnx"), a.size)
(out / "metrics.json").write_text(json.dumps({"carla": best_cv, "rscd": best_rv, "best_epoch": best_ep,
                                              "carla_val_keys": va_keys, "args": vars(a)}, indent=1))
sm = best_cv["spec_mean"]
print("\n=== 반사도 헤드 결과 ===")
print(f"RSCD acc              : {best_rv['acc']:.4f}   [v2 = 0.8868]")
print(f"CARLA black_ice recall: {best_cv['recall']['black_ice']:.4f}   [v2 = 0.6424]")
print(f"반사도 RMSE           : {best_cv['spec_rmse']:.4f}")
print(f"반사도 평균 ice/wet/normal: {sm['black_ice']:.3f} / {sm['wet']:.3f} / {sm['normal']:.3f}")
print(f"→ 융합 기여: 얼음에서 beta·spec = 0.45 × {sm['black_ice']:.3f} = {0.45*sm['black_ice']:.3f} (임계 0.441)")
print(f"ONNX: {out / 'roadnet_spec.onnx'}")
