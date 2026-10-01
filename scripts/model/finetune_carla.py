#!/usr/bin/env python3
"""RoadNet v1(RSCD 학습본)을 CARLA 합성 데이터로 미세조정한다.

왜 섞어 쓰는가
  CARLA 수집기는 normal/wet/black_ice 3클래스만 만든다 (pothole은 CARLA 재현이 어려움).
  CARLA만으로 미세조정하면 4클래스 헤드의 pothole이 무너지고, 실사진 도메인 성능도 함께
  잊는다. 그래서 배치를 RSCD와 CARLA로 섞고(--carla-frac), RSCD test_50k 성능이 떨어지지
  않는지를 합격 조건으로 본다.

합격 조건 두 개 (둘 다 봐야 한다)
  (a) 실사진 회귀 없음 : RSCD test_50k의 acc / black_ice recall이 v1 대비 유지
  (b) 시뮬 동작        : CARLA 검증 split의 black_ice recall이 충분히 높음
  (b)만 좋으면 우리 합성 아티팩트를 배운 것이고, (a)만 좋으면 미세조정이 안 된 것이다.

예)
  python scripts/model/finetune_carla.py --smoke
  python scripts/model/finetune_carla.py --epochs 4 --steps-per-epoch 600
"""
import argparse, csv, json, math, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.data import DataLoader, ConcatDataset
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import (build_index, build_carla_index, split_items, RscdDataset,
                                   finetune_tf, eval_tf, subsample, class_counts)
from icepredict.train.model import RoadNet, export_onnx

