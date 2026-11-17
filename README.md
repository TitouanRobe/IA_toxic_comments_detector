# IA_toxic_comments_detector

Détection de toxicité : chaque texte reçoit un **score de 0 à 100** (= 100 × probabilité maximale parmi les 6 classes Jigsaw : `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`).

## Installation

```bash
uv sync   # inférence + groupes "train" (spaCy, polars, scikit-learn) et "dev"
```

- **Windows (RTX 5080)** : torch est installé depuis l'index PyTorch CUDA 13.0 (compatible driver CUDA 13.2). Vérifier : `uv run python -c "import torch; print(torch.cuda.get_device_name())"`.
- **Mac (M3)** : torch PyPI, entraînement sur MPS.

## Utilisation

```bash
# Test rapide (Mac)
uv run python train.py --model bert --limit 2000 --epochs 1 --max-length 128 --test-limit 1000

# Entraînement complet (Windows / GPU)
uv run python train.py --model bert

# LSTM (BiLSTM entraîné from scratch, batch 128 et lr 1e-3 par défaut)
uv run python train.py --model lstm --limit 2000 --epochs 1 --max-length 128 --test-limit 1000   # test rapide Mac
uv run python train.py --model lstm --epochs 5                                                     # complet (Windows / GPU)

# LAYA (encodeur ModernBERT-large de convaiinnovations/laya + tête 6 classes, batch 16 et lr 1e-5 par défaut)
# ~840 Mo téléchargés au premier lancement (cache Hugging Face) ; lent sur Mac, GPU conseillé.
uv run python train.py --model laya --limit 2000 --epochs 1 --max-length 128 --test-limit 1000   # test rapide Mac
uv run python train.py --model laya --epochs 2                                                     # complet (Windows / GPU)

# Score d'un texte
uv run python predict.py --model bert "You are an idiot"

# Comparaison des modèles entraînés
uv run python compare.py

# API d'inférence (documentation interactive : http://127.0.0.1:8000/docs, avec ENABLE_DOCS=1 ;
# désactivée par défaut et en production)
uv run python make_api_key.py          # affiche une clé (pour le client) et son empreinte (pour l'API)
ENABLE_DOCS=1 API_KEY_HASHES=<empreinte> uv run uvicorn api:app --reload
curl -X POST http://127.0.0.1:8000/model -H "Content-Type: application/json" \
     -H "X-API-Key: <clé>" -d '{"text": "You are an idiot", "model": "bert"}'
```

### Authentification (clé d'API)

`POST /model` exige le header `X-API-Key`, sinon réponse `401`. `/health` reste public (sondes Kubernetes, health check du load balancer). Dans `/docs` (avec `ENABLE_DOCS=1`), le bouton **Authorize** permet de saisir la clé.

- **Seules les empreintes sont stockées** : `make_api_key.py` génère une clé aléatoire (`dss_…`, 256 bits) et son empreinte SHA-256. Le client reçoit la clé, l'API ne connaît que l'empreinte (`API_KEY_HASHES`, ou le fichier `API_KEY_HASHES_FILE` monté depuis Secret Manager). Une fuite du secret ne donne donc aucune clé utilisable. Une clé mise par erreur à la place d'une empreinte fait échouer le démarrage.
- **Une empreinte par client**, séparées par des virgules ou des retours à la ligne : on peut révoquer un seul client, ou garder ancienne et nouvelle clé pendant une rotation.
- **Comparaison en temps constant** (`hmac.compare_digest`) : le temps de réponse ne renseigne pas sur la clé.
- **Échec fermé** : sans clé configurée, l'API refuse de démarrer ; `AUTH_DISABLED=1` désactive l'authentification, en local uniquement.
- **Limite** : adapté à un nombre de clients raisonnable (une clé par application cliente). Pour des millions d'utilisateurs finaux, passer à des JWT émis par un fournisseur d'identité (Identity Platform, Auth0...).

### Chiffrement

