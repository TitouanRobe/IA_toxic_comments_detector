# Livrables TP1 : API « Digital Social Score »

Détection de toxicité d'un texte, score de 0 à 100, API sécurisée déployée sur Google Cloud (GKE Autopilot, `europe-west1`).

Ce dossier regroupe les livrables demandés par le sujet ([TP1_Digital_Social_Score.pdf](../TP1_Digital_Social_Score.pdf)). Les fichiers de code et de configuration sont des **copies** à la date du rendu (19/11/2026) ; le projet exécutable est à la racine du dépôt (installation et commandes : [README](../README.md)).

## Livrables globaux (section 2 du sujet)

| Livrable demandé | Où |
|---|---|
| API fonctionnelle et documentée | [03_api/API.md](03_api/API.md), code dans [03_api/code/](03_api/code/) |
| Exemples de requêtes et réponses | [03_api/EXEMPLES_REQUETES.md](03_api/EXEMPLES_REQUETES.md) | 
| Schéma d'architecture Cloud + texte explicatif | [07_architecture/ARCHITECTURE_CLOUD.md](07_architecture/ARCHITECTURE_CLOUD.md) + [SVG](07_architecture/architecture-cloud.svg) |
| Registre RGPD | [04_securite_rgpd/REGISTRE_TRAITEMENT.md](04_securite_rgpd/REGISTRE_TRAITEMENT.md) | 
| Tableau de bord / captures supervision | [06_supervision_securite/SUPERVISION.md](06_supervision_securite/SUPERVISION.md), tableau de bord [config/dashboard-toxic-api.json](06_supervision_securite/config/dashboard-toxic-api.json) | 
| Rapport simulation de charge | [05_charge/RAPPORT_CHARGE.md](05_charge/RAPPORT_CHARGE.md) + résultats bruts [05_charge/results/](05_charge/results/) | 
| Grille tests sécurité / stress / failover | [06_supervision_securite/GRILLE_TESTS.md](06_supervision_securite/GRILLE_TESTS.md) |

## Par étape du sujet

| Étape | Exercice | Livrable |
|---|---|---|
| **1. Données et anonymisation** | Jeu Jigsaw, NER spaCy, comparaison initial / anonymisé, registre | [01_donnees/ANONYMISATION.md](01_donnees/ANONYMISATION.md) · code : `count_entities.py`, `anonymize.py` · [registre](04_securite_rgpd/REGISTRE_TRAITEMENT.md) |
| **2. Modèles** | Nettoyage, LSTM / BERT / LAYA, précision, rappel, temps | [02_modeles/COMPARAISON_MODELES.md](02_modeles/COMPARAISON_MODELES.md) · code : `clean_text.py`, `train.py`, `evaluate.py`, `compare.py` · métriques : [02_modeles/metriques/](02_modeles/metriques/) · [model_card.json](02_modeles/model_card.json) |
| **3. API Cloud** | Export du modèle, API texte → score, exemples | [03_api/](03_api/) : `api.py` (FastAPI), `Dockerfile`, manifeste GKE `toxic-api.yaml` |
| **4. Sécurité et RGPD** | Clé d'API, IAM, registre (HTTPS non retenu) | [04_securite_rgpd/SECURITE.md](04_securite_rgpd/SECURITE.md) · [registre](04_securite_rgpd/REGISTRE_TRAITEMENT.md) · [fiche AI Act](04_securite_rgpd/AI_ACT.md) · [Cloud Armor](04_securite_rgpd/cloud-armor-setup.sh) |
| **5. Montée en charge** | Locust : croissance, stress, failover ; P95/P99, erreurs ; améliorations | [05_charge/RAPPORT_CHARGE.md](05_charge/RAPPORT_CHARGE.md) · scénarios : [05_charge/code/config.toml](05_charge/code/config.toml) |
| **6. Sécurité et supervision** | Prometheus / Grafana, analyse de logs, panne et attaque | [06_supervision_securite/SUPERVISION.md](06_supervision_securite/SUPERVISION.md) · [grille de tests](06_supervision_securite/GRILLE_TESTS.md) · alertes : [config/alert-rules.yml](06_supervision_securite/config/alert-rules.yml) · attaques : [attack/](../attack/) |
| **7. Architecture Cloud** | Composants, flux, authentification, chiffrement, zero trust, légende | [07_architecture/ARCHITECTURE_CLOUD.md](07_architecture/ARCHITECTURE_CLOUD.md) · source éditable [.excalidraw](07_architecture/architecture-cloud.excalidraw) |

## Critères d'évaluation (section 3 du sujet)

| Compétence | Indicateurs | Éléments de preuve |
|---|---|---|
| Fonctionnalité | API opérationnelle, scoring correct | ROC-AUC 0,985 (BERT) |
| Sécurité | Authentification, validation d'entrée, chiffrement | [Grille](06_supervision_securite/GRILLE_TESTS.md) [SECURITE.md](04_securite_rgpd/SECURITE.md) |
| Scalabilité | Test de charge et analyse pertinente | [Rapport de charge](05_charge/RAPPORT_CHARGE.md) |
| Supervision | Logs / métriques, dashboard | [SUPERVISION.md](06_supervision_securite/SUPERVISION.md) ; journal JSON pseudonymisé |
| Conformité RGPD | Anonymisation, registre conforme | [ANONYMISATION.md](01_donnees/ANONYMISATION.md) ; [registre](04_securite_rgpd/REGISTRE_TRAITEMENT.md) |
| Présentation | Documentation claire, schéma lisible, code commenté | [schéma](07_architecture/architecture-cloud.svg)|

## Écarts connus

1. **Score 0-100** : l'API ne renvoie que `is_toxic` (choix de sécurité du TP2, contre l'extraction du modèle). Le score 0-100 est calculé à chaque requête, journalisé (`toxicity_score`) et affiché par `predict.py`, mais n'est pas dans la réponse HTTP.
