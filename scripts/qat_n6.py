#!/usr/bin/env python3
"""RoadNet v2를 QAT(양자화 인지 학습)로 int8화한다.

왜 QAT인가
  ONNX Runtime PTQ로는 acc 0.910 -> 0.48로 붕괴했다. per-channel·보정법·conv-only 모두
  무효였고, 원인은 features.1의 첫 depthwise Conv에서 최대오차 15가 발생하는 것으로
  특정됐다 (docs/HANDOFF_2026-09-16.md).

  ST Edge AI Core 3.0.0을 실측한 결과 두 가지가 확인됐다 (docs/n6_npu_analysis_2026-09-18.md):
    - MobileNetV3의 HardSwish/HardSigmoid/QLinearConv가 모두 NPU 지원 목록에 있다
      -> 백본이 문제가 아니다
    - `--quantize`는 텐서 포맷 설정 파일이고 PTQ를 하지 않는다. ST Core는 이미 양자화된
      모델을 받는다 -> 벤더 양자화기가 대신 풀어주지 않는다
  따라서 남은 길은 학습 중에 양자화 오차를 모델이 흡수하게 하는 QAT뿐이다.

성공 판정
  FP32 v2 기준: RSCD test acc 0.8868 / black_ice recall 0.9800, CARLA val ice recall 0.6424.
  QAT 후 fake-quant 정확도가 이 값에 근접해야 한다. PTQ의 0.48과 비교하는 것이 요점이다.
  최종 객관 지표는 stedgeai analyze의 HW epoch 수다 (NPU에 실제로 매핑됐는가).

예)
  python scripts/qat_n6.py --smoke
  python scripts/qat_n6.py --epochs 3 --steps-per-epoch 400
"""
import argparse, csv, json, math, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.data import DataLoader, ConcatDataset, WeightedRandomSampler
import torch.ao.quantization as tq
from torch.ao.quantization.quantize_fx import prepare_qat_fx
from icepredict.common.protocol import ROAD_CLASSES
from icepredict.train.data import (build_index, build_carla_index, split_items, RscdDataset,
                                   finetune_tf, eval_tf, subsample, class_counts)
from icepredict.train.model import RoadNet

ap = argparse.ArgumentParser()
ap.add_argument("--rscd", default=os.path.expanduser("~/icepredict/dataset/rscd/RSCD dataset-1million"))
ap.add_argument("--carla", default=os.path.expanduser("~/icepredict/dataset/carla_v2"))
ap.add_argument("--init", default=os.path.expanduser("~/icepredict/models/roadnet_v2/best.pt"))
ap.add_argument("--out", default=os.path.expanduser("~/icepredict/models/roadnet_v2_qat"))
ap.add_argument("--epochs", type=int, default=3)
ap.add_argument("--steps-per-epoch", type=int, default=400)
ap.add_argument("--batch", type=int, default=96)
ap.add_argument("--carla-frac", type=float, default=0.5)
ap.add_argument("--lr", type=float, default=5e-5, help="QAT는 미세조정(2e-4)보다 더 낮게")
ap.add_argument("--backbone-lr-mult", type=float, default=0.2)
ap.add_argument("--workers", type=int, default=12)
ap.add_argument("--size", type=int, default=224)
ap.add_argument("--val-frac", type=float, default=0.3)
ap.add_argument("--rscd-test-per-class", type=int, default=0, help="0=test_50k 전체")
ap.add_argument("--freeze-observer-epoch", type=int, default=2,
                help="이 에포크부터 관측자를 멈춰 스케일을 고정한다 (마지막 에포크는 고정된 스케일로 적응)")
ap.add_argument("--smoke", action="store_true")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
if a.smoke:
    a.epochs, a.steps_per_epoch, a.batch, a.workers = 1, 20, 48, 4
    a.rscd_test_per_class, a.freeze_observer_epoch = 100, 1
    a.out = a.out.rstrip("/") + "_smoke"
torch.manual_seed(a.seed)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
dev = "cuda" if torch.cuda.is_available() else "cpu"

# ---- 데이터 (미세조정과 동일 구성) ----------------------------------------
t0 = time.time()
rscd_root = Path(a.rscd)
rscd_tr = build_index(rscd_root, "train")
rscd_te = build_index(rscd_root, "test_50k")
if a.rscd_test_per_class:
    rscd_te = subsample(rscd_te, a.rscd_test_per_class, a.seed)
carla_tr, carla_va, va_keys = split_items(build_carla_index(Path(a.carla)),
                                          val_frac=a.val_frac, seed=a.seed)
print(f"index: rscd_train={len(rscd_tr)} rscd_test={len(rscd_te)} "
      f"carla_train={len(carla_tr)} carla_val={len(carla_va)}  ({time.time()-t0:.0f}s)")

def class_balanced_weights(items):
    n = class_counts(items)
    return torch.tensor([1.0 / max(n[c], 1) for _, c in items], dtype=torch.double)