| Donnée | En transit | Au repos |
|---|---|---|
| Texte analysé | HTTPS uniquement (port 80 fermé sur le load balancer) ; load balancer → pod dans le réseau Google, chiffré au niveau réseau par Google | Jamais stocké : ni base, ni logs applicatifs, pas dans l'URL ; `Cache-Control: no-store` sur les réponses |
| Clé d'API | Header `X-API-Key`, uniquement en HTTPS | Nulle part : seule son empreinte SHA-256 est stockée, dans Secret Manager (chiffré AES-256 par Google, accès IAM restreint, tracé) |
| Modèles (image) | Push/pull en HTTPS vers Artifact Registry | Chiffrés par Google dans Artifact Registry |

L'en-tête `Strict-Transport-Security` (HSTS) impose HTTPS aux navigateurs pendant un an.

### Journal d'accès pseudonymisé

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

`POST /model` avec un corps JSON `{"text": "...", "model": "bert"}` (`model` optionnel, `bert` par défaut ; `text` limité à 1500 caractères) renvoie :
- `is_toxic` : `true` si le score de la classe `toxic` est > 70 ;
- `toxicity_score` : score de la classe `toxic` (0-100) ;
- `classes` : scores (0-100) de `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`.

Sorties : `artifacts/<modèle>/` (poids) et `results/<modèle>/metrics.json` (métriques).

`compare.py` affiche, pour chaque modèle, sur le jeu de test :
- **AUC moy** : ROC-AUC moyenne des 6 classes (critère du meilleur modèle) ;
- **précision / rappel / F1@50** : décision binaire « toxique si score ≥ 50 » ;
- **ms/comm.** : temps de traitement moyen d'un commentaire (inférence sur tout le test, tokenisation comprise) ;
- **train (s)** : durée d'entraînement ;
- puis la ROC-AUC par classe.

Les temps ne sont comparables qu'entre modèles évalués sur le même matériel.

## Déploiement (Docker / GKE)

L'image ne contient que l'API et les dépendances d'inférence (`dependencies` du `pyproject.toml`) :
PyTorch CPU, sans le groupe `train` (spaCy, polars, scikit-learn) ni `data/` (voir `.dockerignore`).
Les poids des modèles **ne sont pas dans l'image** : ils sont sur un **volume persistant** (bucket Cloud Storage `<projet>-toxic-models`, monté dans `/app/artifacts` via un `PersistentVolumeClaim`). L'image reste légère (build et push rapides), et mettre à jour un modèle ne demande pas de reconstruire l'image.

Le déploiement est décrit dans [k8s/toxic-api.yaml](k8s/toxic-api.yaml) :

| Objet | Rôle |
|---|---|
| `Deployment` | Pods de l'API : 2 CPU, 2 Gi, non-root, sondes sur `/health` ; modèles servis : `bert`, `lstm` (`ALLOWED_MODELS`) |
| `HorizontalPodAutoscaler` | 1 à 5 pods, cible 70 % de CPU |
| `Service` | Point d'accès interne aux pods, ciblé par l'Ingress |
| `Ingress` + `ManagedCertificate` | Load balancer HTTPS public (HTTP désactivé), certificat géré par Google |
| `ServiceAccount` + `SecretProviderClass` | Identité de l'API (Workload Identity) et montage des empreintes de clés depuis Secret Manager |
| `PersistentVolume` + `PersistentVolumeClaim` (`model-cache`) | Stockage persistant des modèles : bucket Cloud Storage monté en lecture seule par tous les pods (pilote GCS FUSE) |
| `BackendConfig` | Health check, timeout de 120 s, politique Cloud Armor |

