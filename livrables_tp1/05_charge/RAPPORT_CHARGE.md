# Rapport de simulation de montée en charge

API « Digital Social Score » déployée sur GKE Autopilot (`europe-west1`), modèle BERT, testée le 12/11/2026 avec **Locust** ([loadtest/](../../loadtest/)).

## 1. Protocole

| Élément | Valeur |
|---|---|
| Outil | Locust 2.x, piloté par `loadtest/run.py` (scénarios dans `loadtest/config.toml`) |
| Cible | `POST /model` (BERT) en HTTP, avec une clé d'API de test |
| Utilisateur virtuel | ~1 requête/s (pause aléatoire de 0,5 à 1,5 s), textes de longueurs variées, aucune donnée personnelle |
| Déploiement testé | Pods de 2 CPU / 2 Gi, une inférence à la fois par pod, HPA sur le CPU (cible 60 %), 3 pods minimum |
| Contournements pendant le test | Exception Cloud Armor pour l'IP de la machine de test (sinon `429` au-delà de 100 req/min) et plafond par IP levé (`MAX_IN_FLIGHT_PER_IP=0`) : on mesure la capacité de l'API, pas le filtrage |
| Relevés | Latences moyenne, P50, P95, P99, max, taux d'erreur (Locust) ; nombre de pods et CPU moyen toutes les 15 s (HPA) ; mémoire par pod (`kubectl top pods`) |

| Scénario | Profil | Durée | Question posée |
|---|---|---|---|
| `smoke` | 2 utilisateurs | 30 s | La chaîne complète (DNS, TLS, clé, Cloud Armor) fonctionne-t-elle ? |
| `ramp` | 5 → 120 utilisateurs, paliers de 90 s | 12 min | Croissance progressive : où est le point de saturation ? |
| `rapid` | 0 → 120 utilisateurs en 12 s, maintenus | 8 min | L'autoscaling réagit-il assez vite à un afflux brutal ? |
| `soak` | 60 utilisateurs constants | 30 min | La latence et la mémoire restent-elles stables dans la durée ? |
| `failover` | 40 utilisateurs, un pod supprimé à t = 300 s | 10 min | Le service survit-il à la perte d'un pod ? |
| `stress` | 0 → 200 utilisateurs en 1 min | 5 min | Que se passe-t-il au-delà de la capacité ? |

## 2. Résultats

Tableau produit par `uv run python loadtest/summarize.py` (latences en ms) :

| Test | Requêtes | Débit moy. (req/s) | Débit max (req/s) | Latence moy. | P50 | P95 | P99 | Max | Erreurs (%) | Pods max |
|---|---|---|---|---|---|---|---|---|---|---|
| smoke (16:00) | 49 | 1,6 | 1,7 | 182 | 89 | 620 | 690 | 695 | 0,00 | 3 |
| avant-ramp (14:25, 1er essai) | 18 365 | 25,5 | 71,5 | 1 102 | 330 | 2 600 | **30 000** | 39 367 | 0,17 | 9 |
| ramp | 16 848 | 23,4 | 44,6 | 1 273 | 580 | 4 600 | 6 300 | 19 251 | **0,00** | 6 |
| rapid | 20 384 | 42,4 | 50,1 | 1 785 | 710 | 6 000 | 8 600 | 20 089 | 0,01 | 6 |
| soak | 70 112 | 39,0 | 46,8 | 520 | 330 | 1 600 | 3 400 | 10 304 | **0,00** | 6 |
| failover | 16 385 | 27,3 | 31,4 | 440 | 340 | 1 200 | 1 800 | 5 361 | **0,01** | 6 |
| stress | 10 536 | 35,2 | 48,7 | 4 179 | 950 | 17 000 | 20 000 | 24 904 | 0,03 | 6 |

Erreurs observées : quelques `502` isolés (2 à 3 par test, au moment où un pod est retiré du load balancer), et 31 `429` lors du premier essai (exception Cloud Armor pas encore en place).

Rapports Locust détaillés (graphiques débit / latence / utilisateurs dans le temps) : fichiers `*.html` de [results/](results/).

### 2.1 Croissance progressive (`ramp`) : point de saturation

Médiane, sur chaque palier, des valeurs relevées toutes les 2 s :

