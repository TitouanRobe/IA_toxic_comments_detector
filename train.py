"""Entraîne un modèle, l'évalue sur le jeu de test et sauvegarde poids + métriques.

Exemples :
  uv run python train.py --model bert                                   # entraînement complet (Windows / GPU)
  uv run python train.py --model bert --limit 2000 --epochs 1 --max-length 128 --test-limit 2000   # test rapide Mac
  uv run python train.py --model lstm --epochs 5                        # LSTM, entraînement complet
  uv run python train.py --model laya --epochs 2                        # LAYA (ModernBERT-large), GPU conseillé
"""

import argparse
import json
import random
import time

import numpy as np

from toxicity.config import ARTIFACTS_DIR, RESULTS_DIR, TrainConfig
from toxicity.data import load_test, load_train_val
from toxicity.device import describe, get_device
from toxicity.metrics import compute_metrics
from toxicity.models.registry import MODELS, get_model_class


def parse_args() -> TrainConfig:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults = TrainConfig()
    p.add_argument("--model", choices=list(MODELS), default=defaults.model)
    p.add_argument("--epochs", type=int, default=defaults.epochs)
    p.add_argument("--batch-size", type=int, default=None, help="défaut : 32 sur CUDA, 16 sur MPS/CPU")
    p.add_argument("--lr", type=float, default=None, help="défaut : propre à chaque modèle")
    p.add_argument("--max-length", type=int, default=defaults.max_length)
    p.add_argument("--limit", type=int, default=None, help="sous-échantillon de train.csv")
    p.add_argument("--test-limit", type=int, default=None, help="sous-échantillon du jeu de test")
    p.add_argument("--num-workers", type=int, default=defaults.num_workers)
    p.add_argument("--seed", type=int, default=defaults.seed)
    a = p.parse_args()
    return TrainConfig(
        model=a.model, epochs=a.epochs, batch_size=a.batch_size, lr=a.lr, max_length=a.max_length,
        limit=a.limit, test_limit=a.test_limit, num_workers=a.num_workers, seed=a.seed,
    )


def set_seed(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def main() -> None:
    cfg = parse_args()
    set_seed(cfg.seed)
    print(f"Modèle : {cfg.model} | matériel : {describe(get_device())}")

    train_split, val_split = load_train_val(cfg.val_ratio, cfg.limit, cfg.seed)
    test_split = load_test(cfg.test_limit, cfg.seed)
    print(f"Train : {len(train_split)} | Val : {len(val_split)} | Test : {len(test_split)}")

    model = get_model_class(cfg.model)(cfg)
    start = time.time()
    fit_info = model.fit(train_split, val_split)
    train_seconds = time.time() - start

    model_dir = ARTIFACTS_DIR / cfg.model
    model.save(model_dir)
    print(f"Modèle sauvegardé : {model_dir}")

    # Temps de traitement : inférence sur tout le jeu de test (tokenisation comprise).
    start = time.time()
    test_probs = model.predict_proba(test_split.texts)
    inference_seconds = time.time() - start
    ms_per_comment = 1000 * inference_seconds / max(len(test_split), 1)

    test_metrics = compute_metrics(test_probs, test_split.labels)
    print(f"Test : mean ROC-AUC = {test_metrics['mean_roc_auc']:.4f} | score>=50 : "
          f"précision = {test_metrics['precision_any_toxic']:.4f}, "
          f"rappel = {test_metrics['recall_any_toxic']:.4f}, F1 = {test_metrics['f1_any_toxic']:.4f}")
    print(f"Temps de traitement : {inference_seconds:.1f}s pour {len(test_split)} commentaires "
          f"({ms_per_comment:.2f} ms/commentaire)")

    results_dir = RESULTS_DIR / cfg.model
    results_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "model": cfg.model,
        "device": describe(get_device()),
        "config": cfg.to_dict(),
        "train_seconds": round(train_seconds, 1),
        "inference_seconds": round(inference_seconds, 2),
        "ms_per_comment": round(ms_per_comment, 3),
        "test": test_metrics,
        **fit_info,
    }
    (results_dir / "metrics.json").write_text(json.dumps(report, indent=2))
    print(f"Métriques : {results_dir / 'metrics.json'}")


if __name__ == "__main__":
    main()
