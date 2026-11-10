"""Constantes et configuration partagées par tous les modèles."""

from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ARTIFACTS_DIR = ROOT / "artifacts"  # poids des modèles entraînés
RESULTS_DIR = ROOT / "results"  # métriques, pour la comparaison

TRAIN_CSV = DATA_DIR / "train_clean.csv"
TEST_CSV = DATA_DIR / "test.csv"
TEST_CLEAN_CSV = DATA_DIR / "test_clean.csv"  # test anonymisé + nettoyé (clean_text.py --split test)
TEST_LABELS_CSV = DATA_DIR / "test_labels.csv"

TEXT_COL = "comment_text"
LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]

SEED = 42


@dataclass
class TrainConfig:
    """Hyperparamètres d'entraînement. Les champs à None prennent la valeur
    par défaut adaptée au matériel (voir device.py) ou au modèle."""

    model: str = "bert_clean"
    epochs: int = 2
    batch_size: int | None = None
    lr: float | None = None
    max_length: int = 256
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    grad_clip: float = 1.0
    max_pos_weight: float = 10.0
    val_ratio: float = 0.1
    limit: int | None = None  # sous-échantillon du train (tests rapides)
    test_limit: int | None = None
    num_workers: int = 0
    seed: int = SEED

    def to_dict(self) -> dict:
        return asdict(self)
