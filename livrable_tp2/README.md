# TP2 — Digital Social Score : de la menace à la défense

**Équipe :** Cléo Martin-Colleu, Titouan Robe — ESIGELEC, Déploiement de solutions IA et cybersécurité.
**Système :** API de détection de commentaires toxiques (DistilBERT affiné), déployée sur GKE Autopilot (`europe-west1`) derrière un load balancer Google et Cloud Armor.
**Attaque étudiée :** déni de service sur le modèle d'IA (DoS applicatif sur l'endpoint d'inférence).

## Résultats en bref

| | Version attaquée (vague 6) | Version défendue |
|---|---|---|
| Latence médiane des clients légitimes, flood depuis 1 IP | 1 001 ms | **81 ms** |
| Commentaires toxiques correctement signalés par minute, sous attaque | 88 | **350** (390 sans attaque) |
| Latence médiane, flood réparti sur 25 IP | 2 029 ms | 223 ms, mais 57 % de refus |
| Précision / taux de faux positifs du modèle (400 textes, seuil 70) | 88,1 % / 13,0 % | 88,1 % / 13,0 % : l'attaque ne change aucune décision |

**L'attaque qui a le plus compté : l'audit du professeur (17/11).** Sa charge a atteint notre modèle (7712 requêtes acceptées en 92 s, latence ×11), il a pu commencer à extraire le modèle via les scores renvoyés, et il a trouvé `/model` grâce à la documentation publique. Chaque constat a donné lieu à une correction : ban par IP réellement déployable, plafond de requêtes par IP, délestage rapide, file équitable, réponse réduite à la décision, documentation fermée, en-têtes complétés ([2_attaque.md §4](2_attaque.md), [3_defense.md §1](3_defense.md)).

L'attaque vise la **disponibilité** de la modération, pas l'exactitude du modèle. Les défenses rendent l'API résistante à un flood depuis une IP sans aucun coût en conditions normales ; contre une attaque très distribuée, elles limitent les dégâts sans les supprimer ([3_defense.md](3_defense.md)).

## Livrables demandés → fichiers

| Étape | Livrable demandé par le sujet | Fichier |
|---|---|---|
| 1 | Fiche d'analyse « Attaque IA » (1 page) | [1_fiche_attaque.md](1_fiche_attaque.md) |
| 2 | Script d'attaque dans `/attack`, graphique des métriques, interprétation | [2_attaque.md](2_attaque.md) (+ code : [attack/](../attack/)) |
| 3 | Tableau des métriques avant/après, graphique de comparaison | [3_defense.md](3_defense.md) (+ [donnees/tableau_avant_apres.md](donnees/tableau_avant_apres.md), [figures/](figures/)) |
| 4 | Fiche IA Act (tableau de risque et justification), `model_card.json` à la racine | [4_fiche_ia_act.md](4_fiche_ia_act.md), [../model_card.json](../model_card.json) |
| 5 | Pipeline intégrant des étapes de sécurité, logs d'exécution | [5_devsecops.md](5_devsecops.md), [logs/](logs/) |
| — | Analyse critique et recommandations | [6_analyse_critique.md](6_analyse_critique.md) |
| — | Où se trouve le code de chaque étape | [rendu.md](rendu.md) |

## Critères d'évaluation → où y répondre

| Critère | Ce que nous apportons |
|---|---|
| Réalisme de l'attaque | Attaque menée contre l'API réelle d'un autre groupe ([2_attaque.md §1](2_attaque.md)), et attaques subies en production (un groupe, puis le professeur) analysées côté attaquant **et** côté serveur |
| Efficacité de la défense | Même attaque rejouée sur la version attaquée et sur la version défendue, avec précision, faux positifs, latence et disponibilité mesurés pour des clients légitimes ([3_defense.md](3_defense.md)) |
| Conformité IA Act | Classement justifié niveau par niveau, cas qui changeraient le classement, registre des risques, model card vérifiée automatiquement ([4_fiche_ia_act.md](4_fiche_ia_act.md)) |
| Pipeline DevSecOps | 10 contrôles automatiques à chaque push, dont tests des défenses et conformité de la model card ; problèmes réels trouvés et corrigés ([5_devsecops.md](5_devsecops.md)) |
| Rigueur et clarté | Protocole fixé et reproductible (graine, commit, commandes), limites de chaque mesure écrites à côté du résultat |
| Originalité et réflexion | File d'attente équitable qui éjecte le spammeur plutôt que le nouvel arrivant ; arbitrage transparence / anti-extraction ; mise en évidence de la limite des défenses par IP face à une attaque distribuée ([6_analyse_critique.md](6_analyse_critique.md)) |

## Reproduire les résultats

```bash
uv sync                                                                   # dépendances
uv run python attack/bench_defense.py run --ref 451d731 --label avant      # version attaquée (~5 min)
uv run python attack/bench_defense.py run --label apres                    # version défendue (~5 min)
uv run python attack/bench_defense.py report                               # tableau + graphiques
uv run pytest -v                                                           # tests des défenses
python ci/check_model_card.py                                              # conformité de la model card
```

Prérequis : les poids entraînés dans `artifacts/bert/` (voir le [README du projet](../README.md), entraînement ou copie depuis le bucket) et les données Jigsaw dans `data/`.

## Contenu du dossier

```
livrable_tp2/
├── README.md               ce sommaire
├── 1_fiche_attaque.md      étape 1
├── 2_attaque.md            étape 2
├── 3_defense.md            étape 3
├── 4_fiche_ia_act.md       étape 4
├── 5_devsecops.md          étape 5
├── 6_analyse_critique.md   analyse critique et recommandations
├── rendu.md                carte du code
├── donnees/                résultats bruts du banc (JSON) et tableau avant/après
├── figures/                graphiques SVG
└── logs/                   logs d'exécution des contrôles de la pipeline
```
