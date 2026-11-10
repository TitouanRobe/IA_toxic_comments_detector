"""Interface commune à tous les modèles (BERT, LSTM, LAYA...).

Un modèle reçoit des textes bruts et renvoie une probabilité par classe :
les scripts train.py / predict.py / compare.py n'ont pas besoin de savoir
s'il s'agit d'un réseau PyTorch ou d'un modèle scikit-learn.
"""

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from toxicity.config import TrainConfig
from toxicity.data import Split


class ToxicityModel(ABC):
    name: str  # clé dans le registre, et nom du dossier dans artifacts/ et results/

    def __init__(self, config: TrainConfig):
        self.config = config

    @abstractmethod
    def fit(self, train: Split, val: Split) -> dict:
        """Entraîne le modèle ; renvoie un historique (métriques par epoch, etc.)."""

    @abstractmethod
    def predict_proba(self, texts: list[str]) -> np.ndarray:
        """Probabilités (n, len(LABELS)) dans [0, 1]."""

    @abstractmethod
    def save(self, directory: Path) -> None: ...

    @classmethod
    @abstractmethod
    def load(cls, directory: Path) -> "ToxicityModel": ...
