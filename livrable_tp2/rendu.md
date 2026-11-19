# Où est le code du TP2

Le code n'est pas recopié dans `livrable_tp2/` : il vit à sa place dans le projet. Ce document indique, pour chaque étape, **quel fichier** contient **quoi**. Les chemins sont relatifs à la racine du dépôt.

## Étape 2 — Attaque

| Fichier | Rôle |
|---|---|
| [attack/dos.py](../attack/dos.py) | **Script d'attaque** : générateur de charge HTTP asynchrone et borné (durée ≤ 5 min, une seule cible). Mesure une latence de référence au repos, puis la latence p50/p95/p99, les codes HTTP, la disponibilité et le débit seconde par seconde sous charge. Endpoint, méthode et corps configurables (utilisé contre l'API d'un autre groupe). Sorties : JSON, CSV et graphiques SVG dans `attack/results/` |
| [attack/bench_defense.py](../attack/bench_defense.py) | **Banc de mesure avant / après** : lance notre API dans une version donnée (commit git), l'attaque (flood 1 IP, flood 25 IP) et fait tourner en parallèle 4 clients légitimes qui envoient 400 commentaires étiquetés du jeu de test. Calcule précision, taux de faux positifs, rappel, latence, disponibilité et débit de modération. `report` produit le tableau et les graphiques de [3_defense.md](3_defense.md) |
| [attack/report.py](../attack/report.py) | Tableau de bord HTML autonome de tous les tirs `dos.py` (`attack/results/report.html`) |
| [attack/analysis_vs_report_jungle.md](../attack/analysis_vs_report_jungle.md), [attack/defense_analysis.md](../attack/defense_analysis.md), [attack/analysis_prof_attack.md](../attack/analysis_prof_attack.md) | Analyses des trois attaques : la nôtre contre les « Dragons célestes », celle de « Report Jungle » contre nous, celle du professeur contre nous (constats, causes côté serveur, améliorations et effet mesuré) |
| [attack/results/](../attack/results/) | Résultats bruts des tirs contre l'autre groupe (`dos-palier-*` : avant leur correction, `dos-corr-palier-*` : après) et des premiers tests locaux (`local-*`) |

## Étape 3 — Défense

Ordre dans lequel une requête traverse les défenses, du load balancer au modèle :

| # | Défense | Fichier | Détail |
|---|---|---|---|
| 1 | Ban temporaire par IP (100 req/min puis `429`, ban 10 min au-delà de 200 req/min) et rejet des corps > 4096 octets (`403`) au load balancer | [k8s/cloud-armor-setup.sh](../k8s/cloud-armor-setup.sh) | Cloud Armor, politique `toxic-api-policy` ; script idempotent (crée ou met à jour) |
| 2 | En-têtes de sécurité (HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) et journal pseudonymisé de chaque refus | [api.py](../api.py), middleware `access_log_and_security_headers` (vers la ligne 221) | Toutes les réponses de l'API, refus compris |
| 3 | Plafond de 4 requêtes en cours par IP (`429` immédiat, avant lecture du corps) | [toxicity/ip_limit.py](../toxicity/ip_limit.py), branché dans [api.py](../api.py) ligne 218 | `MAX_IN_FLIGHT_PER_IP` |
| 4 | Limite de taille du corps HTTP (`413`), avec ou sans `Content-Length` | [toxicity/body_limit.py](../toxicity/body_limit.py), branché ligne 214 | `MAX_BODY_BYTES = 4096` (ligne 80) |
| 5 | Texte limité à 1500 caractères (`422`) | [api.py](../api.py) ligne 74, `PredictionRequest` | Justifié par la troncature du modèle à 256 tokens et la distribution des données |
| 6 | Documentation et schéma OpenAPI fermés en production | [api.py](../api.py) lignes 81 et 206 | `ENABLE_DOCS=1` pour les ouvrir en local |
| 7 | File d'inférence bornée (8 places, 2 s max) et **équitable** : file pleine, on éjecte une requête de l'IP la plus présente | [toxicity/fair_queue.py](../toxicity/fair_queue.py), utilisée par `_acquire_inference_slot` dans [api.py](../api.py) (ligne 300) | `503` + `Retry-After` |
| 8 | Traitement asynchrone : une requête en attente n'occupe aucun thread, `/health` répond même sous attaque | [api.py](../api.py) : `health`, `predict`, `require_api_key` en `async` | Cause du « flapping » de la vague 5 |
| 9 | Réponse réduite à `{"is_toxic": …}` (anti-extraction du modèle) | [api.py](../api.py), classe `Prediction` (ligne 286) | Scores conservés côté serveur uniquement |
| 10 | Réglages de production et autoscaling (3 à 15 pods) | [k8s/toxic-api.yaml](../k8s/toxic-api.yaml) | Variables `MAX_QUEUE_LENGTH`, `INFERENCE_QUEUE_TIMEOUT`, `MAX_IN_FLIGHT_PER_IP`, `ENABLE_DOCS` |
| — | Métrique des refus par raison (`ip_limit`, `full`, `evicted`, `timeout`) | [toxicity/metrics_prom.py](../toxicity/metrics_prom.py), `toxic_api_shed_total` | Pour voir pendant une attaque si les refus visent l'attaquant |

Journal détaillé des changements, attaque par attaque : [attack/defense_changes.md](../attack/defense_changes.md).

## Étape 4 — Conformité IA Act

| Fichier | Rôle |
|---|---|
| [model_card.json](../model_card.json) | Model card (à la racine, comme demandé), champs du sujet + justification du risque, biais, performances, usages exclus |
| [ci/check_model_card.py](../ci/check_model_card.py) | Vérification automatique de la model card dans la CI |
| [REGISTRE_TRAITEMENT.md](../REGISTRE_TRAITEMENT.md) | Registre RGPD (article 30), cité par la fiche IA Act |

## Étape 5 — DevSecOps

| Fichier | Rôle |
|---|---|
| [.github/workflows/devsecops.yml](../.github/workflows/devsecops.yml) | Pipeline : 10 jobs de sécurité et de conformité |
| [tests/](../tests/) | Tests pytest des défenses (`test_api_defenses.py`) et de la file équitable (`test_fair_queue.py`), avec un modèle factice |
| [pyproject.toml](../pyproject.toml) | Configuration de ruff, bandit et pytest, exceptions justifiées |
| [.gitleaks.toml](../.gitleaks.toml) | Configuration de la recherche de secrets |
| [Dockerfile](../Dockerfile) | Image multi-étapes, utilisateur non-root, paquets système à jour (scannée par trivy) |
