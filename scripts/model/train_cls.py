#!/usr/bin/env python3
"""RSCD로 노면 4클래스 분류기 학습 (데스크탑 RTX 3090용).
예) 스모크: python scripts/model/train_cls.py --smoke
    본학습: python scripts/model/train_cls.py --epochs 8 --samples-per-epoch 200000 --out models/roadnet_v1
"""
import argparse, csv, json, math, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.data import DataLoader
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import build_index, RscdDataset, train_tf, eval_tf, balanced_sampler, subsample, class_counts
from icepredict.train.model import RoadNet, export_onnx

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v1"))
ap.add_argument("--epochs", type=int, default=8)
ap.add_argument("--samples-per-epoch", type=int, default=200_000)
ap.add_argument("--batch", type=int, default=256)
ap.add_argument("--lr", type=float, default=2e-3)
ap.add_argument("--workers", type=int, default=16)
ap.add_argument("--size", type=int, default=224)
ap.add_argument("--val-per-class", type=int, default=0, help="0=검증셋 전체")
ap.add_argument("--smoke", action="store_true", help="아주 작은 데이터로 파이프라인 점검")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
if a.smoke:
    a.epochs, a.samples_per_epoch, a.val_per_class, a.workers = 1, 2048, 200, 4
    a.out = a.out.rstrip("/") + "_smoke"
torch.manual_seed(a.seed)
root, out = Path(a.root), Path(a.out); out.mkdir(parents=True, exist_ok=True)
dev = "cuda" if torch.cuda.is_available() else "cpu"

t0 = time.time()
tr_items = build_index(root, "train"); va_items = build_index(root, "vali_20k"); te_items = build_index(root, "test_50k")
if a.val_per_class: va_items = subsample(va_items, a.val_per_class, a.seed); te_items = subsample(te_items, a.val_per_class, a.seed)
print(f"index: train={len(tr_items)} val={len(va_items)} test={len(te_items)}  ({time.time()-t0:.0f}s)")
print("train class counts:", dict(zip(ROAD_CLASSES, class_counts(tr_items))))

dl_kw = dict(batch_size=a.batch, num_workers=a.workers, pin_memory=True, persistent_workers=a.workers > 0)
tr_dl = DataLoader(RscdDataset(tr_items, train_tf(a.size)), sampler=balanced_sampler(tr_items, a.samples_per_epoch, a.seed), drop_last=True, **dl_kw)
va_dl = DataLoader(RscdDataset(va_items, eval_tf(a.size)), shuffle=False, **dl_kw)
te_dl = DataLoader(RscdDataset(te_items, eval_tf(a.size)), shuffle=False, **dl_kw)

model = RoadNet().to(dev).to(memory_format=torch.channels_last)
opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.05)
steps = a.epochs * len(tr_dl); warm = max(1, int(0.05 * steps))
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: s / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))
scaler = torch.amp.GradScaler(dev)

@torch.no_grad()
def evaluate(dl):
    model.eval(); C = len(ROAD_CLASSES); cm = torch.zeros(C, C, dtype=torch.long)
    for x, y in dl:
        x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last)
        with torch.autocast(dev, dtype=torch.float16):
            p = model(x).argmax(1).cpu()
        cm += torch.bincount(y * C + p, minlength=C * C).view(C, C)
    acc = cm.diag().sum().item() / cm.sum().item()
    recall = (cm.diag() / cm.sum(1).clamp(min=1)).tolist(); prec = (cm.diag() / cm.sum(0).clamp(min=1)).tolist()
    return {"acc": acc, "macro_recall": sum(recall) / C, "recall": dict(zip(ROAD_CLASSES, recall)), "precision": dict(zip(ROAD_CLASSES, prec)), "cm": cm.tolist()}

log = open(out / "train_log.csv", "a", newline=""); w = csv.writer(log)
if log.tell() == 0: w.writerow(["epoch", "step", "loss", "val_acc", "val_macro_recall", "val_ice_recall", "lr", "elapsed_s"])
best = -1; step = 0; t0 = time.time()
for ep in range(a.epochs):
    model.train(); run = 0.0; n = 0
    for x, y in tr_dl:
        x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last); y = y.to(dev, non_blocking=True)
        with torch.autocast(dev, dtype=torch.float16):
            loss = F.cross_entropy(model(x), y, label_smoothing=0.05)
        opt.zero_grad(set_to_none=True); scaler.scale(loss).backward(); scaler.unscale_(opt)
        nn.utils.clip_grad_norm_(model.parameters(), 5.0); scaler.step(opt); scaler.update(); sched.step()
        run += loss.item(); n += 1; step += 1
        if step % 100 == 0:
            print(f"ep{ep} step{step} loss {run/n:.4f} lr {sched.get_last_lr()[0]:.2e} {time.time()-t0:.0f}s", flush=True)
    ev = evaluate(va_dl)
    print(f"== epoch {ep}: loss {run/max(n,1):.4f} val acc {ev['acc']:.4f} macro_recall {ev['macro_recall']:.4f} recall {json.dumps({k: round(v,3) for k,v in ev['recall'].items()})}", flush=True)
    w.writerow([ep, step, f"{run/max(n,1):.4f}", f"{ev['acc']:.4f}", f"{ev['macro_recall']:.4f}", f"{ev['recall']['black_ice']:.4f}", f"{sched.get_last_lr()[0]:.2e}", f"{time.time()-t0:.0f}"]); log.flush()
    torch.save({"model": model.state_dict(), "epoch": ep, "val": ev, "args": vars(a)}, out / "last.pt")
    if ev["macro_recall"] > best:
        best = ev["macro_recall"]; torch.save({"model": model.state_dict(), "epoch": ep, "val": ev, "args": vars(a)}, out / "best.pt")

model.load_state_dict(torch.load(out / "best.pt", map_location=dev)["model"])
te = evaluate(te_dl)
print("== TEST:", json.dumps({k: te[k] for k in ("acc", "macro_recall", "recall", "precision")}, indent=1))
print("confusion (rows=true, cols=pred, order", ROAD_CLASSES, "):"); [print("  ", r) for r in te["cm"]]
(out / "test_metrics.json").write_text(json.dumps(te, indent=1))
export_onnx(model, str(out / "roadnet.onnx"), a.size); print("ONNX saved:", out / "roadnet.onnx")
