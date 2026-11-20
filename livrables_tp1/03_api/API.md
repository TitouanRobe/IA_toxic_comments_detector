# Documentation de l'API

API REST d'inférence « Digital Social Score » (FastAPI) : elle reçoit un texte et indique s'il est toxique. Le modèle calcule un score de 0 à 100 pour chacune des 6 classes `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`.

- **Format** : JSON, encodage UTF-8
- **Version** : 0.1.0
- **Authentification** : clé d'API dans l'en-tête `X-API-Key` (obligatoire sur `POST /model`)
- **Documentation interactive (Swagger)** : `/docs`, schéma OpenAPI : `/openapi.json`. Uniquement si l'API est lancée avec `ENABLE_DOCS=1` (fermés par défaut, donc en production : `404`)

## URL de base

| Environnement | URL |
|---|---|
| Production (GKE, HTTP) | `http://136.82.246.21` |
| Local (uvicorn) | `http://127.0.0.1:8000` |
| Local (Docker) | `http://localhost:8080` |

En production, l'API répond en **HTTP** (pas de HTTPS) : la clé d'API et le texte analysé circulent en clair entre le client et le load balancer. Limite assumée pour ce projet ; en usage réel, activer HTTPS (certificat géré par Google, déjà prévu dans le manifeste).

## Authentification

`POST /model` exige l'en-tête `X-API-Key: dss_…`. Sans clé ou avec une clé invalide : `401`.