ap = argparse.ArgumentParser()
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v2"))
ap.add_argument("--init", default=os.path.expanduser("~/icepredict/models/roadnet_v1/best.pt"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v2"))
ap.add_argument("--epochs", type=int, default=4)
ap.add_argument("--steps-per-epoch", type=int, default=600)
ap.add_argument("--batch", type=int, default=128)
ap.add_argument("--carla-frac", type=float, default=0.5, help="배치 중 CARLA 비중")
ap.add_argument("--lr", type=float, default=2e-4, help="미세조정이므로 본학습(2e-3)보다 훨씬 낮게")
ap.add_argument("--backbone-lr-mult", type=float, default=0.2, help="백본은 더 천천히")
ap.add_argument("--workers", type=int, default=12)
ap.add_argument("--size", type=int, default=224)
ap.add_argument("--rscd-test-per-class", type=int, default=0, help="0=test_50k 전체")
# val_frac이 작으면 (town,weather) 조합이 건조/젖음 각 1개씩만 뽑혀 검증이 한 도시에
# 몰린다. 조합 단위로 나누므로 0.3 정도는 있어야 조건 다양성이 확보된다.
ap.add_argument("--val-frac", type=float, default=0.3)
ap.add_argument("--smoke", action="store_true")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
if a.smoke:
    a.epochs, a.steps_per_epoch, a.batch, a.workers = 1, 30, 64, 4
    a.rscd_test_per_class = 150
    a.out = a.out.rstrip("/") + "_smoke"
torch.manual_seed(a.seed)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
dev = "cuda" if torch.cuda.is_available() else "cpu"

# ---- 인덱스 ---------------------------------------------------------------
t0 = time.time()
rscd_root = Path(a.rscd)
rscd_tr = build_index(rscd_root, "train")
rscd_te = build_index(rscd_root, "test_50k")
if a.rscd_test_per_class:
    rscd_te = subsample(rscd_te, a.rscd_test_per_class, a.seed)
carla_all = build_carla_index(Path(a.carla))
carla_tr, carla_va, va_keys = split_items(carla_all, val_frac=a.val_frac, seed=a.seed)
print(f"index: rscd_train={len(rscd_tr)} rscd_test={len(rscd_te)} "
      f"carla_train={len(carla_tr)} carla_val={len(carla_va)}  ({time.time()-t0:.0f}s)")
print("carla train 클래스:", dict(zip(ROAD_CLASSES, class_counts(carla_tr))))
print("carla val   클래스:", dict(zip(ROAD_CLASSES, class_counts(carla_va))))
print("carla val   조합  :", ", ".join(va_keys))

# ---- 샘플러: 소스 비율 × 클래스 균형 -------------------------------------
# CARLA에는 pothole이 없다. 그래서 pothole은 RSCD 쪽에서만 뽑히고, 전체 배치에서 네 클래스가
# 고르게 들어오도록 소스별 가중치를 따로 만든다.
def class_balanced_weights(items):
    n = class_counts(items)
    return torch.tensor([1.0 / max(n[c], 1) for _, c in items], dtype=torch.double)

mixed = ConcatDataset([RscdDataset(rscd_tr, finetune_tf(a.size)),
                       RscdDataset(carla_tr, finetune_tf(a.size))])
w_rscd = class_balanced_weights(rscd_tr); w_carla = class_balanced_weights(carla_tr)
w_rscd = w_rscd / w_rscd.sum() * (1.0 - a.carla_frac)
w_carla = w_carla / w_carla.sum() * a.carla_frac
weights = torch.cat([w_rscd, w_carla])
g = torch.Generator().manual_seed(a.seed)
sampler = torch.utils.data.WeightedRandomSampler(weights, num_samples=a.steps_per_epoch * a.batch,
                                                 replacement=True, generator=g)

dl_kw = dict(batch_size=a.batch, num_workers=a.workers, pin_memory=True, persistent_workers=a.workers > 0)
tr_dl = DataLoader(mixed, sampler=sampler, drop_last=True, **dl_kw)
carla_va_dl = DataLoader(RscdDataset(carla_va, eval_tf(a.size)), shuffle=False, **dl_kw)
rscd_te_dl = DataLoader(RscdDataset(rscd_te, eval_tf(a.size)), shuffle=False, **dl_kw)

# ---- 모델: v1 가중치에서 시작 --------------------------------------------
model = RoadNet(pretrained=False).to(dev).to(memory_format=torch.channels_last)
ck = torch.load(a.init, map_location="cpu")
model.load_state_dict(ck["model"])
print(f"init: {a.init} (epoch {ck.get('epoch')}, val macro_recall {ck.get('val',{}).get('macro_recall')})")

opt = torch.optim.AdamW([
    {"params": model.features.parameters(), "lr": a.lr * a.backbone_lr_mult},
    {"params": model.head_cls.parameters(), "lr": a.lr},
], weight_decay=0.02)
steps = a.epochs * len(tr_dl); warm = max(1, int(0.1 * steps))
sched = torch.optim.lr_scheduler.LambdaLR(
    opt, lambda s: s / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))
scaler = torch.amp.GradScaler(dev)

@torch.no_grad()
def evaluate(dl, present: tuple[str, ...] = ROAD_CLASSES):
    model.eval(); C = len(ROAD_CLASSES); cm = torch.zeros(C, C, dtype=torch.long)
    for x, y in dl:
        x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last)
        with torch.autocast(dev, dtype=torch.float16):
            p = model(x).argmax(1).cpu()
        cm += torch.bincount(y * C + p, minlength=C * C).view(C, C)
    acc = cm.diag().sum().item() / max(cm.sum().item(), 1)
    recall = (cm.diag() / cm.sum(1).clamp(min=1)).tolist()
    prec = (cm.diag() / cm.sum(0).clamp(min=1)).tolist()
    idx = [ROAD_CLASSES.index(c) for c in present]        # 없는 클래스는 macro에서 제외
    return {"acc": acc, "macro_recall": sum(recall[i] for i in idx) / len(idx),
            "recall": dict(zip(ROAD_CLASSES, recall)), "precision": dict(zip(ROAD_CLASSES, prec)),
            "cm": cm.tolist()}

CARLA_PRESENT = ("normal", "wet", "black_ice")
base = evaluate(rscd_te_dl)
print(f"[기준] v1 RSCD test: acc {base['acc']:.4f} macro_recall {base['macro_recall']:.4f} "
      f"ice_recall {base['recall']['black_ice']:.4f}")
