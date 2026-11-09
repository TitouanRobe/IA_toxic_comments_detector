"""Compte les entités nommées (spaCy) et les données personnelles RGPD (regex)
présentes dans data/test.csv.

- spaCy NER  : PERSON, ORG, GPE/LOC (lieux), FAC, NORP
- Regex      : EMAIL, IP_ADDRESS (v4/v6), PHONE, URL, IBAN, CREDIT_CARD, SSN, USERNAME_MENTION
"""

import re
import time
from collections import Counter
from pathlib import Path

import polars as pl
import spacy

CSV_PATH = Path(__file__).parent / "data" / "train.csv"
OUTPUT_PATH = Path(__file__).parent / "data" / "train_entities.csv"
TOTALS_PATH = Path(__file__).parent / "data" / "train_entities_totals.csv"
FORCE_RECOMPUTE = False  # True pour ignorer le cache et tout recalculer

# Labels spaCy utiles pour le RGPD (identité, organisation, localisation)
SPACY_LABELS = {"PERSON", "ORG", "GPE", "LOC", "FAC", "NORP"}

# Données personnelles que le NER ne détecte pas : on utilise des regex.
PII_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "IP_ADDRESS": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"  # IPv4
        r"|\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b"  # IPv6 (simplifié)
    ),
    "PHONE": re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?"
        r"\d{2,4}[\s.-]\d{2,4}[\s.-]\d{2,4}(?:[\s.-]\d{2,4})?(?![\w.])"
    ),
    "URL": re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
}


def regex_entities(text: str) -> Counter:
    counts: Counter = Counter()
    for label, pattern in PII_PATTERNS.items():
        n = len(pattern.findall(text))
        if n:
            counts[label] = n
    return counts


def extract_counts(texts: list[str], batch_size: int = 256, n_process: int = 1) -> list[Counter]:
    """Une Counter par texte : labels spaCy + labels regex."""
    # On ne garde que le NER : tagger/parser/lemmatizer ralentissent pour rien.
    nlp = spacy.load(
        "en_core_web_sm",
        disable=["tagger", "parser", "attribute_ruler", "lemmatizer"],
    )
    results = []
    total = len(texts)
    start = time.time()
    for i, (text, doc) in enumerate(
        zip(texts, nlp.pipe(texts, batch_size=batch_size, n_process=n_process)), start=1
    ):
        counts = Counter(ent.label_ for ent in doc.ents if ent.label_ in SPACY_LABELS)
        counts.update(regex_entities(text))
        results.append(counts)
        if i % 100 == 0 or i == total:
            elapsed = time.time() - start
            eta = elapsed / i * (total - i)
            print(
                f"\rLigne {i}/{total} ({i / total:.1%}) - {elapsed:.0f}s écoulées, ~{eta:.0f}s restantes",
                end="",
                flush=True,
            )
    print()
    return results


def main(limit: int | None = None) -> None:
    labels = sorted(SPACY_LABELS | set(PII_PATTERNS))

    if OUTPUT_PATH.exists() and not limit and not FORCE_RECOMPUTE:
        print(f"Cache trouvé, chargement de {OUTPUT_PATH} (pas de recalcul)")
        df = pl.read_csv(OUTPUT_PATH)
    else:
        df = pl.read_csv(CSV_PATH)
        if limit:
            df = df.head(limit)

        counters = extract_counts(df["comment_text"].fill_null("").to_list())

        entities_df = pl.DataFrame(
            {label: [c.get(label, 0) for c in counters] for label in labels},
            schema={label: pl.Int32 for label in labels},
        )
        df = pl.concat([df, entities_df], how="horizontal")
        df.write_csv(OUTPUT_PATH)
        print(f"Détail par commentaire : {OUTPUT_PATH}")

    # Total par classe
    totals = (
        df.select(pl.col(labels).sum())
        .unpivot(variable_name="label", value_name="total")
        .join(
            df.select([(pl.col(l) > 0).sum().alias(l) for l in labels]).unpivot(
                variable_name="label", value_name="n_comments"
            ),
            on="label",
        )
        .sort("total", descending=True)
    )
    print(f"{df.height} commentaires analysés")
    print(totals)
    totals.write_csv(TOTALS_PATH)
    print(f"Totaux par classe : {TOTALS_PATH}")


if __name__ == "__main__":
    main()
