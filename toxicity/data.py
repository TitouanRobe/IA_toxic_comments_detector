"""Chargement des données Jigsaw et découpage train / validation / test."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.model_selection import train_test_split

from toxicity.config import LABELS, SEED, TEST_CSV, TEST_LABELS_CSV, TEXT_COL, TRAIN_CSV


@dataclass
class Split:
    texts: list[str]
    labels: np.ndarray  # (n, len(LABELS)), float32 0/1

    def __len__(self) -> int:
        return len(self.texts)


def _to_split(df: pl.DataFrame) -> Split:
    return Split(
        texts=df[TEXT_COL].fill_null("").to_list(),
        labels=df.select(LABELS).to_numpy().astype(np.float32),
    )


def _subsample(df: pl.DataFrame, limit: int | None, seed: int) -> pl.DataFrame:
    if limit and limit < df.height:
        return df.sample(n=limit, seed=seed, shuffle=True)
    return df


def load_train_val(
    val_ratio: float = 0.1, limit: int | None = None, seed: int = SEED
) -> tuple[Split, Split]:
    """train.csv découpé en train/validation, stratifié sur 'toxic'."""
    df = _subsample(pl.read_csv(TRAIN_CSV), limit, seed)
    idx = np.arange(df.height)
    train_idx, val_idx = train_test_split(
        idx, test_size=val_ratio, random_state=seed, stratify=df["toxic"].to_numpy()
    )
    return _to_split(df[train_idx]), _to_split(df[val_idx])


def load_test(limit: int | None = None, seed: int = SEED, path: Path = TEST_CSV) -> Split:
    """test (texte) + test_labels.csv ; les lignes notées -1 (non évaluées) sont exclues."""
    df = pl.read_csv(path).select("id", TEXT_COL).join(pl.read_csv(TEST_LABELS_CSV), on="id", how="inner")
    df = df.filter(pl.col("toxic") != -1)
    return _to_split(_subsample(df, limit, seed))
