"""Anonymise data/train.csv : remplace les données personnelles par des pseudonymes
cohérents au sein d'un même commentaire (ex: [PERSON_1], [PERSON_2], [EMAIL_1]).

- spaCy NER  : PERSON, ORG, FAC  -> anonymisés
               NORP, GPE, LOC    -> conservés (signal utile pour la détection de toxicité)
- Regex      : EMAIL, URL, IP_ADDRESS, PHONE, IBAN, CREDIT_CARD, ADDRESS, USERNAME -> anonymisés

Usage :
    python anonymize.py            # tout le fichier (cache si le résultat existe déjà)
    python anonymize.py 2000       # test rapide sur 2000 lignes (écrit un fichier d'échantillon)
"""

import re
import sys
import time
from collections import Counter
from pathlib import Path

import polars as pl
import spacy

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "test.csv"
OUTPUT_PATH = DATA_DIR / "test_anonymized.csv"
SAMPLE_PATH = DATA_DIR / "test_anonymized_sample.csv"

FORCE_RECOMPUTE = False  # True pour ignorer le cache
KEEP_ORIGINAL = False  # True pour garder comment_text (non anonymisé) dans le CSV de sortie

# Labels spaCy à anonymiser (NORP, GPE, LOC volontairement conservés)
SPACY_LABELS = {"PERSON", "FAC"}

STREET_SUFFIX = (
    r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Place|Pl|"
    r"Way|Square|Sq|Terrace|Ter|Highway|Hwy|Crescent|Cres|Close|Gardens|Park|Row"
)

# Regex : (label, pattern). Elles ont priorité sur spaCy en cas de chevauchement.
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("URL", re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    (
        "IP_ADDRESS",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"  # IPv4
            r"|(?<![\w:])(?:[A-Fa-f0-9]{0,4}:){2,7}[A-Fa-f0-9]{0,4}(?![\w:])"  # IPv6 (filtré ensuite)
        ),
    ),
    (
        "PHONE",
        re.compile(
            r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?"
            r"\d{2,4}[\s.-]\d{2,4}[\s.-]\d{2,4}(?:[\s.-]\d{2,4})?(?![\w.])"
        ),
    ),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    (
        "ADDRESS",
        re.compile(
            # "221B Baker Street", "12 Rue ..." n'est pas couvert (dataset anglophone)
            rf"\b\d{{1,5}}[A-Za-z]?\s+(?:[A-Z][\w'.-]*\s+){{1,4}}(?:{STREET_SUFFIX})\b\.?"
            r"|\bP\.?\s?O\.?\s?Box\s+\d+\b"  # boîte postale
            r"|\b[A-Z]{2}\s\d{5}(?:-\d{4})?\b"  # état US + ZIP (ex: "NY 10001")
            r"|\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b",  # code postal UK
        ),
    ),
    (
        "USERNAME",
        re.compile(
            r"(?<!\w)@\w{2,}"  # @mention
            r"|\bUser(?:[ _]talk)?:[^\s|\]/#]+",  # liens Wikipedia User:Nom / User talk:Nom
            re.IGNORECASE,
        ),
    ),
]

ISO_DATE = re.compile(r"^\d{4}[-.]\d{1,2}[-.]\d{1,2}$|^\d{1,2}[-.]\d{1,2}[-.]\d{2,4}$")


def is_valid_match(label: str, value: str) -> bool:
    """Filtre les faux positifs des regex trop larges (dates, heures)."""
    if label == "PHONE":
        return sum(c.isdigit() for c in value) >= 7 and not ISO_DATE.match(value.strip())
    if label == "IP_ADDRESS" and ":" in value:
        # évite "21:51:03" et l'indentation wiki "::" / "::Dead"
        groups = [g for g in value.split(":") if g]
        return len(groups) >= 7 or ("::" in value and len(groups) >= 2)
    return True


def regex_spans(text: str) -> list[tuple[int, int, str, int]]:
    spans = []
    for label, pattern in PII_PATTERNS:
        for m in pattern.finditer(text):
            if is_valid_match(label, m.group()):
                spans.append((m.start(), m.end(), label, 0))  # priorité 0 = regex
    return spans


