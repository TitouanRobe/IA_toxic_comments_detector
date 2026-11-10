"""BiLSTM entraîné from scratch en classification multi-label (6 classes de toxicité).

Tokenisation simple (regex), vocabulaire construit sur le train uniquement,
puis même boucle d'entraînement que BERT (torch_trainer).
"""

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from toxicity.config import LABELS, TrainConfig
from toxicity.data import Split
from toxicity.device import get_device
from toxicity.models import torch_trainer
from toxicity.models.base import ToxicityModel

DEFAULT_LR = 1e-3
DEFAULT_BATCH_SIZE = 128  # modèle léger : de gros batchs accélèrent nettement l'entraînement
MAX_VOCAB = 50_000
MIN_FREQ = 2
EMBED_DIM = 300
HIDDEN_DIM = 128
NUM_LAYERS = 2
DROPOUT = 0.3

PAD, UNK = "<pad>", "<unk>"
PAD_ID, UNK_ID = 0, 1
_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def build_vocab(texts: list[str], max_size: int = MAX_VOCAB, min_freq: int = MIN_FREQ) -> dict[str, int]:
    counts = Counter(tok for text in texts for tok in tokenize(text))
    words = [w for w, c in counts.most_common(max_size - 2) if c >= min_freq]
    return {PAD: PAD_ID, UNK: UNK_ID, **{w: i for i, w in enumerate(words, start=2)}}


class LstmClassifier(nn.Module):
    """Embedding -> BiLSTM -> concat(max-pool, mean-pool) masqués -> 6 logits."""

    def __init__(self, vocab_size: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, EMBED_DIM, padding_idx=PAD_ID)
        self.embed_dropout = nn.Dropout1d(DROPOUT)  # dropout "spatial" : éteint des dimensions entières
        self.lstm = nn.LSTM(
            EMBED_DIM, HIDDEN_DIM, num_layers=NUM_LAYERS, batch_first=True,
            bidirectional=True, dropout=DROPOUT,
        )
        self.dropout = nn.Dropout(DROPOUT)
        self.head = nn.Linear(4 * HIDDEN_DIM, len(LABELS))

    def forward(self, input_ids: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        # LSTM en fp32 : la précision mixte n'apporte rien à cette taille et cuDNN gère mal le bf16.
        with torch.autocast(device_type=input_ids.device.type, enabled=False):
            x = self.embedding(input_ids).float()
            x = self.embed_dropout(x.transpose(1, 2)).transpose(1, 2)
            packed = pack_padded_sequence(x, lengths.cpu(), batch_first=True, enforce_sorted=False)
            out, _ = self.lstm(packed)
            out, _ = pad_packed_sequence(out, batch_first=True, total_length=input_ids.size(1))

            mask = (input_ids != PAD_ID).unsqueeze(-1)  # (B, T, 1)
            max_pool = out.masked_fill(~mask, float("-inf")).max(dim=1).values
            mean_pool = (out * mask).sum(dim=1) / lengths.clamp(min=1).unsqueeze(-1).to(out.dtype)
            return self.head(self.dropout(torch.cat([max_pool, mean_pool], dim=-1)))


class LstmToxicityModel(ToxicityModel):
    name = "lstm"

    def __init__(self, config: TrainConfig, vocab: dict[str, int] | None = None):
        if config.batch_size is None:
            config = replace(config, batch_size=DEFAULT_BATCH_SIZE)
        super().__init__(config)
        self.dev = get_device()
        self.vocab = vocab
        self.module = LstmClassifier(len(vocab)) if vocab is not None else None

    def _encode(self, text: str) -> list[int]:
        ids = [self.vocab.get(tok, UNK_ID) for tok in tokenize(text)[: self.config.max_length]]
        return ids or [UNK_ID]  # texte vide : au moins un token pour le LSTM

    # Padding dynamique à la longueur max du batch.
    def _collate(self, items: list[tuple[str, np.ndarray]]) -> dict:
        texts, labels = zip(*items)
        encoded = [self._encode(t) for t in texts]
        lengths = torch.tensor([len(ids) for ids in encoded], dtype=torch.long)
        input_ids = torch.full((len(encoded), int(lengths.max())), PAD_ID, dtype=torch.long)
        for i, ids in enumerate(encoded):
            input_ids[i, : len(ids)] = torch.tensor(ids, dtype=torch.long)
        return {"input_ids": input_ids, "lengths": lengths, "labels": torch.from_numpy(np.stack(labels))}

    @staticmethod
    def _forward(module: nn.Module, batch: dict) -> torch.Tensor:
        return module(batch["input_ids"], batch["lengths"])

    def fit(self, train: Split, val: Split) -> dict:
        self.vocab = build_vocab(train.texts)  # train uniquement : pas de fuite val/test
        print(f"Vocabulaire : {len(self.vocab)} tokens")
        self.module = LstmClassifier(len(self.vocab))
        info = torch_trainer.train(
            self.module, train, val, self._collate, self._forward,
            self.dev, self.config, lr=self.config.lr or DEFAULT_LR,
        )
        return {"vocab_size": len(self.vocab), **info}

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        if self.module is None:
            raise RuntimeError("Modèle non entraîné : appelez fit() ou load() d'abord.")
        self.module.to(self.dev.device)
        dummy = Split(texts=texts, labels=np.zeros((len(texts), len(LABELS)), dtype=np.float32))
        loader = torch_trainer.make_loader(
            dummy, self._collate, self.config.batch_size * 2, self.dev, self.config, shuffle=False
        )
        return torch_trainer.predict(self.module, loader, self.dev, self._forward)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        torch.save(self.module.state_dict(), directory / "model.pt")
        (directory / "vocab.json").write_text(json.dumps(self.vocab, ensure_ascii=False), encoding="utf-8")
        (directory / "train_config.json").write_text(json.dumps(self.config.to_dict(), indent=2))

    @classmethod
    def load(cls, directory: Path) -> "LstmToxicityModel":
        config = TrainConfig(**json.loads((directory / "train_config.json").read_text()))
        vocab = json.loads((directory / "vocab.json").read_text(encoding="utf-8"))
        model = cls(config, vocab=vocab)
        model.module.load_state_dict(torch.load(directory / "model.pt", map_location="cpu"))
        return model
