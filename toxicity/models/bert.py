"""DistilBERT fine-tuné en classification multi-label (6 classes de toxicité)."""

import json
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from toxicity.config import LABELS, TrainConfig
from toxicity.data import Split
from toxicity.device import get_device
from toxicity.models import torch_trainer
from toxicity.models.base import ToxicityModel

PRETRAINED = "distilbert-base-uncased"
DEFAULT_LR = 2e-5


class BertToxicityModel(ToxicityModel):
    name = "bert"

    def __init__(self, config: TrainConfig, pretrained: str = PRETRAINED):
        super().__init__(config)
        self.dev = get_device()
        self.tokenizer = AutoTokenizer.from_pretrained(pretrained)
        self.module = AutoModelForSequenceClassification.from_pretrained(
            pretrained,
            num_labels=len(LABELS),
            problem_type="multi_label_classification",
            id2label=dict(enumerate(LABELS)),
            label2id={label: i for i, label in enumerate(LABELS)},
        )

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
        batch["labels"] = torch.from_numpy(np.stack(labels))
        return batch

    @staticmethod
    def _forward(module: torch.nn.Module, batch: dict) -> torch.Tensor:
        return module(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits

    def fit(self, train: Split, val: Split) -> dict:
        return torch_trainer.train(
            self.module, train, val, self._collate, self._forward,
            self.dev, self.config, lr=self.config.lr or DEFAULT_LR,
        )

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        self.module.to(self.dev.device)
        dummy = Split(texts=texts, labels=np.zeros((len(texts), len(LABELS)), dtype=np.float32))
        batch_size = (self.config.batch_size or self.dev.default_batch_size) * 2
        loader = torch_trainer.make_loader(dummy, self._collate, batch_size, self.dev, self.config, shuffle=False)
        return torch_trainer.predict(self.module, loader, self.dev, self._forward)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.module.save_pretrained(directory)
        self.tokenizer.save_pretrained(directory)
        (directory / "train_config.json").write_text(json.dumps(self.config.to_dict(), indent=2))

    @classmethod
    def load(cls, directory: Path) -> "BertToxicityModel":
        config = TrainConfig(**json.loads((directory / "train_config.json").read_text()))
        return cls(config, pretrained=str(directory))
