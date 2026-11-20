# Grille de tests : sécurité, stress, failover

Légende du statut : ✅ conforme (résultat mesuré) · ⚠️ partiel ou limite connue · ⏳ à revérifier en production

Sources des preuves :
- **L-19/11** : tests en local du 19/11/2026, même code que la production ([exemples](../03_api/EXEMPLES_REQUETES.md)) ;
- **Charge 12/11** : tests Locust contre la production ([rapport](../05_charge/RAPPORT_CHARGE.md)) ;
- **TP2** : attaques DoS reçues et défenses ([attack/](../../attack/)) ;
- **CI** : pipeline GitHub Actions `.github/workflows/devsecops.yml`, à chaque push.

## 1. Sécurité : authentification et contrôle d'accès

| # | Test | Méthode | Attendu | Obtenu | Statut | Preuve |
|---|---|---|---|---|---|---|
| S1 | Appel sans clé d'API | `POST /model` sans `X-API-Key` | `401`, modèle non exécuté | `401 {"detail":"Clé d'API absente ou invalide"}` | ✅ | L-19/11 |
| S2 | Clé invalide | `X-API-Key: dss_faux` | `401` + événement `auth_failed` journalisé | `401`, ligne `auth_failed` avec IP pseudonymisée | ✅ | L-19/11 |
| S3 | Clé valide | Clé générée par `make_api_key.py` | `200` | `200 {"is_toxic":true}` | ✅ | L-19/11 |
| S4 | Clé non stockée côté serveur | Lecture du code et du secret | Seule l'empreinte SHA-256 est connue de l'API | `API_KEY_HASHES` ; une clé en clair à la place d'une empreinte fait échouer le démarrage | ✅ | `api.py` |
| S5 | Démarrage sans clé configurée | Lancer l'API sans `API_KEY_HASHES` | Refus de démarrer (échec fermé) | L'API refuse de démarrer | ✅ | `api.py` |
| S6 | Comparaison en temps constant | Lecture du code | Pas de fuite d'information par le temps de réponse | `hmac.compare_digest` | ✅ | `api.py` |
| S7 | Force brute sur les clés | > 30 clés refusées/min | Alerte | Règle `AuthFailureSpike` | ✅ | `k8s/monitoring/alert-rules.yml` |
| S8 | Accès aux secrets (IAM) | Politique IAM des secrets | Seul le compte de service `toxic-api` lit les secrets ; accès tracés | Workload Identity + `secretmanager.secretAccessor` sur ce seul compte, Cloud Audit Logs | ✅ | README §Déploiement |
| S9 | Supervision non exposée | Ports publiés par le Service et l'Ingress | Port 8081 (métriques), Grafana et Prometheus inaccessibles depuis Internet | Accès uniquement par `kubectl port-forward` | ✅ | `k8s/` |

## 2. Sécurité : validation des entrées

| # | Test | Méthode | Attendu | Obtenu | Statut | Preuve |
|---|---|---|---|---|---|---|
| V1 | Texte vide | `{"text": ""}` | `422` | `422 string_too_short` | ✅ | L-19/11 |
| V2 | Texte > 1500 caractères | 1501 × `a` | `422` | `422 string_too_long` | ✅ | L-19/11 |
| V3 | Corps > 4096 octets | 5000 × `a` | `413` avant lecture du corps | `413` en 0,3 ms | ✅ | L-19/11 |
| V4 | Corps > 4096 octets en production | idem, via le load balancer | `403` Cloud Armor, la requête n'atteint pas le pod | Règle Cloud Armor priorité 900 | ⏳ | `k8s/cloud-armor-setup.sh` |
| V5 | JSON mal formé | `{text: oops` | `422` | `422 json_invalid` | ✅ | L-19/11 |
| V6 | Modèle inconnu | `"model": "xyz"` | `404` | `404 Modèle 'xyz' non disponible` | ✅ | L-19/11 |
| V7 | Documentation fermée | `GET /docs`, `/openapi.json` | `404` | `404` | ✅ | L-19/11 |
| V8 | Scan d'URL inconnues | > 20 req/min vers des chemins inexistants | Alerte, pas d'explosion des séries de métriques | Règle `ScanDetected`, labels de chemins bornés | ✅ | `alert-rules.yml`, `toxicity/metrics_prom.py` |

