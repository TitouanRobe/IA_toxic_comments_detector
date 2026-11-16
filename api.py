"""API FastAPI d'inférence de toxicité.

Lancement (clé et empreinte générées par make_api_key.py) :
  API_KEY_HASHES=<empreinte> uv run uvicorn api:app --reload
Exemple :
  curl -X POST http://127.0.0.1:8000/model -H "Content-Type: application/json" \\
       -H "X-API-Key: <clé>" -d '{"text": "You are an idiot", "model": "bert"}'
Le texte est envoyé dans le corps (POST) et non dans l'URL, pour qu'il ne
se retrouve pas dans les logs d'accès, proxys ou historiques. Il n'est jamais stocké.
Documentation interactive : http://127.0.0.1:8000/docs (bouton "Authorize" pour la clé)

Authentification : header X-API-Key, obligatoire sur /model. /health reste public
(sondes Kubernetes et health check du load balancer).
L'API ne connaît que l'EMPREINTE SHA-256 des clés, jamais les clés elles-mêmes : une fuite de
la configuration ne donne aucune clé utilisable. Le client garde sa clé, l'API hache celle
qu'il envoie et compare.

Variables d'environnement :
  API_KEY_HASHES       empreintes SHA-256 (64 caractères hexadécimaux) des clés acceptées,
                       séparées par des virgules ou des retours à la ligne (une par client ;
                       plusieurs : rotation sans coupure). Sans empreinte, l'API refuse de démarrer.
  API_KEY_HASHES_FILE  fichier contenant les empreintes (prioritaire). Sur GKE, c'est le secret
                       Google Secret Manager monté dans le pod (voir k8s/toxic-api.yaml).
  LOG_PSEUDONYM_KEY(_FILE)  clé de pseudonymisation des logs, voir toxicity/audit_log.py.
  AUTH_DISABLED        "1" pour désactiver l'authentification (développement local uniquement).
  ALLOWED_MODELS     modèles servis, séparés par des virgules (défaut : tous) ; les autres -> 404.
  PRELOAD_MODELS     modèles chargés au démarrage, séparés par des virgules (défaut : bert).
                     Évite que la première requête après un démarrage à froid paie le chargement.
  TORCH_NUM_THREADS  threads PyTorch ; à aligner sur le nombre de vCPU (défaut : torch décide).
  MAX_CONCURRENT_INFERENCES  inférences simultanées par pod (défaut : 1), les autres attendent.
  INFERENCE_QUEUE_TIMEOUT    attente max en file (s, défaut : 20) avant une réponse 503.
  METRICS_PORT       port des métriques Prometheus (défaut : 8081, 0 pour désactiver),
                     interne au cluster, voir toxicity/metrics_prom.py.
"""

import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from threading import BoundedSemaphore, Lock

import torch
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from toxicity import audit_log, metrics_prom
from toxicity.config import ARTIFACTS_DIR, LABELS
from toxicity.models.base import ToxicityModel
from toxicity.models.registry import MODELS, get_model_class

DEFAULT_MODEL = "bert"
TOXIC_THRESHOLD = 70  # score "toxic" strictement supérieur à 70 -> toxique
# Limite d'entrée (caractères). Les modèles tronquent à max_length = 256 tokens (~1200-1500
# caractères, voir toxicity/models/*.py) : au-delà, le texte est jeté par le tokenizer et
# n'influe pas sur le score. Accepter 5000 n'apportait donc rien au modèle tout en offrant une
# surface d'attaque (épuisement de ressources par entrées surdimensionnées, « sponge input » :
# réseau + parsing + tokenisation). 1500 couvre 96 % des commentaires réels (p95 du jeu de
# données = 1319 car.) et s'aligne sur ce que le modèle lit vraiment. La même limite est
# appliquée en amont, au load balancer (Cloud Armor), pour rejeter les corps trop gros avant
# qu'ils n'atteignent le pod : voir k8s/cloud-armor-setup.sh.
MAX_TEXT_LENGTH = 1500  # caractères
OTHER_LABELS = [label for label in LABELS if label != "toxic"]

_models: dict[str, ToxicityModel] = {}
_lock = Lock()

