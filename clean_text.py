"""Nettoie le CSV anonymisé : emojis, ponctuation, casse, espaces.

Entrée  : data/train_anonymized.csv (colonne comment_text_anon, produite par anonymize.py)
Sortie  : data/train_clean.csv      (colonne comment_text_clean ajoutée, comment_text_anon conservée)

Les pseudonymes ([PERSON_1], [EMAIL_1]...) sont protégés : ni mis en minuscules,
ni débarrassés de leurs crochets.

Usage :
    python clean_text.py
"""

import re
import unicodedata
from pathlib import Path

import polars as pl

DATA_DIR = Path(__file__).parent / "data"
INPUT_PATH = DATA_DIR / "train_anonymized.csv"
OUTPUT_PATH = DATA_DIR / "train_clean.csv"

SRC_COL = "comment_text"
DST_COL = "comment_text"

# Pseudonymes produits par anonymize.py : à ne pas toucher
TAG = re.compile(r"\[[A-Z_]+_\d+\]")

EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"  # pictogrammes, émoticônes, transports, symboles
    "\U00002600-\U000027bf"  # symboles divers, dingbats
    "\U0001f1e6-\U0001f1ff"  # drapeaux
    "\U00002b00-\U00002bff"  # flèches et symboles divers
    "‍️︎"  # joiner et sélecteurs de variation
    "]+"
)
APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})
PUNCT = re.compile(r"[^\w\s']")  # tout sauf lettres, chiffres, espaces et apostrophes
EDGE_APOSTROPHE = re.compile(r"(?<![^\W_])'|'(?![^\W_])")  # apostrophe hors d'un mot ("don't" conservé)
SPACES = re.compile(r"\s+")


def clean_segment(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(APOSTROPHES)
    text = EMOJI.sub(" ", text)
    text = text.lower()
    text = PUNCT.sub(" ", text)
    return EDGE_APOSTROPHE.sub(" ", text)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    # On nettoie les morceaux entre pseudonymes et on remet les pseudonymes tels quels
    parts, last = [], 0
    for m in TAG.finditer(text):
        parts.append(clean_segment(text[last : m.start()]))
        parts.append(f" {m.group()} ")
        last = m.end()
    parts.append(clean_segment(text[last:]))
    return SPACES.sub(" ", "".join(parts)).strip()


def main() -> None:
    df = pl.read_csv(INPUT_PATH)
    cleaned = [clean_text(t) for t in df[SRC_COL].to_list()]
    df = df.with_columns(pl.Series(DST_COL, cleaned))
    df.write_csv(OUTPUT_PATH)

    empty = sum(1 for t in cleaned if not t)
    print(f"{df.height} commentaires nettoyés -> {OUTPUT_PATH}")
    print(f"Commentaires devenus vides : {empty}")


if __name__ == "__main__":
    main()