## 3. Sécurité : chiffrement et confidentialité (RGPD)

| # | Test | Méthode | Attendu | Obtenu | Statut | Preuve |
|---|---|---|---|---|---|---|
| C1 | Chiffrement du trafic client | Configuration de l'Ingress | HTTPS | **Non retenu** : API exposée en HTTP (`http://136.82.246.21/`), clé d'API et texte en clair sur le réseau ; choix assumé du projet, atténuations dans [SECURITE.md](../04_securite_rgpd/SECURITE.md) | ⚠️ | `k8s/toxic-api.yaml` |
| C2 | Trafic interne chiffré | Load balancer → pods, accès aux services Google | Chiffré au niveau réseau / TLS | Réseau Google ; API Google en TLS (Secret Manager, Cloud Storage, Artifact Registry) | ✅ | `ARCHITECTURE_CLOUD.md` |
| C3 | En-têtes de sécurité | En-têtes de réponse | CSP, `X-Frame-Options`, `nosniff`, `no-referrer`, `no-store` (HSTS envoyé, sans effet en HTTP) | Tous présents | ✅ | L-19/11 |
| C4 | Texte jamais journalisé | Lecture du journal après des requêtes | Ni texte, ni clé, ni IP en clair | Seulement `text_length` et `toxicity_score` ; IP et client en HMAC-SHA256 | ✅ | L-19/11 |
| C5 | Pas d'IP en clair ailleurs | Configuration | Journal d'accès uvicorn et logs du load balancer désactivés | `--no-access-log`, `BackendConfig` sans logging | ✅ | `Dockerfile`, `k8s/toxic-api.yaml` |
| C6 | Pas d'extraction facile du modèle | Réponse de `/model` | Décision seulement, pas de probabilités | `{"is_toxic": …}` uniquement | ✅ | L-19/11 |
| C7 | Données d'entraînement anonymisées | `anonymize.py` + regex de contrôle | Plus de PII détectable par les regex | 40 % des commentaires modifiés ; limites documentées | ⚠️ | [Rapport d'anonymisation](../01_donnees/ANONYMISATION.md) |

## 4. Sécurité : chaîne de livraison (CI)

| # | Test | Outil | Attendu | Statut |
|---|---|---|---|---|
| CI1 | Secrets dans le code et l'historique git | gitleaks | Aucun | ✅ (bloquant) |
| CI2 | Dépendances vulnérables | pip-audit (`uv.lock`) | Aucune faille connue | ✅ (bloquant) |
| CI3 | Failles dans le code Python | bandit, ruff | Aucune de sévérité moyenne ou plus | ✅ (bloquant) |
| CI4 | Image Docker | trivy | Aucune faille HIGH/CRITICAL corrigeable | ✅ (bloquant) |
| CI5 | Dockerfile, manifeste Kubernetes | hadolint, kubeconform, kube-linter | Non-root, système de fichiers en lecture seule, manifeste valide | ✅ (bloquant) |

## 5. Stress et abus

