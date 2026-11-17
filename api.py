"""API FastAPI d'inférence de toxicité.

Lancement (clé et empreinte générées par make_api_key.py) :
  ENABLE_DOCS=1 API_KEY_HASHES=<empreinte> uv run uvicorn api:app --reload
Exemple :
  curl -X POST http://127.0.0.1:8000/model -H "Content-Type: application/json" \\
       -H "X-API-Key: <clé>" -d '{"text": "You are an idiot", "model": "bert"}'
Le texte est envoyé dans le corps (POST) et non dans l'URL, pour qu'il ne
se retrouve pas dans les logs d'accès, proxys ou historiques. Il n'est jamais stocké.
Documentation interactive : http://127.0.0.1:8000/docs (bouton "Authorize" pour la clé),
uniquement avec ENABLE_DOCS=1 (désactivée par défaut, donc en production).

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
  MAX_QUEUE_LENGTH           requêtes en attente max par pod (défaut : 8) ; file pleine : la
                             requête de l'IP la plus présente est éjectée, ou la nouvelle refusée (503).
  INFERENCE_QUEUE_TIMEOUT    attente max en file (s, défaut : 2) avant une réponse 503.
  MAX_IN_FLIGHT_PER_IP       requêtes /model en cours max par IP et par pod (défaut : 4) ; au-delà,
                             429 immédiat avant toute lecture. 0 pour désactiver (test de charge).
  ENABLE_DOCS        "1" pour exposer /docs, /redoc et /openapi.json (défaut : désactivés).
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
from threading import Lock

import torch
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from toxicity import audit_log, metrics_prom
from toxicity.body_limit import BodySizeLimit
from toxicity.config import ARTIFACTS_DIR, LABELS
from toxicity.fair_queue import FairInferenceQueue
from toxicity.ip_limit import PerIPInFlightLimit
from toxicity.models.base import ToxicityModel
from toxicity.models.registry import MODELS, get_model_class

DEFAULT_MODEL = "bert"
TOXIC_THRESHOLD = 70  # score "toxic" strictement supérieur à 70 -> toxique
# Limite d'entrée (caractères). Les modèles tronquent à max_length = 256 tokens (~1200-1500
# caractères, voir toxicity/models/*.py) : au-delà, le texte est jeté par le tokenizer et
# n'influe pas sur le score. Accepter 5000 n'apportait donc rien au modèle tout en offrant une
# surface d'attaque (épuisement de ressources par entrées surdimensionnées, « sponge input » :
# réseau + parsing + tokenisation). 1500 couvre ~96 % des commentaires réels (p95 = 1364 car.
# sur train.csv, 1274 sur test.csv) et s'aligne sur ce que le modèle lit vraiment.
MAX_TEXT_LENGTH = 1500  # caractères
# Taille maximale du corps HTTP, vérifiée AVANT sa lecture (toxicity/body_limit.py, réponse 413).
# 1500 caractères font ~1,5 Ko de JSON en ASCII ; 4096 laisse la marge pour l'UTF-8 multioctet et
# les caractères échappés (é). Même seuil que la règle Cloud Armor 900, qui rejette les corps
# trop gros au load balancer (k8s/cloud-armor-setup.sh) ; ici on couvre aussi les corps envoyés
# sans Content-Length (chunked), que Cloud Armor ne voit pas.
MAX_BODY_BYTES = 4096
ENABLE_DOCS = os.environ.get("ENABLE_DOCS") == "1"
_DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}

_models: dict[str, ToxicityModel] = {}
_lock = Lock()

# File d'attente des inférences. Sans limite, FastAPI exécute jusqu'à 40 requêtes en parallèle
# qui se disputent les 2 CPU du pod : chacune devient lente et le débit total baisse. Avec une
# seule inférence à la fois (qui utilise déjà tous les threads PyTorch), les autres attendent
# leur tour et chaque requête passe plus vite.
MAX_CONCURRENT_INFERENCES = int(os.environ.get("MAX_CONCURRENT_INFERENCES", "1"))
# Délestage de charge : au-delà de la capacité du pod, refuser VITE (503 + Retry-After) plutôt que
# faire attendre. Avec une attente tolérée de 20 s (ancienne valeur), un flood restait « accepté
# mais lent » : latence médiane ×11 mesurée par l'attaque de la vague 6. Deux bornes :
# - MAX_QUEUE_LENGTH : nombre de requêtes en attente par pod. Pleine -> 503 immédiat, sans
#   attendre. 8 requêtes à ~100 ms d'inférence = ~1 s d'attente au pire pour celles acceptées.
# - INFERENCE_QUEUE_TIMEOUT : attente maximale d'une requête entrée dans la file (filet de
#   sécurité si une inférence est anormalement lente, ex. texte long sur un pod chargé).
MAX_QUEUE_LENGTH = int(os.environ.get("MAX_QUEUE_LENGTH", "8"))
INFERENCE_QUEUE_TIMEOUT = float(os.environ.get("INFERENCE_QUEUE_TIMEOUT", "2"))
# Une IP ne peut pas avoir plus de 4 requêtes /model en cours sur un pod : 1 en inférence + 3 en
# file au pire, soit moins de la moitié de la file. Un client légitime, qui attend sa réponse avant
# d'envoyer la suivante, n'en a qu'une. Au-delà : 429 immédiat (toxicity/ip_limit.py).
MAX_IN_FLIGHT_PER_IP = int(os.environ.get("MAX_IN_FLIGHT_PER_IP", "4"))
# La file est gérée dans la boucle asyncio, pas dans des threads : une requête en attente ne
# bloque pas de thread. Avant, chaque requête en attente occupait un des 40 threads du pool de
# FastAPI ; sous flood, ils étaient tous bloqués et même /health attendait un thread libre, d'où
# des sondes en échec et un pod retiré du load balancer (service qui « flappe », vague 5).
# File équitable : pleine, elle éjecte une requête de l'IP qui spamme plutôt que de refuser le
# nouvel arrivant (toxicity/fair_queue.py).
_inference_queue = FairInferenceQueue(MAX_CONCURRENT_INFERENCES, MAX_QUEUE_LENGTH)

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


async def require_api_key(request: Request, key: str | None = Security(_api_key_header)) -> None:
    """Refuse la requête (401) si la clé est absente ou inconnue. Sinon, note le pseudonyme
    du client pour le journal d'accès. Asynchrone (calcul immédiat) : ne consomme pas de thread."""
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


