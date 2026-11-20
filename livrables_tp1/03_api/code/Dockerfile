# Image d'inférence (API FastAPI) pour GKE.
#
# Build (depuis un Mac Apple Silicon : les nœuds GKE sont en amd64) :
#   docker build --platform linux/amd64 -t toxic-api .
# Test local (les modèles sont montés depuis artifacts/, ils ne sont pas dans l'image) :
#   python make_api_key.py   (affiche une clé et son empreinte)
#   docker run --rm -p 8080:8080 -v "$(pwd)/artifacts:/app/artifacts:ro" \
#     -e API_KEY_HASHES=<empreinte> toxic-api
#   curl -X POST localhost:8080/model -H "Content-Type: application/json" \
#        -H "X-API-Key: <clé>" -d '{"text": "You are an idiot"}'
#
# Les poids des modèles NE SONT PAS dans l'image : sur GKE, ils sont lus depuis un volume
# persistant (bucket Cloud Storage, voir k8s/toxic-api.yaml). L'image ne contient que le code
# et les dépendances d'inférence (pas le groupe "train" : spaCy, polars, scikit-learn), avec
# PyTorch CPU (voir [tool.uv.sources] dans pyproject.toml).
#
# Ordre des couches : du plus stable au plus changeant. Docker réutilise toutes les couches
# avant la première modifiée : changer api.py ne reconstruit ni ne repousse que quelques Ko.

# ---------- Étape 1 : environnement Python ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --frozen : uv.lock fait foi ; --no-default-groups : ni "train" ni "dev".
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project

# ---------- Étape 2 : image finale ----------
FROM python:3.12-slim

# Correctifs de sécurité des paquets système (zlib, openssl...) parus depuis la publication de
# l'image de base : sans cela, trivy (CI) échoue sur toute faille corrigée par Debian mais pas
# encore intégrée à python:3.12-slim.
# hadolint ignore=DL3005
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Utilisateur non-root.
RUN useradd --create-home --uid 1000 app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Les modèles sont lus depuis artifacts/ : aucun appel à Hugging Face au runtime.
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    # Chargé au démarrage pour éviter une première requête lente (cold start).
    PRELOAD_MODELS=bert \
    # À aligner sur resources.requests.cpu du manifest.
    TORCH_NUM_THREADS=2 \
    PORT=8080

WORKDIR /app
# 1. Dépendances : ne changent qu'avec uv.lock.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# 2. Code : change souvent, en dernier.
COPY --chown=app:app toxicity/ ./toxicity/
COPY --chown=app:app api.py ./

USER app
EXPOSE 8080

# Un seul worker : le modèle n'est chargé qu'une fois en mémoire ; le parallélisme passe par
# les threads PyTorch et par le nombre de pods (HorizontalPodAutoscaler).
# --no-access-log : le journal d'accès d'uvicorn écrit les IP en clair ; il est remplacé par
# le journal pseudonymisé de l'API (toxicity/audit_log.py).
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log"]