mixed = ConcatDataset([RscdDataset(rscd_tr, finetune_tf(a.size)),
                       RscdDataset(carla_tr, finetune_tf(a.size))])
w_r = class_balanced_weights(rscd_tr); w_c = class_balanced_weights(carla_tr)
w_r = w_r / w_r.sum() * (1.0 - a.carla_frac); w_c = w_c / w_c.sum() * a.carla_frac
g = torch.Generator().manual_seed(a.seed)
sampler = WeightedRandomSampler(torch.cat([w_r, w_c]),
                                num_samples=a.steps_per_epoch * a.batch, replacement=True, generator=g)
dl_kw = dict(batch_size=a.batch, num_workers=a.workers, pin_memory=True, persistent_workers=a.workers > 0)
tr_dl = DataLoader(mixed, sampler=sampler, drop_last=True, **dl_kw)
carla_va_dl = DataLoader(RscdDataset(carla_va, eval_tf(a.size)), shuffle=False, **dl_kw)
rscd_te_dl = DataLoader(RscdDataset(rscd_te, eval_tf(a.size)), shuffle=False, **dl_kw)

# ---- FP32 v2에서 시작해 QAT 준비 ------------------------------------------
fp32 = RoadNet(pretrained=False)
ck = torch.load(a.init, map_location="cpu")
fp32.load_state_dict(ck["model"])
print(f"init: {a.init} (epoch {ck.get('epoch')})")

# qconfig를 직접 구성한다. get_default_qat_qconfig_mapping("x86")은
# FusedMovingAvgObsFakeQuantize를 쓰는데, 그 융합 연산(aten::fused_moving_avg_obs_fake_quant)은
# ONNX로 내보낼 수 없다. 일반 FakeQuantize는 aten::fake_quantize_per_*_affine 로 내려가고
# 이것이 QuantizeLinear/DequantizeLinear 쌍으로 변환된다.
# 가중치: 대칭 per-channel int8 / 활성: per-tensor affine uint8 — ONNX QDQ와 Neural-ART가
# 기대하는 조합이다.
act_fq = tq.FakeQuantize.with_args(
    observer=tq.MovingAverageMinMaxObserver, quant_min=0, quant_max=255,
    dtype=torch.quint8, qscheme=torch.per_tensor_affine, reduce_range=False)
wt_fq = tq.FakeQuantize.with_args(
    observer=tq.MovingAveragePerChannelMinMaxObserver, quant_min=-128, quant_max=127,
    dtype=torch.qint8, qscheme=torch.per_channel_symmetric, ch_axis=0)
qconfig = tq.QConfig(activation=act_fq, weight=wt_fq)
qconfig_mapping = tq.QConfigMapping().set_global(qconfig)
example = torch.randn(1, 3, a.size, a.size)
model = prepare_qat_fx(fp32.train(), qconfig_mapping, (example,)).to(dev)
n_fq = sum(1 for m in model.modules() if isinstance(m, tq.FakeQuantizeBase))
print(f"QAT 준비 완료 — FakeQuantize 노드 {n_fq}개")

opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
steps = a.epochs * len(tr_dl); warm = max(1, int(0.1 * steps))
sched = torch.optim.lr_scheduler.LambdaLR(
    opt, lambda s: s / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))

@torch.no_grad()
def evaluate(dl, present=ROAD_CLASSES):
    """fake-quant 상태로 평가한다 = 배포될 int8 모델의 정확도 추정."""
    model.eval(); C = len(ROAD_CLASSES); cm = torch.zeros(C, C, dtype=torch.long)
    for x, y in dl:
        x = x.to(dev, non_blocking=True)
        cm += torch.bincount(y * C + model(x).argmax(1).cpu(), minlength=C * C).view(C, C)
    model.train()
    acc = cm.diag().sum().item() / max(cm.sum().item(), 1)
    recall = (cm.diag() / cm.sum(1).clamp(min=1)).tolist()
    prec = (cm.diag() / cm.sum(0).clamp(min=1)).tolist()
    idx = [ROAD_CLASSES.index(c) for c in present]
    return {"acc": acc, "macro_recall": sum(recall[i] for i in idx) / len(idx),
            "recall": dict(zip(ROAD_CLASSES, recall)), "precision": dict(zip(ROAD_CLASSES, prec)),
            "cm": cm.tolist()}

CARLA_PRESENT = ("normal", "wet", "black_ice")
print("\n[기준] QAT 시작 전 (fake-quant 삽입 직후, 학습 0스텝)")
b_r = evaluate(rscd_te_dl); b_c = evaluate(carla_va_dl, CARLA_PRESENT)
print(f"  RSCD  acc {b_r['acc']:.4f} ice_recall {b_r['recall']['black_ice']:.4f}")
print(f"  CARLA acc {b_c['acc']:.4f} ice_recall {b_c['recall']['black_ice']:.4f}")
print("  ※ 이 값이 이미 낮으면 fake-quant 자체로 붕괴한 것 — PTQ 실패와 같은 상태다\n")

