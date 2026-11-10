"""Métriques communes à tous les modèles, pour une comparaison équitable."""

import numpy as np
from sklearn.metrics import f1_score, roc_auc_score

from toxicity.config import LABELS
from toxicity.scoring import toxicity_score

SCORE_THRESHOLD = 50  # un texte est considéré toxique si score >= 50


def compute_metrics(probs: np.ndarray, labels: np.ndarray) -> dict:
    """ROC-AUC par classe et moyenne, F1 binaire « toxique / non toxique » sur le score."""
    auc = {}
    for i, label in enumerate(LABELS):
        y = labels[:, i]
        # ROC-AUC indéfini si une seule classe présente (petits sous-échantillons).
        auc[label] = float(roc_auc_score(y, probs[:, i])) if 0 < y.sum() < len(y) else None

    valid = [v for v in auc.values() if v is not None]
    any_toxic = labels.max(axis=1)
    predicted = toxicity_score(probs) >= SCORE_THRESHOLD
    return {
        "mean_roc_auc": float(np.mean(valid)) if valid else None,
        "roc_auc": auc,
        "f1_any_toxic": float(f1_score(any_toxic, predicted, zero_division=0)),
        "f1_per_class": {
            label: float(f1_score(labels[:, i], probs[:, i] >= 0.5, zero_division=0))
            for i, label in enumerate(LABELS)
        },
        "n_samples": int(len(labels)),
    }
