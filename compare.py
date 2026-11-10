"""Compare les modèles entraînés (results/*/metrics.json) et désigne le meilleur.

Tableau 1 : ROC-AUC moyenne, précision / rappel / F1 de la décision « toxique si score >= 50 »,
            temps de traitement (ms par commentaire, inférence sur le jeu de test) et d'entraînement.
Tableau 2 : ROC-AUC par classe.
Critère du meilleur modèle : mean ROC-AUC sur le jeu de test.
  uv run python compare.py
"""

import json

from toxicity.config import LABELS, RESULTS_DIR


def fmt(value, digits: int = 4) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "n/a"


def print_table(cols: list[str], rows: list[list], width: int = 10) -> None:
    print(" | ".join(f"{c:>{width}}" for c in cols))
    print("-+-".join("-" * width for _ in cols))
    for row in rows:
        print(" | ".join(f"{str(v):>{width}}" for v in row))


def main() -> None:
    reports = [json.loads(p.read_text()) for p in sorted(RESULTS_DIR.glob("*/metrics.json"))]
    if not reports:
        raise SystemExit(f"Aucun résultat dans {RESULTS_DIR} : lancez d'abord train.py")

    reports.sort(key=lambda r: r["test"]["mean_roc_auc"] or 0.0, reverse=True)

    print("Performances et temps de traitement (décision : toxique si score >= 50)\n")
    print_table(
        ["modèle", "AUC moy", "précision", "rappel", "F1@50", "ms/comm.", "train (s)", "n_test"],
        [
            [
                r["model"],
                fmt(r["test"]["mean_roc_auc"]),
                fmt(r["test"].get("precision_any_toxic")),
                fmt(r["test"].get("recall_any_toxic")),
                fmt(r["test"]["f1_any_toxic"]),
                fmt(r.get("ms_per_comment"), 2),
                fmt(r["train_seconds"], 1),
                r["test"]["n_samples"],
            ]
            for r in reports
        ],
    )

    short = {"severe_toxic": "severe", "identity_hate": "id_hate"}
    print("\nROC-AUC par classe\n")
    print_table(
        ["modèle", *(short.get(l, l) for l in LABELS)],
        [[r["model"], *(fmt(r["test"]["roc_auc"][l]) for l in LABELS)] for r in reports],
    )

    notes = []
    if len({r["test"]["n_samples"] for r in reports}) > 1:
        notes.append("les modèles n'ont pas été évalués sur le même nombre d'exemples de test.")
    if len({r.get("device") for r in reports}) > 1:
        notes.append("les modèles n'ont pas tourné sur le même matériel : les temps ne sont pas comparables.")
    if any("ms_per_comment" not in r for r in reports):
        notes.append("« n/a » = résultats antérieurs à la mesure de précision / rappel / temps : relancer train.py.")
    for note in notes:
        print(f"\nAttention : {note}")
    print(f"\nMeilleur modèle (mean ROC-AUC) : {reports[0]['model']}")


if __name__ == "__main__":
    main()
