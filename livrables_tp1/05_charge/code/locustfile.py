"""Test de charge de l'API (Locust). Réglages et scénarios : loadtest/config.toml.

Chaque utilisateur virtuel envoie en boucle un texte à POST /model, avec une pause aléatoire
entre deux requêtes ([users] dans la config).

Lancement normal : uv run python loadtest/run.py <scénario> (voir run.py).
Sans scénario (variable SCENARIO absente) : mode manuel, interface web http://localhost:8089 :
  uv run locust -f loadtest/locustfile.py
"""

import os
import random
import tomllib
from pathlib import Path

from locust import HttpUser, LoadTestShape, between, task

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((Path(__file__).parent / "config.toml").read_text(encoding="utf-8"))
MODEL = CONFIG["target"]["model"]
SCENARIO = os.environ.get("SCENARIO", "")


def _api_key() -> str:
    if key := os.environ.get("API_KEY", "").strip():
        return key
    key_file = ROOT / CONFIG["target"]["api_key_file"]
    if key_file.exists() and (key := key_file.read_text(encoding="utf-8").strip()):
        return key
    raise SystemExit(f"Clé d'API manquante : export API_KEY=dss_... ou l'écrire dans {key_file}")


API_KEY = _api_key()

# Textes de test génériques (aucune donnée personnelle), toxiques ou non, de longueurs variées :
# le temps d'inférence de BERT dépend de la longueur du texte.
_LONG = (
    "I have been reading the discussion on this article for a while and I think the recent "
    "edits removed a lot of useful context about the history of the topic. "
) * 12  # ~1 800 caractères
TEXTS = [
    "Thanks for your help with the article, much appreciated.",
    "I disagree with your edit, can we discuss it on the talk page?",
    "You are an idiot.",
    "This is the stupidest thing I have ever read, you moron.",
    "Great job on the cleanup, the references look much better now.",
    "Shut up and go away, nobody wants you here.",
    "Could you explain why the section about the history was removed?",
    "I will find you and make you regret this.",
    "What a pathetic loser, get a life.",
    "The sources cited here are outdated, I suggest updating them.",
    _LONG,
    _LONG + " Honestly whoever wrote this is a complete idiot.",
]


class ApiUser(HttpUser):
    host = CONFIG["target"]["host"]  # remplaçable par --host
    wait_time = between(CONFIG["users"]["wait_min"], CONFIG["users"]["wait_max"])

    @task
    def score_text(self) -> None:
        with self.client.post(
            "/model",
            json={"text": random.choice(TEXTS), "model": MODEL},
            headers={"X-API-Key": API_KEY},
            name="POST /model",
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                # 429 = Cloud Armor (limite de débit), 502/503 = pod indisponible.
                r.failure(f"HTTP {r.status_code}")
                return
            # L'API ne renvoie plus que la décision (pas de score, contre l'extraction du modèle).
            is_toxic = r.json().get("is_toxic")
            if not isinstance(is_toxic, bool):
                r.failure(f"réponse invalide : is_toxic={is_toxic!r}")


if SCENARIO:
    if SCENARIO not in CONFIG["scenarios"]:
        raise SystemExit(f"Scénario inconnu '{SCENARIO}' : {', '.join(CONFIG['scenarios'])}")

    class ScenarioShape(LoadTestShape):
        """Fait varier le nombre d'utilisateurs selon les paliers du scénario (config.toml)."""

        stages = CONFIG["scenarios"][SCENARIO]["stages"]

        def tick(self):
            elapsed = self.get_run_time()
            for end, users, spawn_rate in self.stages:
                if elapsed < end:
                    return users, spawn_rate
            return None  # fin du scénario