- Une clé est générée par `uv run python make_api_key.py`, qui affiche la clé (à donner au client) et son empreinte SHA-256 (la seule chose que l'API connaît, stockée dans Google Secret Manager).
- Une clé par application cliente, révocable individuellement.
- `GET /health` reste public (sondes Kubernetes et health check du load balancer) et ne renvoie aucune donnée sensible.

## Endpoints

### `GET /health`

Vérifie que le service répond et indique les modèles chargés en mémoire.

**Réponse `200`**

```json
{ "status": "ok", "loaded_models": ["bert"] }
```

### `POST /model`

Analyse un texte avec le modèle demandé.

Le texte est envoyé dans le corps de la requête (et non dans l'URL), pour ne pas apparaître dans les logs d'accès, proxys ou historiques. Il n'est jamais stocké ni journalisé.

**En-têtes**

| En-tête | Valeur |
|---|---|
| `Content-Type` | `application/json` |
| `X-API-Key` | Clé d'API du client |

**Corps de la requête**

| Champ | Type | Obligatoire | Description |
|---|---|---|---|
| `text` | string | oui | Texte à analyser, de 1 à 1500 caractères |
| `model` | string | non | `bert` (défaut) ou `lstm` en production ; `laya` aussi en local |

```json
{ "text": "You are an idiot", "model": "bert" }
```

**Réponse `200`**

| Champ | Type | Description |
|---|---|---|
| `is_toxic` | boolean | `true` si le score de la classe `toxic` est > 70 (sur 100) |

```json
{ "is_toxic": true }
```

Chaque réponse porte un en-tête `X-Request-ID`, qui permet de retrouver la requête dans le journal.

**Notes**

- Seule la décision est renvoyée, pas les scores : des probabilités détaillées permettraient de copier le modèle (extraction) avec bien moins de requêtes. Le score 0-100 est calculé à chaque requête et conservé côté serveur : journal d'accès (`toxicity_score`) et métriques Prometheus. Il est aussi affiché par `predict.py` (voir [exemples](EXEMPLES_REQUETES.md)).
- Le seuil (70) est fixé côté serveur.

**Erreurs**

| Code | Cause | Exemple de corps |
|---|---|---|
| `401` | Clé d'API absente ou invalide | `{"detail": "Clé d'API absente ou invalide"}` |
| `404` | Modèle inconnu ou non servi par ce déploiement | `{"detail": "Modèle 'xyz' non disponible. Disponibles : bert, lstm"}` |
| `413` | Corps de requête de plus de 4096 octets | `{"detail": "Corps de requête trop volumineux (maximum 4096 octets)"}` |
| `422` | Corps invalide (`text` absent, vide ou de plus de 1500 caractères, JSON mal formé) | Détail de validation FastAPI |
| `429` | Plus de 4 requêtes simultanées depuis la même IP : réessayer après `Retry-After` | `{"detail": "Trop de requêtes simultanées depuis cette adresse"}` |
| `429` | Limite Cloud Armor : plus de 100 requêtes/min depuis la même IP (au-delà de 200/min, IP bannie 10 min) | Page d'erreur du load balancer |
| `403` | Corps de plus de 4096 octets rejeté par Cloud Armor, avant d'atteindre l'API | Page d'erreur du load balancer |
| `503` | Serveur surchargé (file d'attente pleine ou attente de plus de 2 s) : réessayer après `Retry-After` | `{"detail": "Service surchargé, réessayer dans quelques secondes"}` |
| `503` | Modèle connu mais non entraîné, donc absent de `artifacts/` | `{"detail": "Modèle 'laya' non entraîné : lancez train.py --model laya"}` |

## Exemples

Exemples complets, avec les réponses réellement obtenues : [livrables_tp1/03_api/EXEMPLES_REQUETES.md](EXEMPLES_REQUETES.md).

```bash
# curl
curl -X POST http://136.82.246.21/model \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"text": "You are an idiot", "model": "bert"}'
# {"is_toxic":true}
```

```python
# Python
import os

import requests

r = requests.post(
    "http://136.82.246.21/model",
    headers={"X-API-Key": os.environ["API_KEY"]},
    json={"text": "Have a nice day", "model": "bert"},
    timeout=30,
)
r.raise_for_status()
print(r.json()["is_toxic"])  # False
```

## Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `API_KEY_HASHES` / `API_KEY_HASHES_FILE` | aucun (obligatoire) | Empreintes SHA-256 des clés acceptées (fichier monté depuis Secret Manager en production). Sans empreinte, l'API refuse de démarrer |
| `AUTH_DISABLED` | non | `1` désactive l'authentification (local uniquement) |
| `LOG_PSEUDONYM_KEY` / `LOG_PSEUDONYM_KEY_FILE` | aléatoire | Clé HMAC de pseudonymisation des IP et clients dans le journal |
| `ALLOWED_MODELS` | tous | Modèles servis (`bert,lstm` en production) ; les autres : `404` |
| `PRELOAD_MODELS` | `bert` | Modèles chargés et préchauffés au démarrage. Un modèle absent fait échouer le démarrage |
| `TORCH_NUM_THREADS` | choix de torch | Threads PyTorch ; à aligner sur le nombre de vCPU |
| `MAX_CONCURRENT_INFERENCES` | `1` | Inférences simultanées par pod |
| `MAX_QUEUE_LENGTH` | `8` | Requêtes en attente par pod, au-delà : `503` |
| `INFERENCE_QUEUE_TIMEOUT` | `2` | Attente maximale en file (s), au-delà : `503` |
| `MAX_IN_FLIGHT_PER_IP` | `4` | Requêtes simultanées par IP et par pod, au-delà : `429` (`0` = sans limite) |
| `ENABLE_DOCS` | non | `1` ouvre `/docs` et `/openapi.json` |
| `PORT` | `8080` (Docker) | Port d'écoute dans l'image Docker |

## Comportement à connaître

- **Chargement des modèles** : les modèles listés dans `PRELOAD_MODELS` sont chargés et préchauffés au démarrage, avant que `/health` réponde. Les autres le sont à la première requête qui les demande, ce qui rend cette requête plus lente.
- **Modèles servis en production** : `bert` et `lstm`. `laya` (~3 Go de mémoire au chargement) n'est pas servi par le déploiement GKE.
- **Mode hors ligne** : l'image est configurée avec `HF_HUB_OFFLINE=1`, aucun téléchargement Hugging Face n'est fait à l'exécution.
- **Limites de débit** : Cloud Armor (100 req/min/IP, ban 10 min au-delà de 200 req/min), puis plafond de 4 requêtes simultanées par IP et par pod, puis file d'attente bornée et équitable entre IP.
- **En-têtes de sécurité** sur toutes les réponses : HSTS (sans effet en HTTP), `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-store`.