def resolve_overlaps(spans: list[tuple[int, int, str, int]]) -> list[tuple[int, int, str]]:
    """Garde les spans non chevauchants : priorité regex, puis les plus longs."""
    kept: list[tuple[int, int, str]] = []
    for start, end, label, _prio in sorted(spans, key=lambda s: (s[3], -(s[1] - s[0]), s[0])):
        if all(end <= k_start or start >= k_end for k_start, k_end, _ in kept):
            kept.append((start, end, label))
    return sorted(kept)


def anonymize_text(text: str, doc) -> tuple[str, Counter]:
    spans = regex_spans(text)
    spans += [
        (ent.start_char, ent.end_char, ent.label_, 1)
        for ent in doc.ents
        if ent.label_ in SPACY_LABELS
    ]
    kept = resolve_overlaps(spans)

    # Numérotation : même texte (insensible à la casse) -> même pseudonyme dans le commentaire
    ids: dict[tuple[str, str], int] = {}
    per_label: Counter = Counter()
    replacements = []
    for start, end, label in kept:
        key = (label, text[start:end].strip().lower())
        if key not in ids:
            per_label[label] += 1
            ids[key] = per_label[label]
        replacements.append((start, end, f"[{label}_{ids[key]}]"))

    # Remplacement de la fin vers le début pour ne pas décaler les indices
    out = text
    for start, end, tag in reversed(replacements):
        out = out[:start] + tag + out[end:]
    return out, Counter(label for _, _, label in kept)


def anonymize_all(texts: list[str], batch_size: int = 256) -> tuple[list[str], Counter]:
    nlp = spacy.load(
        "en_core_web_sm",
        disable=["tagger", "parser", "attribute_ruler", "lemmatizer"],
    )
    anonymized: list[str] = []
    totals: Counter = Counter()
    n = len(texts)
    start = time.time()
    for i, (text, doc) in enumerate(zip(texts, nlp.pipe(texts, batch_size=batch_size)), start=1):
        anon, counts = anonymize_text(text, doc)
        anonymized.append(anon)
        totals.update(counts)
        if i % 100 == 0 or i == n:
            elapsed = time.time() - start
            eta = elapsed / i * (n - i)
            print(
                f"\rLigne {i}/{n} ({i / n:.1%}) - {elapsed:.0f}s écoulées, ~{eta:.0f}s restantes",
                end="",
                flush=True,
            )
    print()
    return anonymized, totals


def residual_leaks(anonymized: list[str]) -> Counter:
    """Relance les regex sur le texte anonymisé : doit rester à 0 pour chaque label."""
    leaks: Counter = Counter()
    for text in anonymized:
        for label, pattern in PII_PATTERNS:
            leaks[label] += sum(is_valid_match(label, m.group()) for m in pattern.finditer(text))
    return leaks


def main(limit: int | None = None) -> None:
    output = SAMPLE_PATH if limit else OUTPUT_PATH

    if output.exists() and not limit and not FORCE_RECOMPUTE:
        print(f"Cache trouvé : {output} (pas de recalcul)")
        return

    df = pl.read_csv(CSV_PATH)
    if limit:
        df = df.head(limit)

    texts = df["comment_text"].fill_null("").to_list()
    anonymized, totals = anonymize_all(texts)

    # Le texte anonymisé remplace "comment_text" à la même place : le fichier garde
    # les colonnes du CSV source et est directement utilisable par train.py.
    original = df["comment_text"]
    df = df.with_columns(pl.Series("comment_text", anonymized))
    if KEEP_ORIGINAL:
        df = df.with_columns(original.alias("comment_text_original"))
    df.write_csv(output)

    print(f"{df.height} commentaires anonymisés -> {output}")
    print("Entités remplacées :")
    for label, n in totals.most_common():
        print(f"  {label:<12} {n}")

    leaks = residual_leaks(anonymized)
    print("Vérification des fuites résiduelles (regex relancées sur le texte anonymisé) :")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(leaks.items())))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