| # | Test | Méthode | Attendu | Obtenu | Statut | Preuve |
|---|---|---|---|---|---|---|
| T1 | Montée progressive | Locust `ramp`, 5 → 120 utilisateurs | Pas d'erreur, autoscaling | 0,00 % d'erreurs, 3 → 6 pods ; P95 > 1 s au-delà de ~40 utilisateurs | ⚠️ latence | Charge 12/11 |
| T2 | Afflux brutal | Locust `rapid`, 0 → 120 en 12 s | Le service ne tombe pas | 0,01 % d'erreurs, ~2 min pour passer à 6 pods | ✅ | Charge 12/11 |
| T3 | Charge soutenue 30 min | Locust `soak`, 60 utilisateurs | Latence et mémoire stables | 0 erreur / 70 112 requêtes, mémoire max 1,3 Gio < 2 Gi, pas de fuite | ✅ | Charge 12/11 |
| T4 | Au-delà de la capacité | Locust `stress`, 200 utilisateurs | Dégradation contrôlée | 0,03 % d'erreurs, mais P95 17 s (avant délestage) | ⚠️ corrigé depuis par T6 | Charge 12/11 |
| T5 | Rafale d'une seule IP | 30 requêtes simultanées | Au plus 4 traitées, le reste refusé vite | 26 × `429`, 4 acceptées | ✅ | L-19/11 |
| T6 | Saturation de la file | `attack/dos.py`, 100 clients, textes de 1400 caractères | Refus rapide plutôt qu'attente | `503` en ~10 ms ; latence des requêtes acceptées : P50 1 741 → 74 ms | ✅ | TP2 §8d |
| T7 | `/health` sous charge | Sonde chaque seconde pendant T6 | Répond vite (sinon pod retiré à tort) | 620-830 ms → 2-12 ms | ✅ | TP2 §8d |
| T8 | Limite de débit par IP | > 100 req/min depuis une IP | `429` Cloud Armor ; ban 10 min au-delà de 200 req/min | 79-100 % de `429` pour l'attaquant (13/11) ; ban à revérifier | ⏳ | TP2 |
| T9 | Attaque DoS reçue (autre groupe, prof) | Attaques réelles du TP2 | Service maintenu, attaque visible en supervision | 0 % d'erreur côté API sous l'attaque du 13/11 | ✅ | `attack/defense_analysis.md` |

## 6. Failover et continuité de service

| # | Test | Méthode | Attendu | Obtenu | Statut | Preuve |
|---|---|---|---|---|---|---|
| F1 | Perte d'un pod sous charge | Locust `failover` : 40 utilisateurs, suppression d'un pod à t = 300 s | Pas d'interruption | 0 erreur pendant la panne ; P95 ×1,5 pendant ~30 s ; 2 × `502` sur tout le test (0,01 %) | ✅ | Charge 12/11 |
| F2 | Recréation automatique | Même test | Nouveau pod créé par le `Deployment` | 4 pods prêts à chaque relevé du HPA (toutes les 15 s) autour de la panne | ✅ | `failover_hpa.txt` |
| F3 | Nombre minimal de pods | HPA `minReplicas: 3` | La perte d'un pod laisse 2 pods | Validé par F1 | ✅ | `k8s/toxic-api.yaml` |
| F4 | Pod non prêt retiré du trafic | Sondes `startupProbe` / `readinessProbe` sur `/health` | Pas de trafic vers un pod qui charge son modèle | Modèle préchargé et préchauffé avant que `/health` réponde | ✅ | `api.py`, `k8s/toxic-api.yaml` |
| F5 | Détection de la panne | Alertes | Alerte quand un pod tombe ou qu'il en reste moins de 3 | `PodDown`, `BelowMinReplicas`, `ApiMetricsAbsent` | ✅ | `alert-rules.yml` |
| F6 | Mise à jour sans coupure | Nouveau tag d'image, `kubectl apply` | Remplacement progressif des pods | Rolling update ; retour arrière par l'ancien tag | ✅ | README |
| F7 | Perte d'une zone | Cluster régional Autopilot (pods répartis sur plusieurs zones) | Service maintenu | Non testé (pas de moyen de couper une zone) | ⏳ | — |

## 7. Bilan

| Domaine | Tests | ✅ | ⚠️ | ⏳ |
|---|---|---|---|---|
| Authentification et accès | 9 | 9 | 0 | 0 |
| Validation des entrées | 8 | 7 | 0 | 1 |
| Chiffrement et confidentialité | 7 | 5 | 2 | 0 |
| Chaîne de livraison (CI) | 5 | 5 | 0 | 0 |
| Stress et abus | 9 | 6 | 2 | 1 |
| Failover | 7 | 6 | 0 | 1 |
| **Total** | **45** | **38** | **4** | **3** |

Points restants : rejouer en production les tests ⏳ (`403` Cloud Armor, ban d'IP), et mesurer la latence sous `stress` avec le délestage déployé pour clore T4.
