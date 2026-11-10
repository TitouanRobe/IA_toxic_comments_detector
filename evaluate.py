"""Évalue les modèles déjà entraînés (artifacts/<modèle>/) sur un jeu de test, sans réentraîner.

Par défaut : data/test_clean.csv (test anonymisé + nettoyé, voir clean_text.py --split test).
Écrit results/<modèle>/metrics_<nom du csv>.json et affiche un tableau comparatif.

Exemples :
  uv run python evaluate.py                         # tous les modèles entraînés, sur test_clean.csv
  uv run python evaluate.py --models bert lstm
  uv run python evaluate.py --test-csv data/test.csv --limit 2000
"""

import argparse
import json
import time
from pathlib import Path

from toxicity.config import ARTIFACTS_DIR, LABELS, RESULTS_DIR, TEST_CLEAN_CSV
from toxicity.data import load_test
from toxicity.device import describe, get_device
from toxicity.metrics import compute_metrics
from toxicity.models.registry import MODELS, get_model_class


def fmt(value, digits: int = 4) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "n/a"


def print_table(cols: list[str], rows: list[list], width: int = 10) -> None:
    print(" | ".join(f"{c:>{width}}" for c in cols))
    print("-+-".join("-" * width for _ in cols))
    for row in rows:
        print(" | ".join(f"{str(v):>{width}}" for v in row))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+", choices=list(MODELS), default=None,
                   help="défaut : tous les modèles présents dans artifacts/")
    p.add_argument("--test-csv", type=Path, default=TEST_CLEAN_CSV)
    p.add_argument("--limit", type=int, default=None, help="sous-échantillon du jeu de test")
    args = p.parse_args()

    if not args.test_csv.exists():
        raise SystemExit(f"{args.test_csv} introuvable : lancez d'abord `python clean_text.py --split test`")

    names = args.models or [m for m in MODELS if (ARTIFACTS_DIR / m).exists()]
    if not names:
        raise SystemExit(f"Aucun modèle entraîné dans {ARTIFACTS_DIR} : lancez d'abord train.py")

    test = load_test(args.limit, path=args.test_csv)
    device = describe(get_device())
    print(f"Jeu de test : {args.test_csv.name} ({len(test)} commentaires) | matériel : {device}\n")

    reports = []
    for name in names:
        model_dir = ARTIFACTS_DIR / name
        if not model_dir.exists():
            print(f"[{name}] ignoré : aucun modèle dans {model_dir}")
            continue
        print(f"[{name}] chargement + inférence...")
        try:
            model = get_model_class(name).load(model_dir)
            start = time.time()
            probs = model.predict_proba(test.texts)
            seconds = time.time() - start
        except Exception as e:  # artefact incomplet / corrompu : on passe au modèle suivant
            print(f"[{name}] ignoré : {type(e).__name__}: {e}")
            continue

        report = {
            "model": name,
            "device": device,
            "test_csv": args.test_csv.name,
            "inference_seconds": round(seconds, 2),
            "ms_per_comment": round(1000 * seconds / max(len(test), 1), 3),
            "test": compute_metrics(probs, test.labels),
        }
        out = RESULTS_DIR / name / f"metrics_{args.test_csv.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"[{name}] AUC moy = {fmt(report['test']['mean_roc_auc'])} -> {out}")
        reports.append(report)

        del model  # libère la mémoire GPU avant le modèle suivant
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    if not reports:
        return
    reports.sort(key=lambda r: r["test"]["mean_roc_auc"] or 0.0, reverse=True)

    print(f"\nPerformances sur {args.test_csv.name} (décision : toxique si score >= 50)\n")
    print_table(
        ["modèle", "AUC moy", "précision", "rappel", "F1@50", "ms/comm.", "n_test"],
        [
            [r["model"], fmt(r["test"]["mean_roc_auc"]), fmt(r["test"]["precision_any_toxic"]),
             fmt(r["test"]["recall_any_toxic"]), fmt(r["test"]["f1_any_toxic"]),
             fmt(r["ms_per_comment"], 2), r["test"]["n_samples"]]
            for r in reports
        ],
    )
    short = {"severe_toxic": "severe", "identity_hate": "id_hate"}
    print("\nROC-AUC par classe\n")
    print_table(
        ["modèle", *(short.get(l, l) for l in LABELS)],
        [[r["model"], *(fmt(r["test"]["roc_auc"][l]) for l in LABELS)] for r in reports],
    )
    print(f"\nMeilleur modèle (mean ROC-AUC) : {reports[0]['model']}")


if __name__ == "__main__":
    main()
