"""API FastAPI d'inférence de toxicité.

Lancement :
  uv run uvicorn api:app --reload
Exemple :
  curl -X POST http://127.0.0.1:8000/model -H "Content-Type: application/json" \\
       -d '{"text": "You are an idiot", "model": "bert"}'
Le texte est envoyé dans le corps (POST) et non dans l'URL, pour qu'il ne
se retrouve pas dans les logs d'accès, proxys ou historiques.
Documentation interactive : http://127.0.0.1:8000/docs

Variables d'environnement :
  PRELOAD_MODELS     modèles chargés au démarrage, séparés par des virgules (défaut : bert).
                     Évite que la première requête après un démarrage à froid paie le chargement.
  TORCH_NUM_THREADS  threads PyTorch ; à aligner sur le nombre de vCPU (défaut : torch décide).
"""

import os
from contextlib import asynccontextmanager
from threading import Lock

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from toxicity.config import ARTIFACTS_DIR, LABELS
from toxicity.models.base import ToxicityModel
from toxicity.models.registry import MODELS, get_model_class

DEFAULT_MODEL = "bert"
TOXIC_THRESHOLD = 70  # score "toxic" strictement supérieur à 70 -> toxique
MAX_TEXT_LENGTH = 5000  # caractères ; évite qu'un texte énorme sature le modèle
OTHER_LABELS = [label for label in LABELS if label != "toxic"]

_models: dict[str, ToxicityModel] = {}
_lock = Lock()


def load_model(name: str) -> ToxicityModel:
    """Charge le modèle à la première demande puis le garde en mémoire.
    Lève KeyError (modèle inconnu) ou FileNotFoundError (modèle non entraîné)."""
    if name not in MODELS:
        raise KeyError(f"Modèle inconnu '{name}'. Disponibles : {', '.join(MODELS)}")
    with _lock:
        if name not in _models:
            model_dir = ARTIFACTS_DIR / name
            if not model_dir.exists():
                raise FileNotFoundError(f"Modèle '{name}' non entraîné : lancez train.py --model {name}")
            _models[name] = get_model_class(name).load(model_dir)
        return _models[name]


@asynccontextmanager
async def lifespan(_: FastAPI):
    if threads := os.environ.get("TORCH_NUM_THREADS"):
        torch.set_num_threads(int(threads))
    # Un modèle demandé au préchargement mais absent fait échouer le démarrage :
    # mieux vaut un déploiement refusé qu'un service qui répond 503.
    for name in filter(None, (n.strip() for n in os.environ.get("PRELOAD_MODELS", DEFAULT_MODEL).split(","))):
        load_model(name)
    yield


app = FastAPI(title="Toxic comments detector", version="0.1.0", lifespan=lifespan)


class PredictionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH, description="Phrase à analyser")
    model: str = Field(DEFAULT_MODEL, description=f"Modèle : {', '.join(MODELS)}")


class Prediction(BaseModel):
    model: str
    text: str
    is_toxic: bool  # basé uniquement sur la classe "toxic" (score > 70)
    toxicity_score: float  # probabilité de la classe "toxic", entre 0 et 100
    classes: dict[str, float]  # scores 0-100 des autres classes


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "loaded_models": sorted(_models)}


@app.post("/model", response_model=Prediction)
def predict(req: PredictionRequest) -> Prediction:
    text, model = req.text, req.model
    try:
        clf = load_model(model)
    except KeyError as e:
        raise HTTPException(404, e.args[0]) from e
    except FileNotFoundError as e:
        raise HTTPException(503, str(e)) from e
    probs = dict(zip(LABELS, clf.predict_proba([text])[0]))
    toxic_score = 100 * float(probs["toxic"])
    return Prediction(
        model=model,
        text=text,
        is_toxic=toxic_score > TOXIC_THRESHOLD,
        toxicity_score=round(toxic_score, 1),
        classes={label: round(100 * float(probs[label]), 1) for label in OTHER_LABELS},
    )
