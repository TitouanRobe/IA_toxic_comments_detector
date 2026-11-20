# Étape 6 : supervision (Prometheus / Grafana) et analyse

## 1. Chaîne de supervision

| Composant | Rôle | Fichier |
|---|---|---|
| Métriques de l'API | Port 8081, interne au cluster. Requêtes par statut, latence, temps d'inférence, attente en file, scores, longueur des textes, CPU et mémoire. Aucune donnée personnelle | `toxicity/metrics_prom.py` |
| Prometheus | Découvre les pods de l'API (y compris ceux ajoutés par l'autoscaling), collecte toutes les 10 s, évalue les alertes, garde 15 jours d'historique | [config/prometheus.yml](config/prometheus.yml) |
| Grafana | Tableau de bord « Toxic API - supervision » + journaux pseudonymisés de Cloud Logging (Explore) | [config/dashboard-toxic-api.json](config/dashboard-toxic-api.json) |
| Cloud Logging | Journal d'accès JSON pseudonymisé (30 jours) | `toxicity/audit_log.py` |

Prometheus et Grafana ne sont **pas exposés sur Internet** : accès par `kubectl port-forward` (voir README, section « Supervision »).

## 2. Tableau de bord

Le tableau de bord ([config/dashboard-toxic-api.json](config/dashboard-toxic-api.json), importable dans n'importe quel Grafana) est organisé en 4 rangées :

| Rangée | Panneaux |
|---|---|
| **Vue d'ensemble** | Débit `/model` · Latence P95 · Erreurs 5xx · Pods actifs · Échecs d'authentification · Alertes actives |
| **Trafic et latence** | Requêtes/s par statut HTTP · Latence P50/P95/P99 · Temps d'inférence vs attente en file (P95) · Requêtes en attente par pod |
| **Ressources des pods** | CPU par pod (2 cœurs réservés) · Mémoire par pod (limite 2 Gi) |
| **Sécurité et comportement anormal** | Requêtes refusées (401, 503, 404 / scan) · Part de textes toxiques (score > 70) · Longueur des textes (P50, P95) · Alertes en cours |

## 3. Alertes

[config/alert-rules.yml](config/alert-rules.yml) :

| Alerte | Déclencheur | Ce qu'elle révèle |
|---|---|---|
| `LatencySLOViolated` | < 95 % des requêtes en moins d'1 s, 5 min | SLO de latence non tenu |
| `HighErrorRate` | > 1 % de 5xx, 5 min | Erreurs serveur |
| `OverloadShedding` | Des 503 (file pleine), 2 min | Capacité insuffisante |
| `HighCPU` / `HighMemory` | > 90 % des 2 CPU / > 1,7 Go, 5 min | Saturation, fuite mémoire, risque d'arrêt OOM |
| `PodDown` / `BelowMinReplicas` / `ApiMetricsAbsent` | Pod muet / < 3 pods / plus aucun pod | Panne, failover |
| `AuthFailureSpike` | > 30 clés refusées/min, 2 min | Force brute sur les clés |
| `ScanDetected` | > 20 req/min vers des URL inconnues | Scan de vulnérabilités |
| `ToxicityRatioAnomaly` | > 80 % de textes toxiques, 15 min | Dérive des données, abus, modèle défaillant |

## 4. Analyse des logs et comportements anormaux

Méthode : repérer l'heure de l'anomalie sur le tableau de bord, puis filtrer les journaux de l'API sur cette période dans Grafana (**Explore → Cloud Logging**) :

```
resource.type="k8s_container" AND resource.labels.container_name="toxic-api"
jsonPayload.event="auth_failed"          # quelles IP (pseudonymisées) échouent, à quel rythme
jsonPayload.status>=500                  # erreurs serveur et délestage
jsonPayload.latency_ms>2000              # requêtes lentes : quel pod, quelle longueur de texte
```

Cas analysés pendant le projet :

| Situation | Ce que montre la supervision | Analyse |
|---|---|---|
| **Stress** (200 utilisateurs, 12/11) | Latence P95 jusqu'à 17 s, requêtes en attente sur tous les pods, CPU ~60 % seulement, 6 pods | Le CPU n'est pas un bon signal de saturation (inférence séquentielle) : l'autoscaling ne monte pas assez. Voir le [rapport de charge](../05_charge/RAPPORT_CHARGE.md) |
| **Panne d'un pod** (failover, 12/11) | Un pod disparaît puis est recréé ; P95 ×1,5 pendant ~30 s, 0 erreur pendant la panne | Continuité de service assurée par les 3 pods minimum |
| **Attaque DoS reçue** (TP2, 13/11 et 17/11) | Pic de requêtes depuis une seule IP pseudonymisée, `429` Cloud Armor, latence en hausse | Analyse croisée vue attaquant / vue serveur dans [attack/defense_analysis.md](../../attack/defense_analysis.md) ; corrections dans [attack/defense_changes.md](../../attack/defense_changes.md) |
| **Rafale depuis une IP** (local, 19/11) | 26 lignes `429` avec la même IP pseudonymisée en moins d'une seconde | L'IP pseudonymisée est stable : on suit l'abus sans connaître l'IP réelle |

## 5. Captures d'écran

À placer dans [captures/](captures/) (voir la liste dans [captures/README.md](captures/README.md)).
