"""Contrôle de conformité IA Act de model_card.json (job « model-card » de la CI).

Échoue (code de sortie 1) si la model card :
  - n'a pas les champs exigés par le sujet du TP2 (étape 4), ou pas du bon type ;
  - déclare un niveau de risque inconnu, ou « inacceptable » (système interdit, art. 5 : ne doit
    pas être déployé) ;
  - déclare un biais sans analyse, ou un niveau « élevé » sans surveillance humaine décrite ;
  - a une date de mise à jour invalide ou dans le futur ;
  - cite des documents qui n'existent pas dans le dépôt.

Sans dépendance : python3 ci/check_model_card.py [chemin]   (défaut : model_card.json)
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = {  # champs du modèle donné dans le sujet (étape 4)
    "model_name": str, "description": str, "risk_level": str, "data_source": str,
    "privacy_measures": list, "bias_detected": bool, "version": str, "last_update": str,
}
RISK_LEVELS = {"minimal", "limité", "élevé", "inacceptable"}


def check(card: dict) -> list[str]:
    errors = []
    for key, typ in REQUIRED.items():
        if key not in card:
            errors.append(f"champ obligatoire absent : {key}")
        elif not isinstance(card[key], typ) or (typ is not bool and not card[key]):
            errors.append(f"champ {key} : {typ.__name__} non vide attendu")
    level = card.get("risk_level")
    if level not in RISK_LEVELS:
        errors.append(f"risk_level « {level} » inconnu (attendu : {', '.join(sorted(RISK_LEVELS))})")
    if level == "inacceptable":
        errors.append("risk_level inacceptable : pratique interdite (art. 5), déploiement refusé")
    if not card.get("risk_justification"):
        errors.append("risk_justification absente : le niveau de risque doit être justifié")
    if level == "élevé" and not card.get("human_oversight"):
        errors.append("risque élevé sans surveillance humaine décrite (art. 14)")
    if card.get("bias_detected") is True and not card.get("bias_analysis"):
        errors.append("bias_detected = true sans bias_analysis (méthode, résultats, mesures)")
    try:
        if date.fromisoformat(str(card.get("last_update"))) > date.today():
            errors.append("last_update dans le futur")
    except ValueError:
        errors.append("last_update : date AAAA-MM-JJ attendue")
    for doc in card.get("related_documents", []):
        if not (ROOT / doc).exists():
            errors.append(f"document cité introuvable : {doc}")
    return errors


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "model_card.json"
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ÉCHEC : {path} illisible ({e})")
        return 1
    errors = check(card)
    for e in errors:
        print(f"ÉCHEC : {e}")
    if errors:
        return 1
    print(f"OK : {path.name} conforme ({card['model_name']} v{card['version']}, "
          f"risque {card['risk_level']}, mise à jour {card['last_update']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
