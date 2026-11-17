"""LAYA (convaiinnovations/laya) fine-tuné en classification multi-label (6 classes de toxicité).

LAYA = encodeur ModernBERT-large (395M, entraîné par RLCD) + une « tête de décision » pilotée par des
questions typées. On garde l'encodeur pré-entraîné et on remplace la tête par une couche linéaire à
6 sorties, fine-tunée avec la même boucle que BERT : la comparaison reste équitable.

Le checkpoint du Hub contient `encoder/config.json`, `tokenizer/` et un unique `model.safetensors`
dont les poids d'encodeur sont préfixés par `encoder.` ; le package `laya` n'est pas nécessaire.
"""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import snapshot_download
from safetensors import safe_open
from torch import nn
from transformers import AutoConfig, AutoModel, AutoTokenizer

from toxicity.config import LABELS, TrainConfig
from toxicity.data import Split
from toxicity.device import get_device
from toxicity.models import torch_trainer
from toxicity.models.base import ToxicityModel

REPO_ID = "convaiinnovations/laya"
DEFAULT_LR = 1e-5  # modèle « large » : taux plus prudent que pour DistilBERT
DEFAULT_BATCH_SIZE = 16  # 395M paramètres : 32 x 256 tokens est limite en mémoire
ENCODER_PREFIX = "encoder."


class LayaClassifier(nn.Module):
    """Encodeur LAYA -> token CLS (pooling utilisé par LAYA) -> 6 logits."""

    def __init__(self, encoder_config_dir: Path, dropout: float = 0.1):
        super().__init__()
        config = AutoConfig.from_pretrained(encoder_config_dir)
        config.reference_compile = False  # comme le chargeur officiel : pas de torch.compile implicite
        self.encoder = AutoModel.from_config(config, attn_implementation="sdpa")
        self.dropout = nn.Dropout(dropout)
        self.head = nn.Linear(config.hidden_size, len(LABELS))

    def load_laya_encoder(self, checkpoint: Path) -> None:
        """Charge les poids `encoder.*` du checkpoint LAYA ; la tête de décision d'origine est ignorée."""
        with safe_open(checkpoint, framework="pt") as f:
            state = {
                k.removeprefix(ENCODER_PREFIX): f.get_tensor(k).float()
                for k in f.keys()
                if k.startswith(ENCODER_PREFIX)
            }
        self.encoder.load_state_dict(state, strict=True)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        return self.head(self.dropout(hidden[:, 0]))


class LayaToxicityModel(ToxicityModel):
    name = "laya"

    def __init__(self, config: TrainConfig, source_dir: Path | None = None):
        """`source_dir` : dossier sauvegardé par `save()` ; None = checkpoint LAYA du Hub."""
        if config.batch_size is None:
            config = replace(config, batch_size=DEFAULT_BATCH_SIZE)
        super().__init__(config)
        self.dev = get_device()

        pretrained = source_dir is None
        if pretrained:
            source_dir = Path(snapshot_download(
                REPO_ID, allow_patterns=["encoder/*", "tokenizer/*", "model.safetensors"]
            ))
        self.source_dir = source_dir
        self.tokenizer = AutoTokenizer.from_pretrained(source_dir / "tokenizer")
        self.module = LayaClassifier(source_dir / "encoder")
        if pretrained:
            self.module.load_laya_encoder(source_dir / "model.safetensors")

    # Tokenisation à la volée, avec padding dynamique à la longueur max du batch.
    def _collate(self, items: list[tuple[str, np.ndarray]]) -> dict:
        texts, labels = zip(*items)
        batch = dict(
            self.tokenizer(
                list(texts),
                truncation=True,
                max_length=self.config.max_length,
                padding=True,
                return_tensors="pt",
            )
        )
        batch.pop("token_type_ids", None)
        batch["labels"] = torch.from_numpy(np.stack(labels))
        return batch

    @staticmethod
    def _forward(module: nn.Module, batch: dict) -> torch.Tensor:
        return module(batch["input_ids"], batch["attention_mask"])

    def fit(self, train: Split, val: Split) -> dict:
        return torch_trainer.train(
            self.module, train, val, self._collate, self._forward,
            self.dev, self.config, lr=self.config.lr or DEFAULT_LR,
        )

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        self.module.to(self.dev.device)
        dummy = Split(texts=texts, labels=np.zeros((len(texts), len(LABELS)), dtype=np.float32))
        loader = torch_trainer.make_loader(
            dummy, self._collate, self.config.batch_size * 2, self.dev, self.config, shuffle=False
        )
        return torch_trainer.predict(self.module, loader, self.dev, self._forward)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        torch.save(self.module.state_dict(), directory / "model.pt")
        (directory / "encoder").mkdir(exist_ok=True)
        shutil.copy(self.source_dir / "encoder" / "config.json", directory / "encoder" / "config.json")
        self.tokenizer.save_pretrained(directory / "tokenizer")
        (directory / "train_config.json").write_text(json.dumps(self.config.to_dict(), indent=2))

    @classmethod
    def load(cls, directory: Path) -> "LayaToxicityModel":
        config = TrainConfig(**json.loads((directory / "train_config.json").read_text()))
        model = cls(config, source_dir=directory)
        model.module.load_state_dict(torch.load(directory / "model.pt", map_location="cpu", weights_only=True))
        return model