| Utilisateurs | Débit (req/s) | P50 (ms) | P95 (ms) | Lecture |
|---|---|---|---|---|
| 5 | 4,5 | 77 | 310 | Confortable |
| 10 | 8,6 | 85 | 380 | Confortable |
| 20 | 16,5 | 110 | 630 | Limite du SLO (P95 < 1 s) avec 3 pods |
| 40 | 23,0 | 490 | 2 100 | **Saturation** : les requêtes attendent leur tour |
| 60 | 24,8 | 1 200 | 3 400 | L'autoscaling ajoute des pods, avec retard |
| 80 | 31,4 | 660 | 4 000 | |
| 100 | 34,7 | 1 800 | 4 600 | |
| 120 | 42,2 | 1 000 | 6 200 | 6 pods, débit maximal ~45 req/s |

- **Capacité d'un pod** : ~7 à 8 req/s, soit ~130 ms par inférence BERT sur 2 vCPU, une inférence à la fois (déduit des ~23 req/s atteints à saturation avec 3 pods). Avec 3 pods, le SLO tient jusqu'à ~20 req/s.
- **Point de saturation** : entre 20 et 40 utilisateurs simultanés. Au-delà, le débit augmente moins vite que la demande et la différence se transforme en attente (latence), pas en erreurs.

### 2.2 Montée rapide (`rapid`)

120 utilisateurs arrivent en 12 s. Le HPA passe de 3 à 6 pods en ~2 min (création du pod, téléchargement de l'image, chargement et préchauffage du modèle). Pendant ce rattrapage, la latence monte (P95 6 s), mais le taux d'erreur reste à 0,01 % : le service ralentit sans tomber.

### 2.3 Charge soutenue (`soak`, 30 min)

- **Latence stable, puis meilleure** : P95 médian de 2,4 s en début de test (3 pods), 1,2 s en fin de test (6 pods). Pas de dérive dans le temps.
- **Mémoire** : 340-380 Mio par pod au repos, 490-880 Mio en fin de test, maximum observé 1,3 Gio sur l'ensemble des tests, toujours sous la limite de 2 Gi. Pas de hausse continue : pas de fuite mémoire constatée.
- **0 erreur** sur 70 112 requêtes.

### 2.4 Failover (perte d'un pod)

Un pod sur 4 est supprimé à t = 300 s (`failover-20261112-1631_events.txt`) :

| Fenêtre | Débit (req/s) | P50 (ms) | P95 (ms) | P99 (ms) | Erreurs |
|---|---|---|---|---|---|
| t = 270-300 s (avant) | 29-30 | 210-330 | 750-1 100 | 1 100-1 900 | 0 |
| t = 310-330 s (pendant) | 24-27 | 360-590 | 1 100-1 700 | 1 400-3 400 | 0 |
| t = 340-380 s (après) | 26-30 | 250-410 | 920-1 500 | 1 200-1 700 | 0 |

- **Aucune erreur pendant la panne** : les 3 pods restants absorbent la charge, le pod est recréé automatiquement par le `Deployment`.
- **Impact** : latence P95 ×1,5 pendant ~30 s, débit −15 %, retour à la normale en moins d'une minute.
- Sur tout le test : 2 `502` sur 16 385 requêtes (0,01 %), au moment où le load balancer retire le pod supprimé.

### 2.5 Stress (au-delà de la capacité)

200 utilisateurs, soit ~4 fois la capacité de 6 pods. Le débit plafonne à ~48 req/s, la latence explose (P95 17 s, max 25 s), mais le service ne s'effondre pas (0,03 % d'erreurs). Sans limite de file, toutes les requêtes en trop attendent : du point de vue d'un utilisateur, une réponse en 17 s équivaut à une panne.

### 2.6 Premier essai (`avant-ramp`) et corrections apportées

Le premier essai (14:25) partait d'**un seul pod** (`avant-ramp_hpa.txt`) : P99 de **30 s** et max de 39 s, le temps que l'autoscaling démarre de nouveaux pods (9 pods au maximum, chacun chargeant son modèle avant de pouvoir répondre). Les tests suivants partent de **3 pods minimum** (`minReplicas: 3`) : capacité de base plus élevée, et la perte d'un pod ne coupe pas le service (validé par `failover`).

Résultat : P99 de 30 s à 6,3 s sur le même scénario de croissance, et plus aucune erreur. Après ces tests, la mémoire réservée par pod a été ramenée de 6 Gi à 2 Gi, d'après les mesures (350 Mio à 1,3 Gio réellement utilisés, LAYA n'étant plus servi en production) : plus de pods possibles pour le même quota.

## 3. Analyse