```bash
# Test local (sur Mac Apple Silicon, --platform est nécessaire : les nœuds GKE sont en amd64)
docker build --platform linux/amd64 -t toxic-api .
docker run --rm -p 8080:8080 -e API_KEY_HASHES=<empreinte> toxic-api
curl localhost:8080/health

# Publication dans Artifact Registry (région Paris)
REGION=europe-west1; PROJECT=shopflow-cleo-maelle
gcloud artifacts repositories create toxic --repository-format=docker --location=$REGION
gcloud auth configure-docker $REGION-docker.pkg.dev
# Tag unique à chaque build (date-heure) : ne jamais repousser un tag existant, un nœud qui
# l'a déjà en cache garderait l'ancienne image.
TAG=$(date +%Y%m%d-%H%M); IMAGE=$REGION-docker.pkg.dev/$PROJECT/toxic/toxic-api:$TAG
docker build --platform linux/amd64 -t $IMAGE . && docker push $IMAGE

# Cluster GKE Autopilot (une seule fois)
gcloud container clusters create-auto toxic-cluster --region $REGION
gcloud container clusters get-credentials toxic-cluster --region $REGION

# IP publique statique (globale, exigée par l'Ingress) : faire pointer le DNS du domaine dessus (enregistrement A)
gcloud compute addresses create toxic-api-ip --global
IP=$(gcloud compute addresses describe toxic-api-ip --global --format 'value(address)')
# Sans nom de domaine : sslip.io résout <IP>.sslip.io vers <IP>, sans configuration DNS.
# Avec un domaine à soi : DOMAIN=api.mondomaine.fr et un enregistrement A vers $IP.
DOMAIN=$IP.sslip.io; echo $DOMAIN

# Cloud Armor (politique "toxic-api-policy", relançable) : 100 requêtes / minute / IP puis 429,
# IP bannie 10 min au-delà de 120 req/min, corps de plus de 4096 octets rejetés (403).
bash k8s/cloud-armor-setup.sh

# Clé d'API : la clé est donnée au client (la noter, elle n'est affichée qu'ici) ;
# seule son empreinte va dans Google Secret Manager (stocké en Europe).
gcloud services enable secretmanager.googleapis.com
uv run python make_api_key.py
printf '%s' "<empreinte>" | gcloud secrets create toxic-api-keys \
  --replication-policy=user-managed --locations=$REGION --data-file=-

# Clé de pseudonymisation des logs (aléatoire, personne n'a besoin de la connaître)
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create toxic-api-log-key \
  --replication-policy=user-managed --locations=$REGION --data-file=-

# Module Secret Manager de GKE : monte les secrets en fichiers dans le pod (une seule fois)
gcloud container clusters update toxic-cluster --region $REGION --enable-secret-manager

# IAM : seul le compte de service Kubernetes "toxic-api" (Workload Identity) peut lire ces secrets
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format 'value(projectNumber)')
for SECRET in toxic-api-keys toxic-api-log-key; do
  gcloud secrets add-iam-policy-binding $SECRET --role=roles/secretmanager.secretAccessor \
    --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT.svc.id.goog/subject/ns/default/sa/toxic-api"
done

# Stockage persistant des modèles : bucket en Europe, privé, lisible uniquement par l'API
gcloud storage buckets create gs://$PROJECT-toxic-models --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage cp -r artifacts/bert artifacts/lstm artifacts/laya gs://$PROJECT-toxic-models/
gcloud storage buckets add-iam-policy-binding gs://$PROJECT-toxic-models --role=roles/storage.objectViewer \
  --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT.svc.id.goog/subject/ns/default/sa/toxic-api"

# Déploiement : REGION, PROJECT_ID et DOMAIN du manifest remplacés
# (la première ligne s'arrête avec une erreur si l'une des variables est vide)
: "${REGION:?}" "${PROJECT:?}" "${DOMAIN:?}" "${TAG:?}"
sed -e "s/REGION/$REGION/" -e "s/PROJECT_ID/$PROJECT/" -e "s/DOMAIN/$DOMAIN/" -e "s/IMAGE_TAG/$TAG/" \
  k8s/toxic-api.yaml | kubectl apply -f -
kubectl rollout status deployment/toxic-api
kubectl describe managedcertificate toxic-api-cert   # attendre "Status: Active" (jusqu'à ~1 h)

# Appel
curl -X POST https://$DOMAIN/model -H "Content-Type: application/json" \
  -H "X-API-Key: <clé>" -d '{"text": "You are an idiot"}'
```

