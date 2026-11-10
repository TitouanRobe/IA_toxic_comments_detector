"""Métriques communes à tous les modèles, pour une comparaison équitable."""

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from toxicity.config import LABELS
from toxicity.scoring import toxicity_score

SCORE_THRESHOLD = 50  # un texte est considéré toxique si score >= 50


def _per_class(metric, probs: np.ndarray, labels: np.ndarray) -> dict:
    """Métrique binaire par classe, au seuil de probabilité 0.5."""
    return {
        label: float(metric(labels[:, i], probs[:, i] >= 0.5, zero_division=0))
        for i, label in enumerate(LABELS)
    }


def compute_metrics(probs: np.ndarray, labels: np.ndarray) -> dict:
    """ROC-AUC par classe et moyenne ; précision, rappel et F1 binaires « toxique / non toxique » sur le score."""
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
        "precision_any_toxic": float(precision_score(any_toxic, predicted, zero_division=0)),
        "recall_any_toxic": float(recall_score(any_toxic, predicted, zero_division=0)),
        "f1_any_toxic": float(f1_score(any_toxic, predicted, zero_division=0)),
        "precision_per_class": _per_class(precision_score, probs, labels),
        "recall_per_class": _per_class(recall_score, probs, labels),
        "f1_per_class": _per_class(f1_score, probs, labels),
        "n_samples": int(len(labels)),
    }
