# Documentation de l'API

API REST d'inférence qui prédit la toxicité d'un commentaire (FastAPI). Elle renvoie un score pour chacune des 6 classes suivantes : `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`.

- **Format** : JSON, encodage UTF-8
- **Version** : 0.1.0
- **Documentation interactive (Swagger)** : `/docs`, schéma OpenAPI : `/openapi.json`. Uniquement si l'API est lancée avec `ENABLE_DOCS=1` (fermés par défaut, donc en production : `404`)
- **Authentification** : aucune dans l'application. Sur Cloud Run, l'accès est géré par IAM (voir [Authentification](#authentification-cloud-run)).

## URL de base

| Environnement | URL |
|---|---|
| Local (uvicorn) | `http://127.0.0.1:8000` |
| Local (Docker) | `http://localhost:8080` |

## Endpoints

### `GET /health`

Vérifie que le service répond et indique les modèles chargés en mémoire.

**Réponse `200`**

```json
{ "status": "ok", "loaded_models": ["bert"] }
```

### `POST /model`

Analyse un texte avec le modèle demandé.

Le texte est envoyé dans le corps de la requête (et non dans l'URL), pour ne pas apparaître dans les logs d'accès, proxys ou historiques.

**Corps de la requête**

| Champ | Type | Obligatoire | Description |
|---|---|---|---|
| `text` | string | oui | Texte à analyser, de 1 à 1500 caractères |
| `model` | string | non | `bert` (défaut), `lstm` ou `laya` |

```json
{ "text": "You are an idiot", "model": "bert" }
```

**Réponse `200`**

| Champ | Type | Description |
|---|---|---|
| `model` | string | Modèle utilisé |
| `text` | string | Texte analysé (tel qu'envoyé) |
| `is_toxic` | boolean | `true` si `toxicity_score` > 70 |
| `toxicity_score` | number | Score de la classe `toxic`, de 0 à 100 (1 décimale) |
| `classes` | object | Scores de 0 à 100 des autres classes : `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate` |

```json
{
  "model": "bert",
  "text": "You are an idiot",
  "is_toxic": true,
  "toxicity_score": 96.4,
  "classes": {
    "severe_toxic": 3.1,
    "obscene": 41.7,
    "threat": 0.4,
    "insult": 88.2,
    "identity_hate": 1.2
  }
}
```

**Notes sur les scores**

- Les classes ne sont pas exclusives : un commentaire peut avoir plusieurs scores élevés (classification multi-label). Les scores ne totalisent donc pas 100.
- `is_toxic` ne dépend que de la classe `toxic`. Le seuil (70) est fixé côté serveur.

**Erreurs**

| Code | Cause | Exemple de corps |
|---|---|---|
| `404` | Modèle inconnu | `{"detail": "Modèle inconnu 'xyz'. Disponibles : bert, lstm, laya"}` |
| `422` | Corps invalide (`text` absent, vide ou de plus de 1500 caractères, JSON mal formé) | Détail de validation FastAPI |
| `413` | Corps de requête de plus de 4096 octets | `{"detail": "Corps de requête trop volumineux (maximum 4096 octets)"}` |
| `503` | Modèle connu mais non entraîné, donc absent de `artifacts/` | `{"detail": "Modèle 'laya' non entraîné : lancez train.py --model laya"}` |
| `503` | Serveur surchargé (file d'attente pleine) : réessayer après le délai `Retry-After` | `{"detail": "Service surchargé, réessayer dans quelques secondes"}` |

## Exemples

```bash
# curl
curl -X POST http://127.0.0.1:8000/model \
  -H "Content-Type: application/json" \
  -d '{"text": "You are an idiot", "model": "bert"}'
```

```python
# Python
import requests

r = requests.post(
    "http://127.0.0.1:8000/model",
    json={"text": "Have a nice day", "model": "bert"},
    timeout=30,
)
r.raise_for_status()
print(r.json()["is_toxic"])
```

## Authentification (Cloud Run)

Si le service est déployé sans `--allow-unauthenticated`, chaque appel doit porter un jeton d'identité Google :

```bash
URL=$(gcloud run services describe toxic-api --region <région> --format 'value(status.url)')
curl -X POST "$URL/model" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"text": "You are an idiot"}'
```

Le compte appelant doit avoir le rôle `roles/run.invoker` sur le service.

## Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `PRELOAD_MODELS` | `bert` | Modèles chargés au démarrage, séparés par des virgules. Un modèle absent fait échouer le démarrage. |
| `TORCH_NUM_THREADS` | choix de torch | Nombre de threads PyTorch ; à aligner sur le nombre de vCPU. |
| `PORT` | `8080` (Docker) | Port d'écoute dans l'image Docker (fourni par Cloud Run). |

## Comportement à connaître

- **Chargement des modèles** : les modèles listés dans `PRELOAD_MODELS` sont chargés au démarrage. Les autres le sont à la première requête qui les demande, ce qui rend cette requête plus lente. Ils restent ensuite en mémoire.
- **Cold start** : sur Cloud Run, avec `--min-instances 0`, la première requête après une période d'inactivité attend le démarrage du conteneur et le chargement du modèle.
- **Modèles disponibles dans l'image** : seuls ceux présents dans `artifacts/` au moment du build (actuellement `bert` et `lstm`). `laya` renvoie `503` tant qu'il n'est pas entraîné et copié dans l'image.
- **Mode hors ligne** : l'image est configurée avec `HF_HUB_OFFLINE=1`, aucun téléchargement Hugging Face n'est fait à l'exécution.
- **Pas de limite de débit** dans l'application : à gérer via la concurrence et le nombre maximal d'instances de Cloud Run.
