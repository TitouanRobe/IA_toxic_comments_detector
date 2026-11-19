"""Configuration commune des tests : l'API est importée avec une clé de test connue et un modèle
factice (aucun poids à télécharger : les tests tournent tels quels dans la CI)."""

import hashlib
import os

import pytest

TEST_KEY = "cle-de-test-ci"

# Avant tout import de api.py : ces variables sont lues au chargement du module.
os.environ["API_KEY_HASHES"] = hashlib.sha256(TEST_KEY.encode()).hexdigest()
os.environ["LOG_PSEUDONYM_KEY"] = "cle-de-pseudonymisation-de-test"
os.environ["METRICS_PORT"] = "0"
os.environ["PRELOAD_MODELS"] = ""
os.environ.pop("AUTH_DISABLED", None)
os.environ.pop("ENABLE_DOCS", None)


class FakeModel:
    """Remplace BERT : « toxique » si le texte contient "idiot". Même interface que ToxicityModel."""

    def predict_proba(self, texts: list[str]) -> list[list[float]]:
        return [[0.95 if "idiot" in t.lower() else 0.05] + [0.01] * 5 for t in texts]


@pytest.fixture(scope="session")
def api_module():
    import api

    api._models["bert"] = FakeModel()
    return api


@pytest.fixture()
def client(api_module):
    from fastapi.testclient import TestClient

    return TestClient(api_module.app)


@pytest.fixture()
def auth() -> dict:
    return {"X-API-Key": TEST_KEY}
