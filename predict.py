"""Calcule le score de toxicité (0-100) d'un ou plusieurs textes.

Exemple :
  uv run python predict.py --model bert "You are an idiot" "Thanks for your help"
"""

import argparse
import json

from toxicity.config import ARTIFACTS_DIR
from toxicity.models.registry import MODELS, get_model_class
from toxicity.scoring import explain


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("texts", nargs="+")
    p.add_argument("--model", choices=list(MODELS), default="bert")
    args = p.parse_args()

    model_dir = ARTIFACTS_DIR / args.model
    if not model_dir.exists():
        raise SystemExit(f"Aucun modèle entraîné dans {model_dir} : lancez d'abord train.py --model {args.model}")

    model = get_model_class(args.model).load(model_dir)
    for text, probs in zip(args.texts, model.predict_proba(args.texts)):
        print(json.dumps({"text": text, **explain(probs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
