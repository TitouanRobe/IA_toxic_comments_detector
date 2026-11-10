# IA_toxic_comments_detector

Détection de toxicité : chaque texte reçoit un **score de 0 à 100** (= 100 × probabilité maximale parmi les 6 classes Jigsaw : `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`).

## Installation

```bash
uv sync
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
```

Sorties : `artifacts/<modèle>/` (poids) et `results/<modèle>/metrics.json` (métriques).

`compare.py` affiche, pour chaque modèle, sur le jeu de test :
- **AUC moy** : ROC-AUC moyenne des 6 classes (critère du meilleur modèle) ;
- **précision / rappel / F1@50** : décision binaire « toxique si score ≥ 50 » ;
- **ms/comm.** : temps de traitement moyen d'un commentaire (inférence sur tout le test, tokenisation comprise) ;
- **train (s)** : durée d'entraînement ;
- puis la ROC-AUC par classe.

Les temps ne sont comparables qu'entre modèles évalués sur le même matériel.

## Ajouter un modèle

1. Créer `toxicity/models/<nom>.py` avec une classe héritant de `ToxicityModel` (`fit`, `predict_proba`, `save`, `load`).
   Un modèle PyTorch peut réutiliser `toxicity/models/torch_trainer.py` ; un modèle scikit-learn implémente simplement `fit` / `predict_proba`.
2. L'enregistrer dans `toxicity/models/registry.py`.
3. `train.py --model <nom>` puis `compare.py`.