log = open(out / "qat_log.csv", "a", newline=""); w = csv.writer(log)
if log.tell() == 0:
    w.writerow(["epoch", "loss", "rscd_acc", "rscd_ice", "carla_acc", "carla_ice", "score", "elapsed_s"])
best, t0 = -1e9, time.time()
for ep in range(a.epochs):
    if ep >= a.freeze_observer_epoch:
        # 스케일을 고정하고 남은 에포크는 그 스케일에 가중치를 적응시킨다.
        model.apply(tq.disable_observer)
        try:    # BN 통계도 같이 고정한다 (스케일만 고정하고 BN이 움직이면 어긋난다)
            from torch.ao.nn.intrinsic.qat import freeze_bn_stats
            model.apply(freeze_bn_stats)
        except Exception:
            pass
        print(f"  (ep{ep}) 관측자·BN 통계 정지 — 스케일 고정")
    run, n = 0.0, 0
    for x, y in tr_dl:
        x = x.to(dev, non_blocking=True); y = y.to(dev, non_blocking=True)
        loss = F.cross_entropy(model(x), y, label_smoothing=0.05)
        opt.zero_grad(set_to_none=True); loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5.0); opt.step(); sched.step()
        run += loss.item(); n += 1
        if n % 100 == 0:
            print(f"  ep{ep} step{n}/{len(tr_dl)} loss {run/n:.4f} {time.time()-t0:.0f}s", flush=True)
    rv = evaluate(rscd_te_dl); cv = evaluate(carla_va_dl, CARLA_PRESENT)
    score = rv["acc"] + 0.5 * cv["recall"]["black_ice"]
    print(f"== ep{ep} loss {run/max(n,1):.4f} | RSCD acc {rv['acc']:.4f} ice {rv['recall']['black_ice']:.4f} "
          f"| CARLA acc {cv['acc']:.4f} ice {cv['recall']['black_ice']:.4f} | score {score:.4f}", flush=True)
    w.writerow([ep, f"{run/max(n,1):.4f}", f"{rv['acc']:.4f}", f"{rv['recall']['black_ice']:.4f}",
                f"{cv['acc']:.4f}", f"{cv['recall']['black_ice']:.4f}", f"{score:.4f}",
                f"{time.time()-t0:.0f}"]); log.flush()
    torch.save({"model": model.state_dict(), "epoch": ep, "rscd": rv, "carla": cv, "args": vars(a)},
               out / "last.pt")
    if score > best:
        best = score
        torch.save({"model": model.state_dict(), "epoch": ep, "rscd": rv, "carla": cv, "args": vars(a)},
                   out / "best.pt")
        best_rv, best_cv, best_ep = rv, cv, ep

# ---- QDQ ONNX export -------------------------------------------------------
model.load_state_dict(torch.load(out / "best.pt", map_location=dev)["model"])
model.eval().apply(tq.disable_observer)          # 스케일 고정 상태로 내보낸다
onnx_path = out / "roadnet_qat_qdq.onnx"
torch.onnx.export(model.cpu(), (example,), str(onnx_path), opset_version=13,
                  input_names=["image"], output_names=["logits"], dynamo=False)
print(f"\nQDQ ONNX: {onnx_path}")

import onnx
m = onnx.load(str(onnx_path))
kinds = {}
for node in m.graph.node:
    kinds[node.op_type] = kinds.get(node.op_type, 0) + 1
qdq = kinds.get("QuantizeLinear", 0) + kinds.get("DequantizeLinear", 0)
print(f"ONNX 노드: 총 {len(m.graph.node)}개, QuantizeLinear {kinds.get('QuantizeLinear',0)} / "
      f"DequantizeLinear {kinds.get('DequantizeLinear',0)}")
if qdq == 0:
    print("※ QDQ 노드가 0개다 — export가 fake-quant를 반영하지 못했다. NPU 매핑이 안 된다")

res = {"baseline_fakequant": {"rscd": b_r, "carla": b_c},
       "final": {"rscd": best_rv, "carla": best_cv, "epoch": best_ep},
       "onnx_nodes": kinds, "carla_val_keys": va_keys, "args": vars(a)}
(out / "metrics.json").write_text(json.dumps(res, indent=1))

print("\n=== QAT 결과 ===")
print(f"RSCD  acc              : {b_r['acc']:.4f} (fake-quant 직후) → {best_rv['acc']:.4f}   [FP32 v2 = 0.8868, PTQ 실패 = 0.48]")
print(f"RSCD  black_ice recall : {b_r['recall']['black_ice']:.4f} → {best_rv['recall']['black_ice']:.4f}   [FP32 v2 = 0.9800]")
print(f"CARLA black_ice recall : {b_c['recall']['black_ice']:.4f} → {best_cv['recall']['black_ice']:.4f}   [FP32 v2 = 0.6424]")
