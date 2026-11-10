"""Conversion des probabilités par classe en score de toxicité 0-100."""

import numpy as np

from toxicity.config import LABELS


def toxicity_score(probs: np.ndarray) -> np.ndarray:
    """Score = 100 x probabilité maximale parmi les 6 classes. probs : (n, 6)."""
    return np.rint(100 * probs.max(axis=1)).astype(int)


def explain(probs_row: np.ndarray) -> dict:
    """Score global + détail par classe (0-100) pour un seul texte."""
    return {
        "score": int(round(100 * float(probs_row.max()))),
        "classes": {label: round(100 * float(p), 1) for label, p in zip(LABELS, probs_row)},
    }