- **Nouvelle version** : nouveau `TAG`, build et push, puis la commande `sed … | kubectl apply` ci-dessus. Le changement de tag suffit à déclencher le déploiement (pas besoin de `rollout restart`). Retour arrière : relancer l'`apply` avec l'ancien `TAG`.
- **Sans domaine** (test) : `kubectl port-forward svc/toxic-api 8080:80` puis `curl localhost:8080/health`. Le certificat géré ne s'active pas sans DNS valide.
- **Autoscaling** : `kubectl get hpa toxic-api` montre la charge et le nombre de pods. Chaque pod charge ses propres modèles : un pod de plus = 2 CPU et 2 Gi facturés en plus.
- **Suspendre / reprendre** (économiser : en Autopilot, 0 pod = presque plus de calcul facturé ; le load balancer et l'IP réservée restent facturés). Le HPA remonte l'API à `minReplicas` : il faut le supprimer avant de passer à 0, et le réappliquer à la reprise (`TAG` = tag actuellement déployé, `kubectl get deploy toxic-api -o jsonpath='{.spec.template.spec.containers[0].image}'`) :
  ```bash
  # Suspendre
  kubectl delete hpa toxic-api
  kubectl scale deployment/toxic-api --replicas=0
  kubectl -n monitoring scale deployment/grafana deployment/prometheus --replicas=0

  # Reprendre (le apply recrée le HPA ; attendre ~1-2 min que /health réponde)
  sed -e "s/REGION/$REGION/" -e "s/PROJECT_ID/$PROJECT/" -e "s/DOMAIN/$DOMAIN/" -e "s/IMAGE_TAG/$TAG/" \
    k8s/toxic-api.yaml | kubectl apply -f -
  kubectl scale deployment/toxic-api --replicas=3
  kubectl -n monitoring scale deployment/grafana deployment/prometheus --replicas=1
  ```
- **Mettre à jour un modèle** (sans reconstruire l'image) : `gcloud storage cp -r artifacts/bert gs://$PROJECT-toxic-models/` puis `kubectl rollout restart deployment/toxic-api` (les modèles sont chargés au démarrage des pods).
- **Sécurité** : HTTPS uniquement (certificat géré, port 80 fermé, HSTS), clé d'API (`X-API-Key`) dont seule l'empreinte est stockée dans Secret Manager (lecture réservée au compte de service de l'API, accès tracés dans Cloud Audit Logs) et limitation de débit par IP (Cloud Armor).
- **Ajouter un client ou changer de clé sans coupure** : ajouter une version du secret contenant toutes les empreintes valides (une par ligne), puis redémarrer (le secret est lu au démarrage du pod) :
  `printf '%s\n%s' "$EMPREINTE1" "$EMPREINTE2" | gcloud secrets versions add toxic-api-keys --data-file=-` puis `kubectl rollout restart deployment/toxic-api`.
  Les anciennes versions restent consultables et réactivables (`gcloud secrets versions list toxic-api-keys`).
- `TORCH_NUM_THREADS` doit suivre `resources.requests.cpu` ; `PRELOAD_MODELS` (défaut `bert`) liste les modèles chargés **et préchauffés** au démarrage (une inférence factice avant que `/health` réponde : un nouveau pod ne reçoit du trafic qu'une fois rapide). Précharger `laya` rallonge le démarrage : augmenter alors `failureThreshold` de la `startupProbe`.
- **File d'attente des inférences** : une seule inférence à la fois par pod (`MAX_CONCURRENT_INFERENCES=1`), au plus `MAX_QUEUE_LENGTH` (8) requêtes en attente ; file pleine ou attente de plus de `INFERENCE_QUEUE_TIMEOUT` (2 s) : réponse `503` + `Retry-After` immédiate (délestage rapide plutôt qu'une latence qui explose). La file est gérée en asynchrone : une requête en attente n'occupe pas de thread, et `/health` répond même sous flood.
- **Taille des requêtes** : corps limité à 4096 octets (`413`), vérifié avant lecture, y compris sans `Content-Length` ; `text` limité à 1500 caractères (`422`).
- **En-têtes de sécurité** sur toutes les réponses de l'API : HSTS, `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`. `/docs` et `/openapi.json` fermés sauf `ENABLE_DOCS=1`.
- Logs et état : `kubectl logs deploy/toxic-api` (journal JSON pseudonymisé), `kubectl get pods`.
- Sans Docker en local : `gcloud builds submit --tag $IMAGE`.

## Supervision (Prometheus / Grafana)

Fichiers dans [k8s/monitoring/](k8s/monitoring/), namespace `monitoring`, **non exposé sur Internet** (accès par `kubectl port-forward`).

| Composant | Rôle |
|---|---|
| Métriques de l'API ([toxicity/metrics_prom.py](toxicity/metrics_prom.py)) | Port 8081, interne au cluster (absent du Service et de l'Ingress). Requêtes par statut, latence, temps d'inférence, attente en file, scores, longueur des textes, CPU et mémoire du processus. Aucune donnée personnelle, labels bornés (un scan d'URL ne crée pas de séries) |
| Prometheus | Découvre les pods de l'API (même ceux ajoutés par l'autoscaling), collecte toutes les 10 s, évalue les alertes. Historique 15 jours sur disque persistant (10 Gi). Droit minimal : lister les pods du namespace `default` |
| Grafana | Tableau de bord « Toxic API - supervision » (page d'accueil) + logs pseudonymisés de Cloud Logging dans **Explore** |

**Alertes** ([alert-rules.yml](k8s/monitoring/alert-rules.yml)) :

| Alerte | Déclencheur | Ce qu'elle révèle |
|---|---|---|
| `LatencySLOViolated` | < 95 % des requêtes en moins d'1 s, 5 min | SLO de latence non tenu |
| `HighErrorRate` | > 1 % de 5xx, 5 min | Erreurs serveur |
| `OverloadShedding` | Des 503 (file d'attente pleine), 2 min | Capacité insuffisante |
| `HighCPU` / `HighMemory` | > 90 % des 2 CPU / > 1,7 Go (limite 2 Gi), 5 min | Saturation, fuite mémoire, risque d'arrêt OOM |
| `PodDown` / `BelowMinReplicas` / `ApiMetricsAbsent` | Pod muet / < 3 pods / plus aucun pod | Panne, failover |
| `AuthFailureSpike` | > 30 clés refusées/min, 2 min | Force brute sur les clés |
| `ScanDetected` | > 20 req/min vers des URL inconnues | Scan de vulnérabilités |
| `ToxicityRatioAnomaly` | > 80 % de textes toxiques, 15 min | Dérive des données, abus, modèle défaillant |

**Installation** (après le déploiement de l'API, qui expose le port de métriques) :

```bash
# Mot de passe admin de Grafana, dans un Secret (jamais dans git)
kubectl create namespace monitoring
kubectl -n monitoring create secret generic grafana-admin --from-literal=password="$(openssl rand -base64 18)"

# Lecture des logs de l'API par Grafana (Workload Identity, lecture seule)
gcloud projects add-iam-policy-binding $PROJECT --role=roles/logging.viewer --condition=None \
  --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT.svc.id.goog/subject/ns/monitoring/sa/grafana"

kubectl kustomize k8s/monitoring | sed "s/PROJECT_ID/$PROJECT/" | kubectl apply -f -
kubectl -n monitoring rollout status deployment/prometheus deployment/grafana
```

**Accès** (un terminal par outil, laissé ouvert) :

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000        # http://localhost:3000 (admin)
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.password}' | base64 -d   # mot de passe
kubectl -n monitoring port-forward svc/prometheus 9090:9090     # http://localhost:9090/alerts
```

**Analyser une anomalie** : repérer l'heure sur le tableau de bord, puis dans Grafana **Explore → Cloud Logging**, filtrer les logs de l'API sur cette période :

```
resource.type="k8s_container" AND resource.labels.container_name="toxic-api"
jsonPayload.event="auth_failed"          # quelles IP (pseudonymisées) échouent, à quel rythme
jsonPayload.status>=500                  # erreurs serveur et délestage
jsonPayload.latency_ms>2000              # requêtes lentes : quel pod, quelle longueur de texte
```

Modifier une règle ou le tableau de bord : éditer le fichier puis relancer la commande `kubectl kustomize … | kubectl apply` (le pod concerné redémarre seul).

## Test de charge (Locust)

Tous les réglages sont dans [loadtest/config.toml](loadtest/config.toml) : cible (`host`, `model`), fichier de la clé, rythme des utilisateurs, scénarios (paliers modifiables) et instant du failover. Chaque utilisateur virtuel envoie ~1 requête/s à `POST /model` avec des textes de longueurs variées (aucune donnée personnelle) :

| Scénario | Profil | Durée | Objectif |
|---|---|---|---|
| `ramp` | 5 → 120 utilisateurs par paliers de 90 s | 12 min | Croissance progressive : suivre l'autoscaling, trouver le point de saturation |
| `smoke` | 2 utilisateurs | 30 s | Vérification rapide (clé, Cloud Armor, URL) avant un vrai test |
| `rapid` | 0 → 120 utilisateurs en 12 s, maintenus | 8 min | Montée rapide : délai de réaction de l'autoscaling, erreurs pendant le rattrapage |
| `soak` | 60 utilisateurs constants | 30 min | Charge soutenue : stabilité de la latence et de la mémoire dans la durée |
| `failover` | 40 utilisateurs, un pod supprimé à 5 min | 10 min | Reprise après la perte d'un pod |
| `stress` | 0 → 200 utilisateurs en 1 min | 5 min | Stress : comportement au-delà de la capacité, délai de l'autoscaling |
| `spike` | 10 → 150 → 10 utilisateurs | 8 min | Pic soudain puis retour au calme (montée et descente du nombre de pods) |

```bash
printf '%s' "dss_..." > loadtest/.api_key   # une clé valide (make_api_key.py) ; fichier exclu de git

# Cloud Armor limite à 100 requêtes/min/IP et bannit au-delà de 120 : exception temporaire pour
# la machine de test. Priorité 950 : après le filtre de taille (900), avant le ban (1000).
MY_IP=$(curl -s https://ifconfig.me)
gcloud compute security-policies rules create 950 --security-policy toxic-api-policy \
  --src-ip-ranges "$MY_IP/32" --action allow --description "test de charge (temporaire)"

uv run python loadtest/run.py --list    # scénarios disponibles
uv run python loadtest/run.py smoke     # vérification rapide
uv run python loadtest/run.py ramp      # puis rapid, soak, failover, stress, spike
uv run python loadtest/summarize.py     # tableau Markdown de tous les tests, pour le rapport

# À la fin : supprimer l'exception
gcloud compute security-policies rules delete 950 --security-policy toxic-api-policy --quiet
```

Résultats dans `loadtest/results/` : `<test>_stats.csv` (latences moyenne, P50/P95/P99, erreurs), `<test>_stats_history.csv` (évolution dans le temps), `<test>.html` (graphiques Locust, à joindre au rapport), `<test>_hpa.txt` (pods et CPU toutes les 15 s), `<test>_events.txt` (heure du failover).

- **Failover** : `failover` supprime automatiquement un pod à `failover_at` secondes (config). Comparer erreurs et latence autour de cet instant dans `<test>_stats_history.csv`. Le HPA garde au moins 3 pods : les 2 autres prennent le relais pendant le redémarrage.
- **Mémoire pendant `soak`** : `kubectl top pods -l app=toxic-api` en début et en fin de test (une hausse continue signalerait une fuite).
- **Mode interactif** : `uv run locust -f loadtest/locustfile.py` puis http://localhost:8089 (choix libre du nombre d'utilisateurs, graphiques en direct).
- **Attaque (étape 6)** : relancer `stress` **sans** l'exception Cloud Armor : les requêtes au-delà de 100/min reçoivent `429` et n'atteignent pas l'API.
- **Coût** : `stress` et `ramp` peuvent monter jusqu'à `maxReplicas` pods (2 CPU et 2 Gi chacun). Le quota de CPU de la région peut bloquer avant : les pods en trop restent alors en `Pending`.

## Ajouter un modèle

1. Créer `toxicity/models/<nom>.py` avec une classe héritant de `ToxicityModel` (`fit`, `predict_proba`, `save`, `load`).
   Un modèle PyTorch peut réutiliser `toxicity/models/torch_trainer.py` ; un modèle scikit-learn implémente simplement `fit` / `predict_proba`.
2. L'enregistrer dans `toxicity/models/registry.py`.
3. `train.py --model <nom>` puis `compare.py`.
