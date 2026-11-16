# Analyse — Notre attaque DoS contre l'API du groupe « Report Jungle »

**Objet :** interpréter les tirs effectués avec `attack/dos.py` contre l'API du groupe
« Report Jungle » (`http://136.82.199.122/laya/...`), pour le livrable étape 2
(« Mise en œuvre de l'attaque » / « graphique des métriques + interprétation »).

**Cible :** `GET /laya/health` (endpoint public, sans clé). On n'a pas de clé de test pour
`POST /laya/score` (l'endpoint qui exécute réellement leur modèle de toxicité) : cette analyse
mesure donc la résistance de leur **serveur web / infra**, pas directement leur IA. Voir la
limite en section 5.

**Méthode :** 3 paliers de concurrence (100 / 1000 / 2000 clients), 30 s chacun, avec une
baseline (5 requêtes séquentielles) avant chaque tir. Résultats bruts :
`results/dos-palier-100.json`, `dos-palier-1000.json`, `dos-palier-2000.json`
(+ graphiques `*_latency.svg`, `*_throughput.svg`).

---

## 1. Résultats mesurés

| Concurrence | Requêtes | Débit (req/s) | Codes HTTP | Latence au repos | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| **100**  | 2 221 | 71,7 | 100 % `200` | 57,8 ms | 989 ms | 3 757 ms | 5 675 ms | 11 733 ms |
| **1000** | 2 553 | 57,5 | 100 % `200` | 51,2 ms | 9 825 ms | 39 452 ms | 42 300 ms | 43 748 ms |
| **2000** | 3 379 | 59,7 | 100 % `200` | 48,1 ms | 29 861 ms | 51 951 ms | 53 686 ms | 56 164 ms |

**Facteurs de dégradation (p50 / p95 / p99 / max, vs baseline) :**

| Concurrence | p50 | p95 | p99 | max |
|---|---|---|---|---|
| 100  | ×17 | ×65 | ×98 | ×203 |
| 1000 | ×192 | ×770 | ×826 | ×854 |
| 2000 | ×621 | ×1080 | ×1116 | ×1168 |

---

## 2. Interprétation

**Zéro erreur à tous les paliers — mais une latence qui explose.** Sur les 3 tirs, **100 % des
requêtes reçoivent un `200`**, aucun `429`, aucun `503`, aucune erreur réseau. Contrairement à
ce qu'on a observé sur notre propre API quand le groupe « Report Jungle » nous a attaqués (où
Cloud Armor bloquait l'excès de trafic avec des `429` avant même d'atteindre le backend), **leur
infra ne rejette jamais rien** : elle encaisse tout en mettre les requêtes en attente,
quel que soit le volume. Chaque requête finit par aboutir, mais certaines après **56 secondes**
d'attente pour un simple `GET /health`.

**Le débit reste plat (~58-72 req/s) quelle que soit la concurrence.** Que ce soit 100, 1000 ou
2000 clients simultanés, leur serveur traite à peu près le même nombre de requêtes par seconde.
Ça signifie qu'au-delà d'un certain seuil, **augmenter la pression n'accélère rien** : les
nouvelles requêtes s'empilent simplement dans une file d'attente plus longue. C'est la signature
d'un **traitement sérialisé ou très peu parallélisé** côté serveur — comparable, en plus extrême,
au choix volontaire de ce projet-ci de limiter `MAX_CONCURRENT_INFERENCES`, **sauf qu'ici rien ne
rejette l'excédent** (pas d'équivalent au `503` + `INFERENCE_QUEUE_TIMEOUT` de `api.py`).

**C'est une vulnérabilité plus grave que celle qu'on leur a trouvée côté `429`, pas moins.**
Une défense qui rejette trop agressivement (leur critique envers nous) pénalise des utilisateurs
légitimes mais **protège le service**. Une défense qui n'a **aucun mécanisme de rejet** (ce
qu'on observe ici) laisse le service **se dégrader sans limite** : avec seulement 100 clients
(une charge triviale à générer), un simple health check met déjà ~1 seconde à répondre en
médiane, et jusqu'à 11,7 secondes dans le pire cas. À 2000 clients, la médiane dépasse **29
secondes**. Un tel délai suffirait à faire échouer n'importe quelle sonde de santé Kubernetes
(liveness/readiness) qui surveillerait cet endpoint, ce qui pourrait déclencher des redémarrages
en cascade — un déni de service bien plus profond que des `429` isolés.

---

## 3. Ce que ça dit de leur architecture (hypothèse, pas une certitude)

On ne contrôle pas leur infra, donc ceci reste une déduction à partir du comportement observé,
pas une preuve directe (contrairement à notre propre analyse, où on a pu croiser avec nos logs
serveur — voir `defense_analysis.md`). Le débit plafonné autour de ~60 req/s quelle que soit la
charge est cohérent avec :
- un serveur à **très peu de workers parallèles** (ex. un seul processus Uvicorn sans worker
  async correctement configuré, ou un nombre de réplicas insuffisant face au trafic) ;
- **aucun rate limiting ni file d'attente bornée** en amont (pas de Cloud Armor équivalent, pas
  de timeout de file comme `INFERENCE_QUEUE_TIMEOUT` dans ce projet) ;
- en conséquence, un **autoscaling potentiellement absent ou trop lent** pour réagir : le débit
  ne grimpe jamais même après 30-56 s de charge soutenue.

---

## 4. Graphiques

- `results/dos-palier-100_latency.svg` / `_throughput.svg`
- `results/dos-palier-1000_latency.svg` / `_throughput.svg`
- `results/dos-palier-2000_latency.svg` / `_throughput.svg`

(Voir aussi `attack/results/report.html` pour le tableau de bord comparatif avant/après entre
les 3 paliers.)

---

## 5. Limites de cette analyse

- **On n'a testé que `GET /laya/health`**, pas `POST /laya/score` (l'endpoint qui exécute
  réellement leur modèle de toxicité) : il nous manque une clé de test de leur groupe. Le
  résultat mesure donc la résistance de leur **serveur web**, pas directement leur IA — même si
  un serveur qui met 29 secondes à répondre à un health check trivial laisse peu d'espoir sur
  son comportement face à une vraie inférence, plus coûteuse.
- **Pas de corroboration côté serveur** (pas d'accès à leurs logs/métriques) : contrairement à
  l'analyse de leur attaque contre nous, on ne peut pas confirmer par une source indépendante
  que la latence mesurée est à 100 % côté leur serveur. Une petite part pourrait venir de notre
  propre machine à forte concurrence (voir la mise en garde du même type faite sur le test à
  25 000 clients) — mais contrairement à ce cas-là, 100/1000/2000 restent sous les limites de
  ports locaux habituelles, et la dégradation est **progressive et cohérente** entre les 3
  paliers (pas de saut brutal ni de débit anormalement bas comme à 25 000), ce qui plaide pour
  une vraie dégradation côté cible plutôt qu'un artefact local.