# Documentation et schéma OpenAPI désactivés par défaut : en production, ils donnaient à un
# attaquant la carte complète de l'API (c'est par /openapi.json que /model a été trouvé, vague 6).
app = FastAPI(
    title="Toxic comments detector", version="0.1.0", lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)
# Ajouté AVANT le middleware de journalisation ci-dessous, donc placé à l'intérieur de celui-ci :
# les refus 413 sont journalisés, comptés dans les métriques et reçoivent les en-têtes de sécurité.
app.add_middleware(BodySizeLimit, max_bytes=MAX_BODY_BYTES)
# Ajouté après BodySizeLimit, donc placé DEVANT lui (et devant l'auth et la file d'inférence) :
# une IP qui a déjà trop de requêtes en cours est refusée (429) avant que son corps soit lu.
# Toujours à l'intérieur du middleware de journalisation : ces refus sont journalisés.
app.add_middleware(PerIPInFlightLimit, max_in_flight=MAX_IN_FLIGHT_PER_IP, paths={"/model"})


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
    # L'API ne renvoie que du JSON : aucune ressource à charger, aucune page à encadrer.
    # Bloque l'inclusion dans une iframe (clickjacking) et toute exécution de contenu.
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # Exception pour /docs (Swagger charge ses scripts depuis un CDN), qui n'existe qu'avec ENABLE_DOCS=1.
    if not (ENABLE_DOCS and request.url.path in _DOCS_PATHS):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
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
    # Uniquement la décision, jamais les scores : des probabilités renvoyées pour chaque texte
    # permettent d'entraîner une copie du modèle avec bien moins de requêtes (extraction de
    # modèle, constat de la vague 6). Le score reste calculé côté serveur (seuil, journal, métriques).
    is_toxic: bool  # basé uniquement sur la classe "toxic" (score > 70)


@app.get("/health")
async def health() -> dict:
    # Asynchrone : répond même si tous les threads sont occupés par des inférences, pour que les
    # sondes Kubernetes et le health check du load balancer ne déclarent pas un pod chargé « mort ».
    return {"status": "ok", "loaded_models": sorted(_models)}


async def _acquire_inference_slot(request: Request) -> bool:
    """Attend un créneau d'inférence. False si la requête est refusée (file pleine), éjectée au
    profit d'une IP moins présente, ou si l'attente dépasse INFERENCE_QUEUE_TIMEOUT."""
    ip = audit_log.client_ip(request) or "unknown"
    metrics_prom.QUEUE_WAITING.inc()
    start = time.perf_counter()
    try:
        refusal = await _inference_queue.acquire(ip, INFERENCE_QUEUE_TIMEOUT)
    finally:
        metrics_prom.QUEUE_WAITING.dec()
        metrics_prom.QUEUE_WAIT.observe(time.perf_counter() - start)
    if refusal:
        metrics_prom.SHED.labels(refusal).inc()
    return refusal is None


@app.post("/model", response_model=Prediction, dependencies=[Depends(require_api_key)])
async def predict(req: PredictionRequest, request: Request) -> Prediction:
    text, model = req.text, req.model
    # Pour le journal d'accès : la longueur du texte, jamais son contenu.
    request.state.model, request.state.text_length = model, len(text)
    # Modèle déjà chargé : accès direct. Sinon chargement (lent) dans un thread.
    clf = _models.get(model)
    if clf is None:
        try:
            clf = await run_in_threadpool(load_model, model)
        except KeyError as e:
            raise HTTPException(404, e.args[0]) from e
        except FileNotFoundError as e:
            raise HTTPException(503, str(e)) from e
    metrics_prom.TEXT_LENGTH.observe(len(text))
    if not await _acquire_inference_slot(request):
        raise HTTPException(503, "Service surchargé, réessayer dans quelques secondes",
                            headers={"Retry-After": "2"})
    try:
        # L'inférence (PyTorch, bloquante) tourne dans un thread pour ne pas figer la boucle
        # asyncio. Non annulable : si le client se déconnecte, le créneau n'est libéré qu'à la
        # fin réelle de l'inférence (jamais deux inférences en même temps par créneau).
        with metrics_prom.INFERENCE_DURATION.labels(model).time():
            scores = (await run_in_threadpool(clf.predict_proba, [text]))[0]
    finally:
        _inference_queue.release()
    toxic_score = 100 * float(dict(zip(LABELS, scores))["toxic"])
    # Le score n'est conservé que côté serveur (journal pseudonymisé, métriques), jamais renvoyé.
    request.state.toxicity_score = round(toxic_score, 1)
    metrics_prom.TOXICITY_SCORE.labels(model).observe(toxic_score)
    return Prediction(is_toxic=toxic_score > TOXIC_THRESHOLD)
