# Captures de supervision : à faire

Aucune capture d'écran n'existe encore dans le projet. Elles demandent le cluster en marche, avec du trafic.

## Préparer

```bash
# Reprendre le cluster (README, « Suspendre / reprendre »), puis :
kubectl -n monitoring port-forward svc/grafana 3000:3000        # http://localhost:3000
kubectl -n monitoring port-forward svc/prometheus 9090:9090     # http://localhost:9090/alerts
uv run python loadtest/run.py ramp                              # du trafic pendant les captures
```

## Captures attendues

| Fichier | Contenu |
|---|---|
| `01_dashboard_vue_ensemble.png` | Rangée « Vue d'ensemble » pendant un test de charge |
| `02_latence_p50_p95_p99.png` | Latence et requêtes en attente pendant la montée en charge |
| `03_pods_cpu_memoire.png` | Pods qui passent de 3 à 6, CPU et mémoire par pod |
| `04_failover.png` | Pendant `loadtest/run.py failover` : un pod disparaît, latence, aucune erreur |
| `05_alertes_prometheus.png` | Page `/alerts` de Prometheus avec au moins une alerte déclenchée (ex. `LatencySLOViolated` sous `stress`) |
| `06_securite_401_429.png` | Rangée « Sécurité » après des requêtes sans clé et une rafale |
| `07_logs_pseudonymises.png` | Explore → Cloud Logging, filtre `jsonPayload.event="auth_failed"` : IP pseudonymisées |

En attendant, les rapports Locust de [../../05_charge/results/](../../05_charge/results/) (`*.html`) contiennent les graphiques de débit, latence et nombre d'utilisateurs de chaque test.
