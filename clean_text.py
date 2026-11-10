"""Nettoie le CSV anonymisé : emojis, ponctuation, casse, espaces.

Entrée  : data/<split>_anonymized.csv (colonne comment_text ou comment_text_anon, produite par anonymize.py)
Sortie  : data/<split>_clean.csv      (colonne comment_text = texte nettoyé)

Les pseudonymes ([PERSON_1], [EMAIL_1]...) sont protégés : ni mis en minuscules,
ni débarrassés de leurs crochets.

Usage :
    python clean_text.py                # train : train_anonymized.csv -> train_clean.csv
    python clean_text.py --split test   # test  : test_anonymized.csv  -> test_clean.csv
"""

import argparse
import re
import unicodedata
from pathlib import Path

import polars as pl

DATA_DIR = Path(__file__).parent / "data"
SRC_COLS = ("comment_text", "comment_text_anon")  # selon la version d'anonymize.py
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
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=["train", "test"], default="train")
    split = p.parse_args().split
    input_path = DATA_DIR / f"{split}_anonymized.csv"
    output_path = DATA_DIR / f"{split}_clean.csv"

    df = pl.read_csv(input_path)
    src_col = next((c for c in SRC_COLS if c in df.columns), None)
    if src_col is None:
        raise SystemExit(f"Aucune colonne texte ({', '.join(SRC_COLS)}) dans {input_path}")
    cleaned = [clean_text(t) for t in df[src_col].to_list()]
    df = df.drop(src_col).with_columns(pl.Series(DST_COL, cleaned))
    df.write_csv(output_path)

    empty = sum(1 for t in cleaned if not t)
    print(f"{df.height} commentaires nettoyés -> {output_path}")
    print(f"Commentaires devenus vides : {empty}")


if __name__ == "__main__":
    main()
