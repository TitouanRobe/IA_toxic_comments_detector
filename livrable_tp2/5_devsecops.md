# Étape 5 — Pipeline DevSecOps

**Fichier :** [.github/workflows/devsecops.yml](../.github/workflows/devsecops.yml) (GitHub Actions).
**Déclenchement :** chaque push sur `main` et chaque pull request. Les 10 jobs tournent en parallèle ; **un seul échec met la pipeline en échec** et bloque la fusion d'une pull request.

## 1. Ce que vérifie la pipeline

| # | Job | Outil (version fixée) | Menace ou exigence couverte |
|---|---|---|---|
| 1 | `gitleaks` | gitleaks 8.21.2 | **Secrets** (clés d'API, mots de passe, jetons) dans le code et dans **tout l'historique git** |
| 2 | `pip-audit` | pip-audit (via `uvx`) | **Dépendances vulnérables** : failles connues (CVE) dans les versions exactes de `uv.lock` |
| 3 | `bandit` | bandit | **Failles dans le code Python** (désérialisation dangereuse, sous-processus, etc.), sévérité moyenne et plus |
| 4 | `ruff` | ruff | Bugs probables et règles de sécurité (`S`, équivalent bandit), style |
| 5 | `hadolint` | hadolint 2.12.0 | **Dockerfile** : bonnes pratiques (utilisateur non-root, versions, couches) |
| 6 | `trivy-image` | trivy 0.58.1 | **Image Docker** construite comme en production (amd64) : failles HIGH et CRITICAL corrigibles |
| 7 | `kubeconform` | kubeconform 0.6.7 | **Manifeste Kubernetes** valide (schéma strict) |
| 8 | `kube-linter` | kube-linter 0.7.1 | **Sécurité du déploiement** : non-root, système de fichiers en lecture seule, ressources limitées… |
| 9 | `tests` | pytest | **Non-régression des défenses du TP2** ([tests/](../tests/)) : en-têtes de sécurité, docs fermées, `413`/`422`, réponse sans scores, plafond par IP (`429`), file équitable |
| 10 | `model-card` | [ci/check_model_card.py](../ci/check_model_card.py) | **Conformité IA Act** : `model_card.json` complète (champs du sujet), niveau de risque autorisé et justifié, biais documenté, documents cités présents |

Principes de sécurité de la pipeline elle-même :
- **droits minimaux** : `permissions: contents: read`, le workflow ne peut rien écrire dans le dépôt ;
- **outils à version fixée**, lancés en conteneur ou via `uvx` : le même résultat se reproduit en local avec la même commande ;
- **exceptions documentées** là où elles sont déclarées (`[tool.ruff]` et `[tool.bandit]` dans [pyproject.toml](../pyproject.toml), commentaires `# noqa` / `# nosec`, [.gitleaks.toml](../.gitleaks.toml)) : aucune règle n'est désactivée sans justification écrite.

## 2. Problèmes réels trouvés et corrigés grâce à la pipeline

| Outil | Problème détecté | Correction | Commit |
|---|---|---|---|
| bandit (B614) | `torch.load` sans `weights_only` : un fichier de poids modifié (bucket compromis) pouvait exécuter du code au chargement | `weights_only=True` dans `toxicity/models/lstm.py` et `laya.py` | `7f5b64c` |
| kube-linter | Système de fichiers du conteneur inscriptible | `readOnlyRootFilesystem: true` + volume `/tmp` dédié dans `k8s/toxic-api.yaml` | `7f5b64c` |
| trivy | Paquets système de l'image de base avec failles corrigées par Debian mais pas encore intégrées | `apt-get upgrade` dans le `Dockerfile` | `68dadeb` |
| ruff | `except Exception: pass` silencieux, variables inutilisées, imports non triés | Corrections ou exception justifiée (`# noqa: S110`) | `7f5b64c` |
| gitleaks | Analyse de l'historique de plus de 10 min à cause des CSV de données | Exclusion justifiée de `data/` (commentaires publics, sans secret) | `9d884f8` |

## 3. Logs d'exécution

**Logs locaux** : les 5 contrôles qui ne demandent pas Docker, avec les mêmes commandes que la CI, rejoués par [ci/local_checks.sh](../ci/local_checks.sh) le 19/11/2026 (`bash ci/local_checks.sh`, un log par contrôle dans [logs/](logs/), code de sortie inclus).

| Contrôle | Commande | Résultat | Log |
|---|---|---|---|
| ruff | `uvx ruff check .` | Aucune erreur | [ruff.log](logs/ruff.log) |
| bandit | `uvx bandit -c pyproject.toml -r . -ll` | Aucun problème de sévérité moyenne ou haute (1 814 lignes analysées) | [bandit.log](logs/bandit.log) |
| pip-audit | `uvx pip-audit -r <export de uv.lock>` | Aucune vulnérabilité connue | [pip-audit.log](logs/pip-audit.log) |
| tests | `uv run pytest -v` | 13 tests réussis | [pytest.log](logs/pytest.log) |
| model-card | `python ci/check_model_card.py` | Conforme (risque minimal, v2.0) | [model-card.log](logs/model-card.log) |

> Limite de pip-audit : `torch` provient de l'index PyTorch et non de PyPI, il n'est pas audité par cet outil (signalé dans le log). Il est couvert par le scan de l'image (trivy), qui inspecte aussi les paquets Python installés dans l'image.

Les jobs qui tournent dans Docker (`gitleaks`, `hadolint`, `trivy-image`, `kubeconform`, `kube-linter`) n'ont pas été relancés en local : leurs logs sont ceux de GitHub Actions.

**Logs GitHub Actions** (le dépôt est privé : à récupérer par un membre de l'équipe) :
1. GitHub → onglet **Actions** → workflow **DevSecOps** → dernier run sur `main` ;
2. bouton **⋯ → Download log archive**, puis décompresser dans `livrable_tp2/logs/github/` ;
3. ou en ligne de commande : `gh run download` / `gh run view <id> --log > livrable_tp2/logs/github/run.log`.

Une capture de la page du run (10 jobs verts) suffit comme preuve visuelle dans le rapport.

## 4. Limites et améliorations possibles

- **Pas de déploiement continu** : la pipeline vérifie mais ne déploie pas. Étape suivante : un job qui construit l'image, la pousse dans Artifact Registry et l'applique sur GKE uniquement si tous les contrôles passent (authentification par Workload Identity Federation, sans clé de compte de service stockée dans GitHub).
- **Actions tierces épinglées par tag** (`actions/checkout@v4`) et non par empreinte SHA : un tag peut être déplacé par l'éditeur. Épingler par SHA protège contre une compromission de l'action.
- **Pas de test d'attaque automatisé** : `attack/bench_defense.py` pourrait tourner dans un job planifié (hebdomadaire) pour détecter une régression de performance sous charge.
- **Pas de signature d'image** : Binary Authorization sur GKE n'accepterait que des images signées par la pipeline.
