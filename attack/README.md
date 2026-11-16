# TP2 — Attaque : Déni de service applicatif (DoS L7)

> Exercice pédagogique autorisé : attaque du déploiement d'un autre groupe du cours,
> dans le but de mesurer la résistance de l'API puis de concevoir une défense (étape 3).

## Fiche d'analyse « Attaque IA » (étape 1)

| Champ | Contenu |
|---|---|
| **Type d'attaque** | Déni de service applicatif (couche 7 / HTTP), par saturation en requêtes concurrentes. |
| **Cible** | L'API `Digital Social Score` d'un autre groupe, exposée publiquement (`POST /model`, `GET /health`). |
| **Point d'entrée exploité** | L'endpoint d'inférence `/model` : chaque requête déclenche une inférence BERT, coûteuse en CPU. Le pod traite les inférences en série (`MAX_CONCURRENT_INFERENCES=1`), la file a un timeout (503). |
| **Objectif de l'attaquant** | Épuiser les ressources de calcul pour dégrader la latence et provoquer des refus (503), rendant le service de modération indisponible pour les utilisateurs légitimes. |
| **Impact attendu — technique** | Latence p95/p99 en forte hausse, taux d'erreur (503/timeout) qui monte, débit utile qui s'effondre, autoscaling (HPA) sous tension / coût cloud en hausse. |
| **Impact attendu — éthique** | Pendant l'indisponibilité, les commentaires toxiques ne sont plus filtrés : la modération tombe, exposant les utilisateurs à des contenus haineux. |
| **Impact attendu — RGPD** | Atteinte à la **disponibilité** (art. 32 : intégrité *et disponibilité* des traitements). Un service de protection des personnes qui tombe est une défaillance de la mesure de sécurité. Les logs d'incident ne doivent pas enregistrer le contenu des requêtes (déjà le cas ici : seulement longueur + score). |
| **Risque IA Act** | Disponibilité et robustesse (art. 15 : exactitude, robustesse, cybersécurité). Un système de modération n'est pas « haut risque » par défaut, mais sa robustesse face aux attaques est un critère de conformité. |

### Deux limites à connaître (honnêteté sur la portée)

1. **Sans clé d'API**, `POST /model` renvoie `401` immédiatement : le modèle BERT n'est **pas**
   exécuté. On ne stresse alors que la couche web/auth (bon marché et très résistante), **pas l'IA**.
   Pour un DoS réellement efficace sur le modèle, il faut une **clé de test** du groupe cible.
   Sans clé, viser `/health` ou documenter que `/model` répond 401 (le modèle est protégé par l'auth).
2. Le TP demande 3 métriques d'impact (**précision**, **taux de faux positifs**, **latence**).
   Un DoS ne mesure que la **latence / disponibilité**. Précision et faux positifs relèvent d'une
   attaque *sur le modèle* (évasion). À assumer dans le rapport, ou à compléter par une sonde d'évasion.

## Mise en œuvre (étape 2) — `dos.py`

Générateur de charge **borné** (durée ≤ 5 min, concurrence ≤ 500, auto-stop, cible unique).
Il mesure une **baseline au repos** puis la dégradation sous charge, seconde par seconde.

```bash
# Sans clé : mesure la résistance du front (401 sur /model, ou cibler /health)
uv run python attack/dos.py http://CIBLE:8000 --endpoint /health --duration 30 --concurrency 50

# Avec clé de test fournie par la cible : charge réellement le modèle
uv run python attack/dos.py http://CIBLE:8000 --api-key dss_xxx --duration 30 --concurrency 100

# Montée progressive de la charge
uv run python attack/dos.py http://CIBLE:8000 --api-key dss_xxx --concurrency 150 --ramp 20

# Autre groupe, endpoint/méthode/format différents (--endpoint, --method, --body sont libres)
uv run python attack/dos.py http://CIBLE:5000 --endpoint /api/predict --method POST \
    --body '{"comment": "some text"}' --duration 30 --concurrency 50
```

**Endpoint, méthode et corps sont libres** (`--endpoint`, `--method`, `--body`) : tous les groupes
n'exposent pas la même API. Par défaut (sans `--body`), une requête POST/PUT/PATCH envoie
`{"text": ..., "model": ...}` (le format de ce projet) — à remplacer par `--body '<json>'` si le
groupe cible attend un autre format.

### Sorties (dans `attack/results/`)
- `dos-<horodatage>.json` — résultats complets (baseline, codes HTTP, percentiles, série temporelle).
- `dos-<horodatage>_timeseries.csv` — métriques par seconde (exploitable Grafana / Excel).
- `dos-<horodatage>_latency.svg` — **graphique** latence p50/p95/p99 sous charge.
- `dos-<horodatage>_throughput.svg` — **graphique** débit + taux d'erreur.

### Métriques mesurées
- Latence p50 / p95 / p99 et facteur de dégradation vs baseline.
- Taux d'erreur et disponibilité (%), répartition des codes HTTP (200 / 429 / 503 / timeout).
- Débit (req/s).

## Défense (étape 3) — pistes

| Contre-mesure | Effet attendu | Déjà présent dans le projet |
|---|---|---|
| **Rate limiting par IP/clé** (Cloud Armor, ou middleware) | Plafonne les requêtes par source → l'attaquant est bridé avant d'épuiser le pod. | Cloud Armor (renvoie 429) — voir `k8s/` |
| **Délestage de charge** (file bornée + timeout 503) | Un pod saturé refuse vite au lieu de s'effondrer. | `MAX_CONCURRENT_INFERENCES`, `INFERENCE_QUEUE_TIMEOUT` dans `api.py` |
| **Autoscaling (HPA)** | Ajoute des pods sous charge CPU. | `k8s/toxic-api.yaml` |
| **Authentification obligatoire** sur `/model` | L'inférence n'est jamais déclenchée sans clé (401). | `require_api_key` dans `api.py` |

Protocole étape 3 : rejouer le **même** tir `dos.py` avant/après activation (ou durcissement) de
la défense, puis comparer les métriques dans un tableau + graphique.
