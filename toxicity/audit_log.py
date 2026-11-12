"""Journal d'accès JSON pseudonymisé (RGPD).

Une ligne JSON par requête sur la sortie standard. Sur GKE, Cloud Logging la récupère et
indexe chaque champ (filtrage par client, statut, latence...).

Ce qui est journalisé : identifiant de requête, méthode, chemin, statut, latence, modèle,
longueur du texte, score. Client et adresse IP sont PSEUDONYMISÉS.
Ce qui ne l'est JAMAIS : le texte analysé, la clé d'API, l'adresse IP en clair.

Pseudonymisation : HMAC-SHA256 avec une clé secrète (LOG_PSEUDONYM_KEY). Un simple hash ne
suffirait pas : les 4 milliards d'adresses IPv4 se hachent en quelques minutes, ce qui
permettrait de retrouver chaque IP. Sans la clé, le pseudonyme est irréversible ; avec la
même clé, un même client a toujours le même pseudonyme (audit, détection d'abus).
Changer la clé rend les anciens et nouveaux pseudonymes impossibles à relier.

Variables d'environnement :
  LOG_PSEUDONYM_KEY       clé de pseudonymisation (chaîne aléatoire), ou
  LOG_PSEUDONYM_KEY_FILE  fichier la contenant (sur GKE : secret Secret Manager monté).
  Sans clé : clé aléatoire propre au processus (pseudonymes non comparables entre pods
  ni après un redémarrage), avec un avertissement.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
from datetime import UTC, datetime

from starlette.requests import Request


def _load_key() -> tuple[bytes, bool]:
    raw = os.environ.get("LOG_PSEUDONYM_KEY", "")
    if path := os.environ.get("LOG_PSEUDONYM_KEY_FILE"):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    if raw.strip():
        return raw.strip().encode(), True
    return secrets.token_bytes(32), False


_KEY, _KEY_IS_STABLE = _load_key()


def pseudonymize(kind: str, value: str | None) -> str | None:
    """'ip', '203.0.113.7' -> 'ip_3f9a2c...' (16 caractères hexadécimaux = 64 bits)."""
    if not value:
        return None
    digest = hmac.new(_KEY, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}_{digest[:16]}"


def client_ip(request: Request) -> str | None:
    """IP réelle du client derrière le load balancer Google, qui ajoute à X-Forwarded-For :
    '<IP éventuellement fournies par le client>, <IP du client>, <IP du load balancer>'.
    Seule l'avant-dernière est fiable (les premières peuvent être falsifiées par le client)."""
    if forwarded := request.headers.get("x-forwarded-for"):
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-2] if len(parts) >= 2 else parts[0]
    return request.client.host if request.client else None


class JsonFormatter(logging.Formatter):
    """Format compris par Cloud Logging : 'severity' et 'message' sont reconnus,
    les autres champs deviennent des champs filtrables (jsonPayload)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "message": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("toxic_api")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not _KEY_IS_STABLE:
        logger.warning("LOG_PSEUDONYM_KEY absente : clé aléatoire, pseudonymes non comparables "
                       "entre pods ni après redémarrage")
    return logger