| Constat | Cause | Conséquence |
|---|---|---|
| Le service ne tombe jamais (≤ 0,03 % d'erreurs, même en stress) | Redondance (3 pods minimum), autoscaling, load balancer qui ne route que vers les pods prêts | Disponibilité assurée |
| La latence, elle, dégrade fortement au-delà de ~20 req/s pour 3 pods | Une inférence BERT à la fois par pod, sur CPU : les requêtes en trop font la queue | SLO (95 % < 1 s) non tenu au-delà de 40 utilisateurs |
| Le HPA n'a pas dépassé 6 pods, avec un CPU moyen de 47 à 61 % | Le CPU n'est pas un bon indicateur de saturation ici : l'inférence est séquentielle, le pod sature (file d'attente) avant que son CPU n'atteigne 100 % | L'autoscaling réagit trop tard et pas assez |
| ~2 min pour ajouter un pod | Démarrage du pod + chargement et préchauffage du modèle | Un pic brutal (`rapid`) se traduit par 1 à 2 min de latence élevée |

## 4. Améliorations proposées

| Amélioration | Effet attendu | État |
|---|---|---|
| **File d'attente bornée + délestage** (`503` + `Retry-After` après 2 s d'attente, 8 requêtes en file maximum) | Les requêtes acceptées restent rapides ; le surplus est refusé en ~10 ms au lieu d'attendre 17 s | **Fait** (17/11) : en local, P50 de 1 741 ms à 74 ms sous la même charge ([attack/defense_changes.md §8d](../../attack/defense_changes.md)) |
| **Équité entre clients** (4 requêtes simultanées max par IP, file qui éjecte le plus gros consommateur) | Un client abusif ne bloque pas les autres | **Fait** (17/11) |
| **Load balancer + Cloud Armor** (100 req/min/IP, ban au-delà de 200) | Le trafic abusif est arrêté avant d'atteindre les pods | **Fait** |
| **Redondance** : 3 pods minimum, sondes de santé, recréation automatique | Survit à la perte d'un pod sans erreur | **Fait**, validé par `failover` |
| **Autoscaling sur la longueur de file ou la latence** (métrique Prometheus `toxic_api` via l'adaptateur de métriques personnalisées) au lieu du CPU | Ajout de pods dès que les requêtes attendent | Proposé |
| **Cible CPU plus basse** (60 % → 40 %) ou `minReplicas` plus haut aux heures de pointe | Marge pour absorber un pic pendant les 2 min de démarrage d'un pod | Proposé (coût : plus de pods en permanence) |
| **Cache des résultats** (clé = HMAC du texte, Memorystore/Redis, TTL court) | Les textes répétés (spam, copier-coller) ne repassent pas par le modèle | Proposé. Attention RGPD : ne stocker que l'empreinte du texte, jamais le texte |
| **Modèle plus léger en production** (LSTM : ~1,4 ms/commentaire contre ~33 ms pour BERT, ou BERT distillé / quantifié int8, ONNX Runtime) | Débit par pod ×2 à ×20 | Proposé, au prix d'une précision un peu plus faible (voir [comparaison des modèles](../02_modeles/COMPARAISON_MODELES.md)) |
| **Inférence par lots** (regrouper les requêtes arrivées dans la même fenêtre de 10-20 ms) | Meilleure utilisation du CPU | Proposé |
| **GPU** (nœuds GPU GKE) | Inférence BERT ×10 plus rapide | Proposé, coût élevé |

## 5. Conclusion

L'API passe d'un prototype à un service **scalable et résilient** : elle encaisse 70 000 requêtes en 30 min sans erreur, survit à la perte d'un pod sans erreur visible, et ne tombe pas sous une charge 4 fois supérieure à sa capacité. Sa limite est la **latence** au-delà de ~40 req/s (6 pods) : les corrections déployées depuis (délestage rapide, équité par IP) protègent les clients légitimes, et le passage à un autoscaling sur la file d'attente est la prochaine étape.

## Fichiers

| Fichier | Contenu |
|---|---|
| `results/<test>.html` | Rapport Locust (graphiques) |
| `results/<test>_stats.csv` | Statistiques agrégées : moyenne, P50…P99, erreurs |
| `results/<test>_stats_history.csv` | Évolution toutes les 2 s |
| `results/<test>_hpa.txt` | Pods demandés / prêts et CPU moyen, toutes les 15 s |
| `results/<test>_failures.csv` | Erreurs par type |
| `results/failover-*_events.txt` | Heure de suppression du pod |
| `results/memoire-*.txt` | `kubectl top pods` avant et après chaque test |