# File d'attente des inférences. Sans limite, FastAPI exécute jusqu'à 40 requêtes en parallèle
# qui se disputent les 2 CPU du pod : chacune devient lente et le débit total baisse. Avec une
# seule inférence à la fois (qui utilise déjà tous les threads PyTorch), les autres attendent
# leur tour et chaque requête passe plus vite.
MAX_CONCURRENT_INFERENCES = int(os.environ.get("MAX_CONCURRENT_INFERENCES", "1"))
# Au-delà de ce temps d'attente, la requête est refusée (503) au lieu d'attendre indéfiniment :
# le client peut réessayer, et un pod saturé ne s'enfonce pas (délestage de charge).
INFERENCE_QUEUE_TIMEOUT = float(os.environ.get("INFERENCE_QUEUE_TIMEOUT", "20"))
_inference_slots = BoundedSemaphore(MAX_CONCURRENT_INFERENCES)

# Textes de préchauffage : un court et un long (troncature à max_length), pour exercer tous les
# chemins du modèle avant de recevoir du trafic.
_WARMUP_TEXTS = ["Thanks for your help.", "This is a warm-up sentence for the model. " * 60]
log = audit_log.setup()

AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def hash_api_key(key: str) -> str:
    """Empreinte d'une clé. SHA-256 simple suffit : les clés sont aléatoires (256 bits),
    pas des mots de passe, donc impossibles à retrouver par force brute."""
    return hashlib.sha256(key.encode()).hexdigest()


def _read_api_key_hashes() -> list[str]:
    """Empreintes depuis API_KEY_HASHES_FILE (secret Secret Manager monté) ou API_KEY_HASHES."""
    raw = os.environ.get("API_KEY_HASHES", "")
    if path := os.environ.get("API_KEY_HASHES_FILE"):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    hashes = [h.strip().lower() for h in raw.replace("\n", ",").split(",") if h.strip()]
    # Refus explicite d'une clé en clair mise par erreur à la place de son empreinte.
    if invalid := [h for h in hashes if not _SHA256_HEX.match(h)]:
        raise RuntimeError(f"{len(invalid)} entrée(s) qui ne sont pas des empreintes SHA-256 : "
                           "mettre l'empreinte donnée par make_api_key.py, pas la clé")
    return hashes


API_KEY_HASHES = _read_api_key_hashes()
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clé d'API")


def require_api_key(request: Request, key: str | None = Security(_api_key_header)) -> None:
    """Refuse la requête (401) si la clé est absente ou inconnue. Sinon, note le pseudonyme
    du client pour le journal d'accès."""
    if AUTH_DISABLED:
        request.state.client = "anonymous"
        return
    # compare_digest : comparaison en temps constant (pas de fuite par le temps de réponse).
    key_hash = hash_api_key(key) if key is not None else None
    matched = next((h for h in API_KEY_HASHES if key_hash and hmac.compare_digest(key_hash, h)), None)
    if matched is None:
        raise HTTPException(401, "Clé d'API absente ou invalide", headers={"WWW-Authenticate": "ApiKey"})
    request.state.client = audit_log.pseudonymize("client", matched)


# Modèles servis par ce déploiement (défaut : tous). Un modèle chargé reste en mémoire : la
# liste fixe donc la mémoire maximale d'un pod (LAYA seul demande ~3 Go au chargement).
ALLOWED_MODELS = [m.strip() for m in os.environ.get("ALLOWED_MODELS", ",".join(MODELS)).split(",") if m.strip()]


def load_model(name: str) -> ToxicityModel:
    """Charge le modèle à la première demande puis le garde en mémoire.
    Lève KeyError (modèle inconnu ou non servi) ou FileNotFoundError (modèle non entraîné)."""
    if name not in MODELS or name not in ALLOWED_MODELS:
        raise KeyError(f"Modèle '{name}' non disponible. Disponibles : {', '.join(ALLOWED_MODELS)}")
    with _lock:
        if name not in _models:
            model_dir = ARTIFACTS_DIR / name
            if not model_dir.exists():
                raise FileNotFoundError(f"Modèle '{name}' non entraîné : lancez train.py --model {name}")
            _models[name] = get_model_class(name).load(model_dir)
        return _models[name]


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Échec fermé : une API déployée sans clé configurée ne doit pas être ouverte à tous.
    if not API_KEY_HASHES and not AUTH_DISABLED:
        raise RuntimeError("Aucune clé d'API : définir API_KEY_HASHES ou API_KEY_HASHES_FILE "
                           "(ou AUTH_DISABLED=1 en local)")
    if threads := os.environ.get("TORCH_NUM_THREADS"):
        torch.set_num_threads(int(threads))
    metrics_prom.start_server()
    # Un modèle demandé au préchargement mais absent fait échouer le démarrage :
    # mieux vaut un déploiement refusé qu'un service qui répond 503.
    for name in filter(None, (n.strip() for n in os.environ.get("PRELOAD_MODELS", DEFAULT_MODEL).split(","))):
        # Préchauffage : la première inférence d'un processus est très lente (initialisation de
        # PyTorch, lecture effective des poids depuis le volume). Elle est faite ici, avant que
        # /health réponde : un nouveau pod ne reçoit du trafic qu'une fois rapide.
        start = time.perf_counter()
        load_model(name).predict_proba(_WARMUP_TEXTS)
        log.info(f"Modèle {name} chargé et préchauffé",
                 extra={"fields": {"event": "warmup", "model": name,
                                   "duration_ms": round(1000 * (time.perf_counter() - start))}})
    yield


