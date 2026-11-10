# IA_toxic_comments_detector

Détection de toxicité : chaque texte reçoit un **score de 0 à 100** (= 100 × probabilité maximale parmi les 6 classes Jigsaw : `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`).

## Installation

```bash
uv sync   # inférence + groupes "train" (spaCy, polars, scikit-learn) et "dev"
```

- **Windows (RTX 5080)** : torch est installé depuis l'index PyTorch CUDA 13.0 (compatible driver CUDA 13.2). Vérifier : `uv run python -c "import torch; print(torch.cuda.get_device_name())"`.
- **Mac (M3)** : torch PyPI, entraînement sur MPS.

## Utilisation

```bash
# Test rapide (Mac)
uv run python train.py --model bert --limit 2000 --epochs 1 --max-length 128 --test-limit 1000

# Entraînement complet (Windows / GPU)
uv run python train.py --model bert

# LSTM (BiLSTM entraîné from scratch, batch 128 et lr 1e-3 par défaut)
uv run python train.py --model lstm --limit 2000 --epochs 1 --max-length 128 --test-limit 1000   # test rapide Mac
uv run python train.py --model lstm --epochs 5                                                     # complet (Windows / GPU)

# LAYA (encodeur ModernBERT-large de convaiinnovations/laya + tête 6 classes, batch 16 et lr 1e-5 par défaut)
# ~840 Mo téléchargés au premier lancement (cache Hugging Face) ; lent sur Mac, GPU conseillé.
uv run python train.py --model laya --limit 2000 --epochs 1 --max-length 128 --test-limit 1000   # test rapide Mac
uv run python train.py --model laya --epochs 2                                                     # complet (Windows / GPU)

# Score d'un texte
uv run python predict.py --model bert "You are an idiot"

# Comparaison des modèles entraînés
uv run python compare.py

# API d'inférence (documentation interactive : http://127.0.0.1:8000/docs)
uv run uvicorn api:app --reload
curl -X POST http://127.0.0.1:8000/model -H "Content-Type: application/json" \
     -d '{"text": "You are an idiot", "model": "bert"}'
```

`POST /model` avec un corps JSON `{"text": "...", "model": "bert"}` (`model` optionnel, `bert` par défaut ; `text` limité à 5000 caractères) renvoie :
- `is_toxic` : `true` si le score de la classe `toxic` est > 70 ;
- `toxicity_score` : score de la classe `toxic` (0-100) ;
- `classes` : scores (0-100) de `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`.

Sorties : `artifacts/<modèle>/` (poids) et `results/<modèle>/metrics.json` (métriques).

`compare.py` affiche, pour chaque modèle, sur le jeu de test :
- **AUC moy** : ROC-AUC moyenne des 6 classes (critère du meilleur modèle) ;
- **précision / rappel / F1@50** : décision binaire « toxique si score ≥ 50 » ;
- **ms/comm.** : temps de traitement moyen d'un commentaire (inférence sur tout le test, tokenisation comprise) ;
- **train (s)** : durée d'entraînement ;
- puis la ROC-AUC par classe.

Les temps ne sont comparables qu'entre modèles évalués sur le même matériel.

## Déploiement (Docker / Cloud Run)

L'image ne contient que l'API et les dépendances d'inférence (`dependencies` du `pyproject.toml`) :
PyTorch CPU, sans le groupe `train` (spaCy, polars, scikit-learn) ni `data/` (voir `.dockerignore`).
Les poids de `artifacts/` sont embarqués : **réentraîner les modèles complètement avant de construire l'image.**

```bash
# Test local (sur Mac Apple Silicon, --platform est nécessaire : Cloud Run tourne en amd64)
docker build --platform linux/amd64 -t toxic-api .
docker run --rm -p 8080:8080 toxic-api
curl localhost:8080/health

# Publication dans Artifact Registry (région Paris)
REGION=europe-west9; PROJECT=<id-projet-gcp>
IMAGE=$REGION-docker.pkg.dev/$PROJECT/toxic/toxic-api:v1
gcloud artifacts repositories create toxic --repository-format=docker --location=$REGION
gcloud auth configure-docker $REGION-docker.pkg.dev
docker build --platform linux/amd64 -t $IMAGE . && docker push $IMAGE

# Déploiement (privé par défaut : appel avec un jeton IAM)
gcloud run deploy toxic-api --image $IMAGE --region $REGION \
  --cpu 2 --memory 4Gi --concurrency 4 --cpu-boost \
  --min-instances 0 --no-allow-unauthenticated
curl -X POST "$(gcloud run services describe toxic-api --region $REGION --format 'value(status.url)')/model" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" -d '{"text": "You are an idiot"}'
```

- `--min-instances 1` supprime les démarrages à froid, mais l'instance est facturée en continu.
- `TORCH_NUM_THREADS` (2 par défaut dans l'image) doit suivre `--cpu` ; `PRELOAD_MODELS` (défaut `bert`) liste les modèles chargés au démarrage.
- Sans Docker en local : `gcloud builds submit --tag $IMAGE` (le `.gcloudignore` garantit l'envoi de `artifacts/`).

## Ajouter un modèle

1. Créer `toxicity/models/<nom>.py` avec une classe héritant de `ToxicityModel` (`fit`, `predict_proba`, `save`, `load`).
   Un modèle PyTorch peut réutiliser `toxicity/models/torch_trainer.py` ; un modèle scikit-learn implémente simplement `fit` / `predict_proba`.
2. L'enregistrer dans `toxicity/models/registry.py`.
3. `train.py --model <nom>` puis `compare.py`.
