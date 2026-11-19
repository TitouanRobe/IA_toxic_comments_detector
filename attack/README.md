# TP2 — Attaque : Déni de service applicatif (DoS L7)

> Exercice pédagogique autorisé : attaque du déploiement d'un autre groupe du cours,
> dans le but de mesurer la résistance de l'API puis de concevoir une défense (étape 3).

Trois attaques sont documentées dans ce dossier : celle que **nous avons menée** contre un autre
groupe, et les deux que **nous avons subies** (un groupe, puis le professeur). Les améliorations
de notre API découlent surtout des secondes.

| Attaque | Analyse | Ce qu'on en retient |
|---|---|---|
| Nous → groupe « Dragons célestes » (16/11) | [analysis_vs_report_jungle.md](analysis_vs_report_jungle.md) | Leur API ne refusait rien : latence jusqu'à 56 s. Après leur correction, 97 % de `429` |
| Groupe « Report Jungle » → nous (13/11) | [defense_analysis.md](defense_analysis.md) | Arrêtés par Cloud Armor en amont : 0 erreur côté serveur. Le throttle simple laisse cependant revenir l'attaquant |
| **Professeur → nous (17/11, vagues 5 et 6)** | [analysis_prof_attack.md](analysis_prof_attack.md) | L'attaque a atteint le modèle : 7712 requêtes acceptées en 92 s, latence ×11, modèle extractible, service instable. **Origine de la plupart des défenses** |

Défenses qui en découlent : [defense_changes.md](defense_changes.md) (journal détaillé) et
[livrable_tp2/3_defense.md](../livrable_tp2/3_defense.md) (synthèse et mesure avant / après).

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
   Depuis l'extérieur, `dos.py` ne mesure que la **latence / disponibilité** : on ne connaît pas
   la vérité terrain des réponses de la cible. Précision et faux positifs sont donc mesurés sur
   **notre** API avec `bench_defense.py` (voir plus bas), pendant que des clients légitimes
   envoient des commentaires étiquetés.

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

## Attaques subies et améliorations qui en découlent

**Groupe « Report Jungle » (13/11).** Leur script mesurait jusqu'à 100 % d'échecs (`429`) et
concluait à un service tombé ; nos logs montraient 0 erreur et une latence normale : Cloud Armor
avait filtré en amont. Leçon : croiser la vue de l'attaquant et celle du serveur. Amélioration :
le simple throttle (l'attaquant revient dès qu'il ralentit) devient un **ban de 10 min par IP**.

**Professeur (17/11).** L'audit de la vague 6 a trouvé 4 problèmes, tous traités :

| Constat de l'audit | Cause trouvée côté serveur | Amélioration | Effet mesuré en local |
|---|---|---|---|
| DoS : 7712 requêtes acceptées en 92 s, latence ×11 | Ban Cloud Armor jamais appliqué en production ; file d'attente de 20 s qui accepte tout | Script Cloud Armor pour projet neuf ; **plafond de 4 requêtes en cours par IP** (`429`) ; **délestage rapide** (8 places, 2 s, `503`) ; **file équitable** qui éjecte le spammeur | Latence médiane des clients légitimes sous flood : **1 001 → 81 ms** |
| Service instable (vague 5) | `/health` bloqué derrière les threads occupés par la file | Endpoints asynchrones | `/health` sous charge : 620–830 ms → 2–12 ms |
| Modèle extractible (489 req/min) | Les 6 scores renvoyés à chaque requête | Réponse réduite à `{"is_toxic": …}` | Un bit par requête au lieu de 6 probabilités |
| `/model` trouvé via l'OpenAPI, en-têtes manquants | Documentation publique ; CSP et X-Frame-Options absents (HSTS et nosniff étaient présents) | Docs fermées en production, en-têtes complétés | Vérifié par les tests automatiques de la CI |

Analyse complète : [analysis_prof_attack.md](analysis_prof_attack.md).

## Défense (étape 3) et mesure avant / après — `bench_defense.py`

Les défenses mises en place sont décrites dans [defense_changes.md](defense_changes.md) (journal
détaillé) et résumées dans [livrable_tp2/3_defense.md](../livrable_tp2/3_defense.md).

`bench_defense.py` rejoue la même attaque contre deux versions de **notre** API (la version
attaquée par le professeur, commit `451d731`, et la version actuelle) pendant que des clients
légitimes envoient des commentaires étiquetés du jeu de test : il mesure la **précision**, le
**taux de faux positifs** et la **latence** vécus par ces clients, avec et sans défense.

```bash
uv run python attack/bench_defense.py run --ref 451d731 --label avant   # ~5 min
uv run python attack/bench_defense.py run --label apres                  # ~5 min
uv run python attack/bench_defense.py report   # livrable_tp2/donnees/tableau_avant_apres.md + figures/
```
