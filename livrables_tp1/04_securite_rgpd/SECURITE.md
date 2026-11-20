# Étape 4 : sécurisation et conformité RGPD

Extrait du [README](../../README.md) du projet, regroupé ici pour le rendu. Voir aussi le [registre RGPD](REGISTRE_TRAITEMENT.md), la [fiche AI Act](AI_ACT.md), la [grille de tests](../06_supervision_securite/GRILLE_TESTS.md) et les règles Cloud Armor ([cloud-armor-setup.sh](cloud-armor-setup.sh)).

## Authentification (clé d'API)

`POST /model` exige le header `X-API-Key`, sinon réponse `401`. `/health` reste public (sondes Kubernetes, health check du load balancer). Dans `/docs` (avec `ENABLE_DOCS=1`), le bouton **Authorize** permet de saisir la clé.

- **Seules les empreintes sont stockées** : `make_api_key.py` génère une clé aléatoire (`dss_…`, 256 bits) et son empreinte SHA-256. Le client reçoit la clé, l'API ne connaît que l'empreinte (`API_KEY_HASHES`, ou le fichier `API_KEY_HASHES_FILE` monté depuis Secret Manager). Une fuite du secret ne donne donc aucune clé utilisable. Une clé mise par erreur à la place d'une empreinte fait échouer le démarrage.
- **Une empreinte par client**, séparées par des virgules ou des retours à la ligne : on peut révoquer un seul client, ou garder ancienne et nouvelle clé pendant une rotation.
- **Comparaison en temps constant** (`hmac.compare_digest`) : le temps de réponse ne renseigne pas sur la clé.
- **Échec fermé** : sans clé configurée, l'API refuse de démarrer ; `AUTH_DISABLED=1` désactive l'authentification, en local uniquement.
- **Limite** : adapté à un nombre de clients raisonnable (une clé par application cliente). Pour des millions d'utilisateurs finaux, passer à des JWT émis par un fournisseur d'identité (Identity Platform, Auth0...).

## Chiffrement

| Donnée | En transit | Au repos |
|---|---|---|
| Texte analysé | HTTP (choix du projet, pas de HTTPS) : le texte circule en clair entre le client et le load balancer ; load balancer → pod dans le réseau Google, chiffré au niveau réseau par Google | Jamais stocké : ni base, ni logs applicatifs, pas dans l'URL ; `Cache-Control: no-store` sur les réponses |
| Clé d'API | Header `X-API-Key`, en clair en HTTP (HTTPS recommandé en production réelle) | Nulle part : seule son empreinte SHA-256 est stockée, dans Secret Manager (chiffré AES-256 par Google, accès IAM restreint, tracé) |
| Modèles (image) | Push/pull en HTTPS vers Artifact Registry | Chiffrés par Google dans Artifact Registry |

L'API est exposée en HTTP simple : l'en-tête `Strict-Transport-Security` (HSTS) est envoyé mais sans effet tant que le service n'est pas en HTTPS.

## Journal d'accès pseudonymisé

Chaque requête produit une ligne JSON sur la sortie standard (`toxicity/audit_log.py`), récupérée par Cloud Logging :

```json
{"timestamp": "2026-11-12T10:42:03.512+00:00", "severity": "INFO", "message": "POST /model 200",
 "event": "request", "request_id": "9f2c41d07a6b4e15", "method": "POST", "path": "/model", "status": 200,
 "latency_ms": 84.2, "client": "client_5b1e0c9a3f2d7e64", "ip": "ip_c07d18a2e94b3f51",
 "model": "bert", "text_length": 16, "toxicity_score": 99.9}
```

- **Jamais journalisé** : le texte analysé, la clé d'API, l'IP en clair. Le journal d'accès d'uvicorn (`--no-access-log`) et les logs du load balancer (`BackendConfig`) sont désactivés, car ils contiennent les IP en clair.
- **Pseudonymes** : `client` et `ip` sont des HMAC-SHA256 avec une clé secrète (`toxic-api-log-key` dans Secret Manager). Irréversibles sans la clé, mais stables : on peut suivre l'activité d'un client ou d'une IP (abus, échecs d'authentification répétés) sans savoir qui c'est.
- **Événements** : `request` (INFO, ou WARNING/ERROR selon le statut) et `auth_failed` (401, WARNING). `/health` n'est journalisé qu'en cas d'erreur.
- **`X-Request-ID`** : renvoyé dans chaque réponse, il permet à un client de signaler une requête précise.
- **Conservation** : 30 jours (bucket `_Default` de Cloud Logging).

Exemples de recherche dans Cloud Logging (**Logging > Explorateur de journaux**) :

```
resource.type="k8s_container" AND resource.labels.container_name="toxic-api"
jsonPayload.event="auth_failed"                    # tentatives avec une clé invalide
jsonPayload.ip="ip_c07d18a2e94b3f51"               # toute l'activité d'une même IP
jsonPayload.latency_ms > 1000                      # requêtes lentes
```

## IAM (moindre privilège)

| Identité | Droit | Sur quoi |
|---|---|---|
| Compte de service Kubernetes `toxic-api` (Workload Identity) | `secretmanager.secretAccessor` | Les seuls secrets `toxic-api-keys` et `toxic-api-log-key` |
| `toxic-api` | `storage.objectViewer` | Le seul bucket des modèles (monté en lecture seule) |
| Compte de service `grafana` (namespace `monitoring`) | `logging.viewer` | Lecture des journaux, rien d'autre |
| Prometheus | RBAC Kubernetes : lister les pods | Namespace `default` uniquement |
| Équipe projet | Owners du projet GCP | Administration ; accès aux secrets tracés dans Cloud Audit Logs |

Commandes complètes : README, section « Déploiement (Docker / GKE) ».

## Protection contre les abus

- **Cloud Armor** (au load balancer) : 100 requêtes/min/IP puis `429`, IP bannie 10 min au-delà de 200 req/min, corps de plus de 4096 octets rejetés (`403`).
- **API** : corps limité à 4096 octets (`413`), texte à 1500 caractères (`422`), 4 requêtes simultanées max par IP et par pod (`429`), file d'attente bornée et équitable (`503` + `Retry-After`).
- **Surface réduite** : `/docs` et `/openapi.json` fermés, conteneur non-root avec système de fichiers en lecture seule, seule la décision `is_toxic` est renvoyée (pas de probabilités : limite l'extraction du modèle).

> ⚠️ **Limite assumée : pas de HTTPS.** L'API est exposée en HTTP (`http://136.82.246.21/`, `allow-http: "true"` dans `k8s/toxic-api.yaml`). La clé d'API et le texte analysé circulent en clair sur Internet : un attaquant sur le trajet pourrait lire ou rejouer la clé. Atténuations en place : le texte n'est jamais stocké, la clé est révocable par client, Cloud Armor limite le débit par IP. Pour passer en HTTPS : activer le certificat géré (`toxic-api-cert`, déjà dans le manifeste) avec un nom de domaine, puis `allow-http: "false"`.
