# Image d'inférence (API FastAPI) pour Cloud Run.
#
# Build (depuis un Mac Apple Silicon, Cloud Run tourne en amd64) :
#   docker build --platform linux/amd64 -t toxic-api .
# Test local :
#   docker run --rm -p 8080:8080 toxic-api
#   curl -X POST localhost:8080/model -H "Content-Type: application/json" -d '{"text": "You are an idiot"}'
#
# Les poids de artifacts/ sont embarqués dans l'image : chaque image = une version du modèle.
# Seules les dépendances d'inférence sont installées (pas le groupe "train" : spaCy, polars,
# scikit-learn), avec PyTorch CPU (voir [tool.uv.sources] dans pyproject.toml).

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

# Utilisateur non-root.
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app toxicity/ ./toxicity/
COPY --chown=app:app api.py ./
COPY --chown=app:app artifacts/ ./artifacts/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Les modèles sont lus depuis artifacts/ : aucun appel à Hugging Face au runtime.
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    # Chargé au démarrage pour éviter une première requête lente (cold start).
    PRELOAD_MODELS=bert \
    # À aligner sur --cpu de Cloud Run.
    TORCH_NUM_THREADS=2 \
    PORT=8080

USER app
EXPOSE 8080

# Cloud Run fournit $PORT. Un seul worker : le modèle n'est chargé qu'une fois en mémoire,
# le parallélisme passe par les threads PyTorch et la concurrence de Cloud Run.
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT} --workers 1"]
