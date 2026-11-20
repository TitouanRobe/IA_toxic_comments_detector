# Étape 2 : préparation et comparaison des modèles

## 1. Préparation des textes

Chaîne : `train.csv` → `anonymize.py` → `train_anonymized.csv` → `clean_text.py` → `train_clean.csv` (même chaîne pour le test).

| Traitement (`clean_text.py`) | Pourquoi |
|---|---|
| Suppression des emojis et pictogrammes | Peu fréquents dans le jeu, absents du vocabulaire du LSTM |
| Normalisation Unicode, mise en minuscules | Réduit le vocabulaire (« IDIOT » = « idiot ») |
| Suppression de la ponctuation (apostrophes internes conservées : « don't »), espaces multiples réduits | Bruit pour le LSTM ; BERT et LAYA ont leur propre tokenizer |
| Pseudonymes protégés (`[PERSON_1]` ni mis en minuscules ni débarrassés de ses crochets) | Le modèle voit toujours le même marqueur pour « une personne citée » |

## 2. Les trois modèles

| | LSTM (statistique) | BERT (transformer) | LAYA (décisionnaire) |
|---|---|---|---|
| Architecture | BiLSTM entraîné de zéro, vocabulaire construit sur le jeu d'entraînement | `distilbert-base-uncased` affiné (DistilBERT, 66 M paramètres) | Encodeur ModernBERT-large de `convaiinnovations/laya` + tête de décision 6 classes |
| Entraînement | 3 époques, lot de 128, lr 1e-3 | 3 époques, lot de 32 | 2 époques, lot de 16, lr 1e-5 |
| Longueur max | 256 jetons | 256 jetons | 256 jetons |
| Taille des poids | 61 Mo | 256 Mo | 1,5 Go |


## 3. Résultats sur le jeu de test

Évaluation des modèles entraînés (`evaluate.py`) sur **5 000 commentaires** du test anonymisé et nettoyé (`test_clean.csv`), sur la même machine (Mac M3, GPU MPS) pour que les temps soient comparables. Décision binaire « toxique si une classe ≥ 50 ».

| Modèle | ROC-AUC moy. | Précision | Rappel | F1 | Temps (ms / commentaire) |
|---|---|---|---|---|---|
| **BERT** | **0,985** | **0,440** | **0,949** | **0,601** | 30,0 |
| LSTM | 0,976 | 0,417 | 0,935 | 0,577 | **2,2** |
| LAYA | 0,985 | 0,480 | 0,941 | 0,635 | 1 338 (*) |

ROC-AUC par classe :

| Modèle | toxic | severe_toxic | obscene | threat | insult | identity_hate |
|---|---|---|---|---|---|---|
| BERT | 0,970 | 0,992 | 0,979 | 0,997 | 0,979 | 0,994 |
| LSTM | 0,963 | 0,985 | 0,977 | 0,991 | 0,972 | 0,966 |
| LAYA | 0,972 | 0,993 | 0,979 | 0,995 | 0,979 | 0,991 |

(*) Temps mesuré sur un Mac (GPU MPS), peu stable : sur une première évaluation, BERT était à 32,8 ms, le LSTM à 1,4 ms et LAYA à 289 ms ; sur la seconde, 30,0 / 2,2 / 1 338 ms (LAYA a probablement été ralenti par la machine). Les métriques de qualité sont, elles, identiques d'une évaluation à l'autre. À retenir : LSTM ≈ 1-2 ms, BERT ≈ 30 ms, LAYA au moins 10 fois plus lent que BERT.

Fichiers : `metriques/<modèle>/metrics_test_clean.json`, produits par `evaluate.py --limit 5000` à partir des modèles de `artifacts/` (échantillon tiré avec une graine fixe, identique pour les trois). Paramètres d'entraînement : `metriques/<modèle>/train_config.json`.

## 4. Analyse

- **Précision faible, rappel élevé, pour les trois modèles** : c'est l'effet voulu de la pondération des classes rares. À un seuil de 50, le modèle préfère signaler un commentaire douteux plutôt que d'en laisser passer un toxique (95 % des toxiques détectés par BERT). Pour un outil de modération qui alerte un humain, ce compromis est acceptable ; l'API applique un seuil plus strict (**70**) sur la classe `toxic`, ce qui augmente la précision.
- **BERT vs LSTM** : BERT est meilleur sur toutes les classes, surtout `identity_hate` (0,994 contre 0,966), où le contexte compte plus que les mots-clés. Mais il est **environ 15 à 25 fois plus lent** (30 ms contre 1-2 ms).
- **LAYA** : à égalité avec BERT en ROC-AUC (0,9847 contre 0,9851), avec la meilleure précision (0,480) et le meilleur F1 (0,635), mais un rappel un peu plus bas (0,941). Il est au moins 10 fois plus lent que BERT (289 à 1 338 ms contre 30 ms) : le gain de qualité ne justifie pas ce coût pour une API.
- La ROC-AUC (indépendante du seuil) est le critère de choix : elle mesure la capacité à classer un commentaire toxique au-dessus d'un sain, quel que soit le seuil retenu ensuite.

## 5. Choix pour la production

**BERT** est le modèle par défaut de l'API : meilleur compromis qualité / coût. Sur CPU (2 vCPU par pod), il traite ~7-8 requêtes/s par pod ([rapport de charge](../05_charge/RAPPORT_CHARGE.md)).

- **LSTM** est aussi servi (`"model": "lstm"`) : quasi aussi bon, 15 à 25 fois plus rapide ; c'est la solution de repli en cas de forte charge.
- **LAYA** n'est pas servi en production : ~3 Go de mémoire au chargement par pod, et un temps d'inférence qui multiplierait le nombre de pods nécessaires.

Score renvoyé : score global = 100 × probabilité maximale parmi les 6 classes ; l'API répond `is_toxic = true` si la classe `toxic` dépasse 70.