app = FastAPI(title="Toxic comments detector", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def access_log_and_security_headers(request: Request, call_next):
    request_id = uuid.uuid4().hex[:16]
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        _log_request(request, request_id, 500, start, exc_info=True)
        _record_metrics(request, 500, start)
        raise
    _record_metrics(request, status, start)
    # /health est appelé en continu par les sondes et le load balancer : pas de bruit dans les logs.
    if not (request.url.path == "/health" and status < 400):
        _log_request(request, request_id, status, start)

    # Permet à un client de citer sa requête au support sans rien révéler.
    response.headers["X-Request-ID"] = request_id
    # Le navigateur n'utilisera plus que HTTPS pour ce domaine pendant 1 an.
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    # Les réponses contiennent le texte analysé : aucun cache (navigateur, proxy) ne doit le garder.
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _record_metrics(request: Request, status: int, start: float) -> None:
    path = metrics_prom.path_label(request.url.path)
    metrics_prom.REQUESTS.labels(request.method, path, str(status)).inc()
    metrics_prom.REQUEST_DURATION.labels(path).observe(time.perf_counter() - start)


def _log_request(request: Request, request_id: str, status: int, start: float, exc_info: bool = False) -> None:
    """Une ligne JSON par requête, sans texte, sans clé, sans IP en clair."""
    state = request.state
    fields = {
        "event": "auth_failed" if status == 401 else "request",
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "latency_ms": round(1000 * (time.perf_counter() - start), 1),
        "client": getattr(state, "client", None),
        "ip": audit_log.pseudonymize("ip", audit_log.client_ip(request)),
        "model": getattr(state, "model", None),
        "text_length": getattr(state, "text_length", None),
        "toxicity_score": getattr(state, "toxicity_score", None),
    }
    level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
    log.log(level, f"{request.method} {request.url.path} {status}", exc_info=exc_info,
            extra={"fields": {k: v for k, v in fields.items() if v is not None}})


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


@app.post("/model", response_model=Prediction, dependencies=[Depends(require_api_key)])
def predict(req: PredictionRequest, request: Request) -> Prediction:
    text, model = req.text, req.model
    # Pour le journal d'accès : la longueur du texte, jamais son contenu.
    request.state.model, request.state.text_length = model, len(text)
    try:
        clf = load_model(model)
    except KeyError as e:
        raise HTTPException(404, e.args[0]) from e
    except FileNotFoundError as e:
        raise HTTPException(503, str(e)) from e
    metrics_prom.TEXT_LENGTH.observe(len(text))
    wait_start = time.perf_counter()
    metrics_prom.QUEUE_WAITING.inc()
    try:
        acquired = _inference_slots.acquire(timeout=INFERENCE_QUEUE_TIMEOUT)
    finally:
        metrics_prom.QUEUE_WAITING.dec()
    metrics_prom.QUEUE_WAIT.observe(time.perf_counter() - wait_start)
    if not acquired:
        raise HTTPException(503, "Service surchargé, réessayer dans quelques secondes",
                            headers={"Retry-After": "5"})
    try:
        with metrics_prom.INFERENCE_DURATION.labels(model).time():
            probs = dict(zip(LABELS, clf.predict_proba([text])[0]))
    finally:
        _inference_slots.release()
    toxic_score = 100 * float(probs["toxic"])
    request.state.toxicity_score = round(toxic_score, 1)
    metrics_prom.TOXICITY_SCORE.labels(model).observe(toxic_score)
    return Prediction(
        model=model,
        text=text,
        is_toxic=toxic_score > TOXIC_THRESHOLD,
        toxicity_score=round(toxic_score, 1),
        classes={label: round(100 * float(probs[label]), 1) for label in OTHER_LABELS},
    )
