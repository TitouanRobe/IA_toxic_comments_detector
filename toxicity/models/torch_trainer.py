"""Boucle d'entraînement PyTorch générique, partagée par BERT et (plus tard) le LSTM.

Chaque modèle fournit :
  - un nn.Module ;
  - une fonction `collate(list[(texte, labels)]) -> dict` qui produit le batch ;
  - une fonction `forward(module, batch) -> logits (B, n_labels)`.
"""

import time
from collections.abc import Callable

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from transformers import get_linear_schedule_with_warmup

from toxicity.config import TrainConfig
from toxicity.data import Split
from toxicity.device import DeviceInfo

Collate = Callable[[list[tuple[str, np.ndarray]]], dict]
Forward = Callable[[nn.Module, dict], torch.Tensor]


class TextDataset(Dataset):
    def __init__(self, split: Split):
        self.split = split

    def __len__(self) -> int:
        return len(self.split)

    def __getitem__(self, i: int) -> tuple[str, np.ndarray]:
        return self.split.texts[i], self.split.labels[i]


def make_loader(
    split: Split, collate: Collate, batch_size: int, dev: DeviceInfo, cfg: TrainConfig, shuffle: bool
) -> DataLoader:
    return DataLoader(
        TextDataset(split),
        batch_size=batch_size,
        shuffle=shuffle,
        collate_fn=collate,
        num_workers=cfg.num_workers,
        pin_memory=dev.pin_memory,
        persistent_workers=cfg.num_workers > 0,
    )


def compute_pos_weight(labels: np.ndarray, cap: float) -> torch.Tensor:
    """Poids des positifs = n_neg / n_pos par classe, plafonné (classes rares)."""
    pos = labels.sum(axis=0)
    neg = len(labels) - pos
    weight = np.where(pos > 0, neg / np.maximum(pos, 1), 1.0)
    return torch.tensor(np.clip(weight, 1.0, cap), dtype=torch.float32)


def _to_device(batch: dict, dev: DeviceInfo) -> dict:
    return {
        k: v.to(dev.device, non_blocking=dev.pin_memory) if torch.is_tensor(v) else v
        for k, v in batch.items()
    }


def _progress(prefix: str, i: int, total: int, start: float, extra: str = "") -> None:
    elapsed = time.time() - start
    eta = elapsed / i * (total - i)
    print(
        f"\r{prefix} {i}/{total} ({i / total:.1%}) - {elapsed:.0f}s écoulées, ~{eta:.0f}s restantes{extra}",
        end="",
        flush=True,
    )


@torch.no_grad()
def predict(
    module: nn.Module, loader: DataLoader, dev: DeviceInfo, forward: Forward, desc: str = "Prédiction"
) -> np.ndarray:
    module.eval()
    outputs = []
    start = time.time()
    for i, batch in enumerate(loader, start=1):
        batch = _to_device(batch, dev)
        with dev.autocast():
            logits = forward(module, batch)
        outputs.append(torch.sigmoid(logits.float()).cpu().numpy())
        if i % 20 == 0 or i == len(loader):
            _progress(desc, i, len(loader), start)
    print()
    return np.concatenate(outputs) if outputs else np.empty((0, 0), dtype=np.float32)


def train(
    module: nn.Module,
    train_split: Split,
    val_split: Split,
    collate: Collate,
    forward: Forward,
    dev: DeviceInfo,
    cfg: TrainConfig,
    lr: float,
) -> dict:
    """Entraîne `module` en place et y recharge le meilleur checkpoint (mean ROC-AUC val)."""
    # Import local : scikit-learn n'est installé que pour l'entraînement (groupe "train").
    from toxicity.metrics import compute_metrics

    batch_size = cfg.batch_size or dev.default_batch_size
    train_loader = make_loader(train_split, collate, batch_size, dev, cfg, shuffle=True)
    val_loader = make_loader(val_split, collate, batch_size * 2, dev, cfg, shuffle=False)

    module.to(dev.device)
    loss_fn = nn.BCEWithLogitsLoss(
        pos_weight=compute_pos_weight(train_split.labels, cfg.max_pos_weight).to(dev.device)
    )
    # Pas de weight decay sur les biais et les LayerNorm (pratique standard).
    decay, no_decay = [], []
    for name, p in module.named_parameters():
        if p.requires_grad:
            (no_decay if p.ndim < 2 else decay).append(p)
    optimizer = torch.optim.AdamW(
        [{"params": decay, "weight_decay": cfg.weight_decay}, {"params": no_decay, "weight_decay": 0.0}],
        lr=lr,
    )
    total_steps = len(train_loader) * cfg.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, int(cfg.warmup_ratio * total_steps), total_steps
    )

    history, best_auc, best_state = [], -1.0, None
    for epoch in range(1, cfg.epochs + 1):
        module.train()
        running, start = 0.0, time.time()
        for i, batch in enumerate(train_loader, start=1):
            batch = _to_device(batch, dev)
            with dev.autocast():
                logits = forward(module, batch)
            loss = loss_fn(logits.float(), batch["labels"])

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(module.parameters(), cfg.grad_clip)
            optimizer.step()
            scheduler.step()

            running += loss.item()
            if i % 20 == 0 or i == len(train_loader):
                _progress(f"Epoch {epoch}/{cfg.epochs}", i, len(train_loader), start, f" - loss {running / i:.4f}")
        print()

        val_probs = predict(module, val_loader, dev, forward, desc="Validation")
        metrics = compute_metrics(val_probs, val_split.labels)
        auc = metrics["mean_roc_auc"] or 0.0
        history.append({"epoch": epoch, "train_loss": running / len(train_loader), "val": metrics})
        print(f"Epoch {epoch} : val mean ROC-AUC = {auc:.4f}, F1 (score>=50) = {metrics['f1_any_toxic']:.4f}")

        if auc > best_auc:
            best_auc = auc
            best_state = {k: v.detach().cpu().clone() for k, v in module.state_dict().items()}

    if best_state is not None:
        module.load_state_dict(best_state)
    return {"history": history, "best_val_mean_roc_auc": best_auc}
