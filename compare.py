"""Compare les modèles entraînés (results/*/metrics.json) et désigne le meilleur.

Critère : mean ROC-AUC sur le jeu de test.
  uv run python compare.py
"""

import json

from toxicity.config import LABELS, RESULTS_DIR


def main() -> None:
    reports = [json.loads(p.read_text()) for p in sorted(RESULTS_DIR.glob("*/metrics.json"))]
    if not reports:
        raise SystemExit(f"Aucun résultat dans {RESULTS_DIR} : lancez d'abord train.py")

    reports.sort(key=lambda r: r["test"]["mean_roc_auc"] or 0.0, reverse=True)
    short = {"severe_toxic": "severe", "identity_hate": "id_hate"}
    cols = ["modèle", "AUC moy", "F1@50", *(short.get(l, l) for l in LABELS), "n_test", "train (s)"]
    print(" | ".join(f"{c:>9}" for c in cols))
    for r in reports:
        t = r["test"]
        fmt = lambda v: f"{v:.4f}" if v is not None else "n/a"  # noqa: E731
        row = [r["model"], fmt(t["mean_roc_auc"]), fmt(t["f1_any_toxic"]),
               *(fmt(t["roc_auc"][l]) for l in LABELS), t["n_samples"], r["train_seconds"]]
        print(" | ".join(f"{str(v):>9}" for v in row))

    sizes = {r["test"]["n_samples"] for r in reports}
    if len(sizes) > 1:
        print("\nAttention : les modèles n'ont pas été évalués sur le même nombre d'exemples de test.")
    print(f"\nMeilleur modèle : {reports[0]['model']}")


if __name__ == "__main__":
    main()
