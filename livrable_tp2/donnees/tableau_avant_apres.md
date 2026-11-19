Généré par `uv run python attack/bench_defense.py report` à partir de `bench-avant.json` (commit 451d731) et `bench-apres.json` (commit 68dadeb).

### Sans attaque

| Indicateur (clients légitimes) | Avant | Après |
|---|---|---|
| Requêtes légitimes servies (200) | 100 % | 100 % |
| Commentaires légitimes analysés par seconde | 13.6 | 13.7 |
| Latence p50 des requêtes servies | 39 ms | 36.8 ms |
| Latence p95 des requêtes servies | 77.5 ms | 72.2 ms |
| Latence p95, refus compris | 77.5 ms | 72.2 ms |
| Précision (requêtes servies) | 89 % | 88.8 % |
| Taux de faux positifs (requêtes servies) | 11.7 % | 11.9 % |
| Rappel (requêtes servies) | 96.7 % | 96.7 % |
| Toxiques effectivement signalés (sur tous les toxiques envoyés) | 96.7 % | 96.7 % |
| Toxiques correctement signalés par minute | 390 | 393 |
| Décisions identiques à celles sans attaque (mêmes textes) | 100 % | 100 % |
| Requêtes légitimes envoyées en 40 s | 543 | 549 |
| Codes HTTP légitimes | {'200': 543} | {'200': 549} |

### Flood 1 IP

| Indicateur (clients légitimes) | Avant | Après |
|---|---|---|
| Requêtes légitimes servies (200) | 100 % | 100 % |
| Commentaires légitimes analysés par seconde | 3.2 | 11.9 |
| Latence p50 des requêtes servies | 1 001 ms | 81 ms |
| Latence p95 des requêtes servies | 1 185 ms | 119.3 ms |
| Latence p95, refus compris | 1 185 ms | 119.3 ms |
| Précision (requêtes servies) | 92.2 % | 89.3 % |
| Taux de faux positifs (requêtes servies) | 7.6 % | 11.8 % |
| Rappel (requêtes servies) | 96.7 % | 96.7 % |
| Toxiques effectivement signalés (sur tous les toxiques envoyés) | 96.7 % | 96.7 % |
| Toxiques correctement signalés par minute | 88.5 | 349.5 |
| Décisions identiques à celles sans attaque (mêmes textes) | 100 % | 100 % |
| Requêtes légitimes envoyées en 40 s | 127 | 478 |
| Codes HTTP légitimes | {'200': 127} | {'200': 478} |
| Attaquant : requêtes envoyées | 2138 | 2127 |
| Attaquant : part atteignant le modèle (200) | 100 % | 73.4 % |
| Attaquant : codes HTTP | {'200': 2138} | {'429': 565, '200': 1562} |

### Flood 25 IP

| Indicateur (clients légitimes) | Avant | Après |
|---|---|---|
| Requêtes légitimes servies (200) | 100 % | 43 % |
| Commentaires légitimes analysés par seconde | 1.8 | 4.8 |
| Latence p50 des requêtes servies | 2 029 ms | 222.8 ms |
| Latence p95 des requêtes servies | 2 282 ms | 260.6 ms |
| Latence p95, refus compris | 2 282 ms | 247.1 ms |
| Précision (requêtes servies) | 95 % | 87.7 % |
| Taux de faux positifs (requêtes servies) | 5.9 % | 16.1 % |
| Rappel (requêtes servies) | 97.4 % | 96.2 % |
| Toxiques effectivement signalés (sur tous les toxiques envoyés) | 97.4 % | 44.1 % |
| Toxiques correctement signalés par minute | 57 | 150 |
| Décisions identiques à celles sans attaque (mêmes textes) | 100 % | 100 % |
| Requêtes légitimes envoyées en 40 s | 73 | 444 |
| Codes HTTP légitimes | {'200': 73} | {'503': 253, '200': 191} |
| Attaquant : requêtes envoyées | 2354 | 4018 |
| Attaquant : part atteignant le modèle (200) | 100 % | 39.2 % |
| Attaquant : codes HTTP | {'200': 2354} | {'503': 2443, '200': 1575} |
