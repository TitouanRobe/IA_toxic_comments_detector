"""Métriques Prometheus de l'API, exposées sur un port séparé (METRICS_PORT, défaut 8081).

Le port de métriques n'est pas publié par le Service Kubernetes : il n'est joignable que
depuis le cluster (Prometheus), jamais depuis Internet via le load balancer.

Aucune donnée personnelle : pas de texte, pas d'IP, pas de client dans les labels. Les labels
sont limités à des valeurs connues (chemins de l'API, statuts, modèles) : un scanner qui
appelle des milliers d'URL inventées ne peut pas faire exploser le nombre de séries.
"""

import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

KNOWN_PATHS = {"/model", "/health", "/docs", "/openapi.json", "/redoc"}

REQUESTS = Counter(
    "toxic_api_requests_total", "Requêtes HTTP reçues", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "toxic_api_request_duration_seconds", "Durée totale des requêtes (file d'attente comprise)", ["path"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30),
)
INFERENCE_DURATION = Histogram(
    "toxic_api_inference_duration_seconds", "Durée de l'inférence seule", ["model"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
QUEUE_WAIT = Histogram(
    "toxic_api_queue_wait_seconds", "Attente avant de pouvoir lancer l'inférence",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)
QUEUE_WAITING = Gauge("toxic_api_queue_waiting", "Requêtes en attente d'un créneau d'inférence")
# Raisons bornées : ip_limit (429, trop de requêtes en cours pour une IP), full (503, file pleine),
# evicted (503, éjectée de la file au profit d'une IP moins présente), timeout (503, attente trop longue).
SHED = Counter("toxic_api_shed_total", "Requêtes refusées par délestage", ["reason"])
TOXICITY_SCORE = Histogram(
    "toxic_api_toxicity_score", "Score de toxicité renvoyé (0-100)", ["model"],
    buckets=(10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
)
TEXT_LENGTH = Histogram(
    "toxic_api_text_length_chars", "Longueur des textes analysés (caractères)",
    buckets=(50, 100, 250, 500, 1000, 2000, 5000),
)


def path_label(path: str) -> str:
    return path if path in KNOWN_PATHS else "other"


def start_server() -> None:
    """Démarre le serveur de métriques (thread). METRICS_PORT=0 le désactive (tests)."""
    if port := int(os.environ.get("METRICS_PORT", "8081")):
        start_http_server(port)