c0 = evaluate(carla_va_dl, CARLA_PRESENT)
print(f"[기준] v1 CARLA val: acc {c0['acc']:.4f} macro_recall {c0['macro_recall']:.4f} "
      f"ice_recall {c0['recall']['black_ice']:.4f}   ← 도메인 갭의 크기")

log = open(out / "finetune_log.csv", "a", newline=""); w = csv.writer(log)
if log.tell() == 0:
    w.writerow(["epoch", "loss", "carla_acc", "carla_ice_recall", "rscd_acc", "rscd_ice_recall", "score", "elapsed_s"])
best, t0 = -1e9, time.time()
for ep in range(a.epochs):
    model.train(); run, n = 0.0, 0
    for x, y in tr_dl:
        x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last); y = y.to(dev, non_blocking=True)
        with torch.autocast(dev, dtype=torch.float16):
            loss = F.cross_entropy(model(x), y, label_smoothing=0.05)
        opt.zero_grad(set_to_none=True); scaler.scale(loss).backward(); scaler.unscale_(opt)
        nn.utils.clip_grad_norm_(model.parameters(), 5.0); scaler.step(opt); scaler.update(); sched.step()
        run += loss.item(); n += 1
        if n % 100 == 0:
            print(f"  ep{ep} step{n}/{len(tr_dl)} loss {run/n:.4f} {time.time()-t0:.0f}s", flush=True)
    cv = evaluate(carla_va_dl, CARLA_PRESENT); rv = evaluate(rscd_te_dl)
    # 선택 기준: 시뮬에서 동작하되 실사진 성능을 깎지 않은 체크포인트.
    # 실사진 정확도가 기준보다 떨어진 만큼은 3배로 벌점을 준다 (실차 근거를 잃으면 안 된다).
    score = cv["macro_recall"] - 3.0 * max(0.0, base["acc"] - rv["acc"])
    print(f"== ep{ep} loss {run/max(n,1):.4f} | CARLA acc {cv['acc']:.4f} ice {cv['recall']['black_ice']:.4f} "
          f"| RSCD acc {rv['acc']:.4f} ice {rv['recall']['black_ice']:.4f} | score {score:.4f}", flush=True)
    w.writerow([ep, f"{run/max(n,1):.4f}", f"{cv['acc']:.4f}", f"{cv['recall']['black_ice']:.4f}",
                f"{rv['acc']:.4f}", f"{rv['recall']['black_ice']:.4f}", f"{score:.4f}",
                f"{time.time()-t0:.0f}"]); log.flush()
    torch.save({"model": model.state_dict(), "epoch": ep, "carla": cv, "rscd": rv, "args": vars(a)}, out / "last.pt")
    if score > best:
        best = score
        torch.save({"model": model.state_dict(), "epoch": ep, "carla": cv, "rscd": rv, "args": vars(a)}, out / "best.pt")

ck = torch.load(out / "best.pt", map_location=dev); model.load_state_dict(ck["model"])
res = {"baseline_rscd": base, "baseline_carla": c0, "final_carla": ck["carla"], "final_rscd": ck["rscd"],
       "best_epoch": ck["epoch"], "carla_val_keys": va_keys, "args": vars(a)}
(out / "metrics.json").write_text(json.dumps(res, indent=1))
export_onnx(model, str(out / "roadnet.onnx"), a.size)
print("\n=== 미세조정 결과 ===")
print(f"CARLA black_ice recall : {c0['recall']['black_ice']:.4f} → {ck['carla']['recall']['black_ice']:.4f}")
print(f"RSCD  acc              : {base['acc']:.4f} → {ck['rscd']['acc']:.4f}")
print(f"RSCD  black_ice recall : {base['recall']['black_ice']:.4f} → {ck['rscd']['recall']['black_ice']:.4f}")
print("ONNX:", out / "roadnet.onnx")
