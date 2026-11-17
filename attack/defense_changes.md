# Étape 3 — Défenses mises en place (changements documentés)

**Projet :** Digital Social Score — API de détection de toxicité.
**Objet :** contre-mesures déployées après les attaques subies, et comment les valider.
**Statut :** défenses implémentées et validées en local (§8d) ; mesure en production
**à compléter** après redéploiement et nouvelle attaque (§7).

### Historique : attaque → constat → correction

| Attaque | Constat principal | Correction | Section |
|---|---|---|---|
| Groupe « Report Jungle » (étape 2, 13/11) | 0 erreur côté serveur, mais un simple throttle laisse l'attaquant revenir dès qu'il ralentit | Ban par IP de 10 min (Cloud Armor) | §2 |
| Groupe « Report Jungle » (volet taille) | Textes jusqu'à 5000 car. acceptés, alors que le modèle n'en lit que ~1500 | `MAX_TEXT_LENGTH` 1500 + filtre de taille au load balancer | §3 |
| Professeur, vague 5 | Service instable (« flapping ») | `/health` et la file d'attente ne bloquent plus de thread | §8 |
| Professeur, vague 6 (17/11) | DoS : 7712 requêtes acceptées en 92 s, latence ×11 ; extraction à 489/min ; en-têtes manquants ; API découverte via `/openapi.json` | Ban réellement déployé, délestage rapide (503), limite de corps (413), en-têtes, docs fermées | §8 |
| Durcissement complémentaire (17/11) | Les scores renvoyés facilitent la copie du modèle ; une IP qui spamme remplit la file et fait refuser les autres clients | Réponse réduite au booléen, ban à 200 req/min, plafond par IP en middleware (429), file équitable qui éjecte le spammeur | §10 |

Ce document fait suite à :
- `attack/defense_analysis.md` — l'attaque du groupe « Report Jungle » contre nous (côté serveur :
  0 erreur, latence < 1 s ; les 429 venaient de Cloud Armor, en amont des pods).
- `attack/analysis_vs_report_jungle.md` — notre attaque contre eux (leur infra n'a aucun rejet,
  latence jusqu'à 56 s : l'absence de ban/rate-limit est une vraie faiblesse).

Conclusion de ces deux analyses : notre point fort est le **filtrage en amont (Cloud Armor)** ;
nos axes d'amélioration sont (a) **bannir** l'attaquant plutôt que seulement le throttler, et
(b) **réduire la surface d'épuisement par la taille d'entrée**. C'est ce qui est traité ici.

---

## 1. Contrainte de départ : une seule clé d'API partagée

Toute l'équipe partage **une seule clé** `X-API-Key`. Conséquence directe sur le choix de défense :
limiter ou bannir **par clé** bloquerait tout le monde en même temps que l'attaquant. Le seul
discriminant exploitable est donc l'**adresse IP source**. Toutes les règles de limitation
ci-dessous portent sur l'IP (`enforceOnKey: IP`), pas sur la clé : une IP abusive est isolée,
les autres clients (même clé, IP différente) continuent de passer.

> Amélioration future : passer à **une clé par client** permettrait un quota par clé en plus du
> ban par IP, et de révoquer un client compromis sans couper les autres.

---

## 2. Changement 1 — Ban temporaire par IP (Cloud Armor)

**Fichier :** `k8s/cloud-armor-setup.sh` (appliqué sur la policy `toxic-api-policy`).

**Avant :** règle `throttle`, 100 req/60 s par IP, `deny(429)` au-delà. Problème : un simple
throttle ne bloque que les requêtes **excédentaires de la fenêtre courante**. Dès que l'attaquant
repasse sous 100/min (ou « pulse »), il est de nouveau servi.

**Après :** règle `rate_based_ban` :

| Paramètre | Valeur | Effet |
|---|---|---|
| `enforceOnKey` | `IP` | isole la source, pas la clé partagée |
| rate-limit | 100 req / 60 s | conforme en dessous (`allow`), `deny(429)` au-dessus |
| ban-threshold | ~~120~~ **200** req / 60 s (relevé en §10) | seuil de bascule en ban |
| **ban-duration** | **600 s** | l'IP est **bloquée 10 min**, même si elle ralentit ensuite |

L'attaquant ne peut donc plus contourner la limite en pulsant juste sous le seuil : au-delà de
200 req/min, il est banni 10 minutes d'un bloc. Le trafic légitime venant d'autres IP n'est pas
affecté.

> Note : Cloud Armor applique une durée de ban **fixe** par règle. Un ban « progressif »
> (1 min → 10 min selon la récidive) demanderait plusieurs règles ou une automatisation externe ;
> non retenu ici pour garder la configuration simple et lisible.

---

## 3. Changement 2 — Réduire la surface « sponge input »

Deux niveaux, défense en profondeur.

### 3a. Limite applicative : `MAX_TEXT_LENGTH` 5000 → 1500 caractères
**Fichier :** `api.py`.

Justification mesurée :
- Les modèles **tronquent à `max_length` = 256 tokens** (`toxicity/models/*.py`), soit
  ~1200–1500 caractères. **Au-delà, le texte est jeté par le tokenizer** et n'influence pas le
  score : accepter 5000 caractères n'apportait rien au modèle.
- Distribution réelle du jeu de données : p95 = **1364** car. sur `train.csv` (159 571
  commentaires) et **1274** sur `test.csv` (153 164) ; p99 ≈ 3500. **1500 caractères couvrent
  95,8 % (train) et 96,2 % (test) des commentaires réels.**
- Gain : la taille maximale d'entrée passe de 5000 à 1500 (**−70 %**), réduisant d'autant le coût
  réseau + parsing + tokenisation d'une requête surdimensionnée. Réponse `422` au-dessus.

> Honnêteté sur l'impact : comme le modèle tronquait **déjà** à 256 tokens, le coût d'inférence
> d'un texte long n'était pas si élevé (la mesure de l'attaquant plafonne ~300 ms de 500 à
> 5000 car.). Ce changement est donc surtout une **réduction de surface d'attaque** et une
> défense en profondeur, pas le levier principal. Le levier principal reste le ban par IP (§2).

### 3b. Rejet au load balancer (avant le pod)
**Fichier :** `k8s/cloud-armor-setup.sh`, règle Cloud Armor priorité 900.

`deny(403)` si `content-length > 4096` octets : un corps grossièrement surdimensionné est rejeté
**avant** d'atteindre le pod (pas de CPU/mémoire consommés à le recevoir/parser).

> Limite de cette règle : contournable en `Transfer-Encoding: chunked` (pas de `Content-Length`).
> La validation applicative (§3a, `422`) ne compense **pas** ce cas : FastAPI lit et parse le
> corps entier **avant** de le valider (et avant même le contrôle de clé, `401`), et uvicorn
> n'impose aucune taille maximale. **Corrigé** par une limite de taille dans l'application, qui
> compte les octets reçus et répond `413` dès le dépassement, avec ou sans `Content-Length`
> (§8c, correction 4). Cloud Armor n'autorise pas `deny(413)`, d'où le `403` au load balancer.

### 3c. Limite applicative du corps HTTP : 4096 octets (`413`)
**Fichier :** `toxicity/body_limit.py` (ajoutée après la vague 6, détail en §8c). Même seuil que la
règle Cloud Armor 900 : le load balancer coupe tôt les corps qui annoncent leur taille, l'API
couvre ceux qui ne l'annoncent pas.

---

## 4. Changement 3 — Scaling plus résilient

**Fichier :** `k8s/toxic-api.yaml` (HorizontalPodAutoscaler).

- **`maxReplicas` 10 → 15** : plus de marge pour absorber une charge soutenue (le commentaire du
  manifeste annonçait déjà « 3 à 15 pods »).
- **Métrique idéale documentée (non activée)** : scaler sur `toxic_api_queue_waiting` (nombre de
  requêtes en attente d'un créneau d'inférence) réagit **avant** la saturation, là où le CPU ne
  monte qu'une fois les pods déjà saturés. Laissée en commentaire dans le manifeste car elle
  nécessite l'adaptateur de métriques custom de Google Managed Prometheus (non déployé).

> Honnêteté sur l'impact : l'autoscaling est une défense **secondaire** contre un flood **court**
> (un nouveau pod met 1–2 min à être prêt, il arrive après le pic). Il protège surtout contre une
> charge **qui dure**. Les défenses primaires contre le DoS restent le ban d'IP (§2) et le
> délestage `503` (file bornée à 8 requêtes par pod, attente max 2 s depuis la vague 6, §8c).
> La vague 6 l'a confirmé : attaque de 92 s, pods supplémentaires prêts en 1 à 2 min.

---

## 5. Défenses déjà présentes (à valoriser dans le rapport)

Ces mécanismes existaient déjà et nous ont protégés pendant l'attaque de l'étape 2 — l'analyse
`defense_analysis.md` montre que le backend n'a eu **aucune erreur**. C'est précisément ce qui
**manque** au groupe « Report Jungle » (cf. `analysis_vs_report_jungle.md`) :

| Mécanisme | Fichier | Rôle |
|---|---|---|
| Délestage `503` (file + timeout) | `api.py` (`INFERENCE_QUEUE_TIMEOUT`) | un pod saturé refuse au lieu de s'effondrer. **Insuffisant tel quel** : avec 20 s d'attente tolérée, le surplus était « accepté mais lent » (vague 6). Durci en §8c |
| 1 inférence à la fois par pod | `api.py` (`MAX_CONCURRENT_INFERENCES`) | évite que N requêtes se disputent le CPU |
| Authentification obligatoire sur `/model` | `api.py` (`require_api_key`) | pas d'inférence sans clé (401) |
| Rate limit par IP (Cloud Armor) | policy `toxic-api-policy` | filtrage en amont, renforcé ici en ban (§2) |
| En-têtes HSTS et `X-Content-Type-Options` | `api.py` (middleware) | déjà présents avant la vague 6 ; complétés par CSP, `X-Frame-Options`, `Referrer-Policy` (§8c) |

---

## 6. Écarté volontairement

- **Cache des scores** (dédup des requêtes identiques) : écarté. Gain réel sur un flood répétitif,
  mais complexité et risque de servir un score périmé ; non prioritaire.
- **Ban progressif** (durée croissante) : non retenu (durée de ban fixe, cf. §2).
- **Quota par clé** : impossible tant que la clé est partagée (cf. §1).

---

## 7. Déploiement et validation (À COMPLÉTER avec les prochaines attaques)

### Déployer
```bash
git pull
REGION=europe-west1; PROJECT=<projet-gcp>; DOMAIN=<domaine>   # valeurs du déploiement en place
TAG=$(date +%Y%m%d-%H%M); IMAGE=$REGION-docker.pkg.dev/$PROJECT/toxic/toxic-api:$TAG
gcloud config set project $PROJECT

# 1. Cloud Armor : ban IP + filtre de taille (crée ou met à jour, relançable)
bash k8s/cloud-armor-setup.sh      # attendu : 900 deny(403), 1000 rate_based_ban 600

# 2. Image puis déploiement
gcloud auth configure-docker $REGION-docker.pkg.dev
docker build --platform linux/amd64 -t $IMAGE . && docker push $IMAGE
sed -e "s/REGION/$REGION/" -e "s/PROJECT_ID/$PROJECT/" -e "s/DOMAIN/$DOMAIN/" -e "s/IMAGE_TAG/$TAG/" \
  k8s/toxic-api.yaml | kubectl apply -f -
kubectl rollout status deployment/toxic-api

# 3. Vérifications
API=http://<ip-ou-domaine>
curl -s -i $API/health | grep -iE "HTTP/|content-security|x-frame|strict-transport"   # 200 + en-têtes
curl -s -o /dev/null -w "docs: %{http_code}\n" $API/docs                                  # 404
```

### Préparer la mesure
Pour **voir** les requêtes bloquées par Cloud Armor (elles n'atteignent pas les logs de l'API),
réactiver temporairement les logs du load balancer **juste avant** l'attaque, puis les
redésactiver après (ils contiennent l'IP client en clair — RGPD) :
```yaml
# k8s/toxic-api.yaml, BackendConfig toxic-api-backend :
  logging:
    enable: true   # temporaire, le temps de l'attaque
```

### Tableau à remplir (avant / après défense)

| Métrique | Étape 2 — Report Jungle (13/11) | Vague 6 — prof, avant corrections (17/11) | Après corrections (à mesurer) |
|---|---|---|---|
| Requêtes atteignant le modèle | 1085 en ~40 min | **7712 en 91,7 s** | _à mesurer_ |
| Taux d'erreur vu par l'attaquant | 79–100 % (429) | faible (requêtes acceptées) | _à mesurer_ |
| Latence p50 vue par l'attaquant | — | **1397 ms** (126 ms au repos) | _à mesurer_ |
| Erreurs côté backend (logs API) | 0 % | _non relevé_ | _à mesurer_ |
| Latence p95 backend | 335 ms | _non relevé_ | _à mesurer_ |
| Extraction du modèle (requêtes/min) | — | 489/min | _à mesurer_ |
| IP attaquante bannie ? | non (throttle) | non (throttle, ban non déployé) | _à vérifier dans les logs LB_ |
| Refus rapides `503` (délestage) | 0 | 0 visible (attente jusqu'à 20 s) | _à mesurer_ |
| Corps > 4096 o rejetés (403 LB / 413 API) ? | non | non | _à vérifier_ |
| `/docs`, `/openapi.json` | exposés | exposés (point d'entrée de l'attaque) | `404` (vérifié en local) |

### Protocole suggéré
1. Rejouer la **même** attaque (`attack/dos.py`, leur `attack.py`, ou nouvelle vague du prof),
   depuis une machine distincte du serveur (voir la limite de mesure en §8d).
2. Relever côté attaquant (codes HTTP, latence) **et** côté serveur : logs Cloud Logging de l'API
   (`jsonPayload.status`, `jsonPayload.latency_ms`) et logs du load balancer (requêtes bloquées
   par Cloud Armor).
3. Compter les IP distinctes de l'attaque (champ `jsonPayload.ip`, pseudonymisé mais comparable)
   pour savoir si l'attaque était distribuée :
   ```bash
   gcloud logging read 'resource.labels.container_name="toxic-api" AND jsonPayload.path="/model"
     AND timestamp>="DEBUT" AND timestamp<="FIN"' --format='value(jsonPayload.ip)' --limit 20000 \
     | sort | uniq -c | sort -rn | head -20
   ```
4. Vérifier qu'une IP qui dépasse 200 req/min disparaît des logs de l'API pendant ~10 min (ban).
5. Remplir le tableau ci-dessus et conclure sur l'efficacité.

---

## 8. Retour d'attaque — audit du professeur (vague 6) et corrections

### 8a. Ce que l'audit a trouvé

Cible : `http://136.82.246.21` (`POST /model`), 4 constats :

| Gravité | Constat |
|---|---|
| HIGH | DoS sur `/model` (découvert via `/openapi.json`) : **7712 requêtes acceptées en 91,7 s** (~84 req/s), latence p50 **1397 ms** contre 126 ms au repos (×11) |
| MEDIUM | Modèle extractible mais freiné : 30 échantillons en 4 s puis 429 en masse, extraction réduite à 489/min |
| MEDIUM | En-têtes manquants (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) |
| — | Service instable (« flapping ») pendant la vague 5 |

### 8b. Analyse des causes

1. **Le ban par IP n'était pas déployé.** Sur le projet GCP qui héberge l'API, la policy était
   restée à l'ancienne règle `throttle` (100 req/min/IP, sans ban ni filtre de taille) :
   `k8s/cloud-armor-setup.sh` n'y avait jamais été exécuté, et il échouait sur un projet neuf
   (il ne faisait qu'un `rules update`). Avec un simple throttle, chaque IP garde 100 req/min
   indéfiniment : 489/min d'extraction ≈ 5 IP × 100/min.
2. **La latence ×11 est de l'attente en file, pas une inférence plus lente.** Une inférence à
   la fois par pod ; au-delà de la capacité des 3 pods, les requêtes attendaient leur tour
   jusqu'à **20 s** (`INFERENCE_QUEUE_TIMEOUT`). Elles étaient donc « acceptées mais lentes » :
   1397 ms ≈ une dizaine de requêtes devant soi.
3. **L'autoscaling arrive trop tard pour un pic court.** Un nouveau pod met 1 à 2 min (nœud
   Autopilot, chargement de BERT, préchauffage), l'attaque durait 92 s, et l'HPA réagit au CPU,
   qui ne monte qu'une fois les pods saturés.
4. **Cause du flapping (vague 5) : threads épuisés.** `/model` et `/health` étaient des fonctions
   synchrones, exécutées dans le pool de 40 threads de FastAPI. Sous flood, les 40 threads étaient
   bloqués jusqu'à 20 s dans la file d'inférence ; `/health` attendait un thread libre, les sondes
   Kubernetes et le health check du load balancer échouaient, et le pod était retiré du trafic.
5. **En-têtes : constat en partie inexact.** HSTS et `X-Content-Type-Options` étaient bien
   renvoyés par l'API (vérifié sur `/health`). L'audit a vraisemblablement lu les réponses `429`,
   générées par Cloud Armor au load balancer, qui ne passent pas par l'API. CSP et
   `X-Frame-Options` manquaient réellement. HSTS est de toute façon sans effet en HTTP simple.

### 8c. Corrections

| # | Correction | Fichier |
|---|---|---|
| 1 | Script Cloud Armor utilisable sur un projet neuf (crée la policy et les règles, ou les met à jour) ; README aligné (plus d'ancien `throttle`) | `k8s/cloud-armor-setup.sh`, `README.md` |
| 2 | **Délestage rapide** : au plus 8 requêtes en attente par pod (`MAX_QUEUE_LENGTH`), puis `503` immédiat ; attente max 20 s → **2 s** | `api.py`, `k8s/toxic-api.yaml` |
| 3 | **File d'attente asynchrone** : `/model`, `/health` et le contrôle de clé ne bloquent plus de thread ; seule l'inférence tourne dans un thread. `/health` répond même sous flood | `api.py` |
| 4 | **Limite de taille du corps** (413 au-delà de 4096 octets) vérifiée avant lecture, y compris sans `Content-Length` (chunked), avant parsing JSON et contrôle de clé | `toxicity/body_limit.py`, `api.py` |
| 5 | En-têtes `Content-Security-Policy`, `X-Frame-Options`, `Referrer-Policy` ajoutés | `api.py` |
| 6 | `/docs`, `/redoc`, `/openapi.json` **fermés en production** (`ENABLE_DOCS=1` pour les ouvrir en local) | `api.py`, `k8s/toxic-api.yaml` |

> Compromis assumé (correction 2) : sous une charge supérieure à la capacité, l'attaquant voit
> davantage de `503` au lieu d'une latence élevée. C'est voulu : les requêtes acceptées restent
> rapides, le pod reste sain, et le surplus est refusé en quelques millisecondes au lieu
> d'occuper le serveur. La disponibilité face à une attaque **distribuée** (beaucoup d'IP, chacune
> sous le seuil de ban) reste bornée par la capacité des pods : c'est la limite du ban par IP.

### 8d. Validation locale (avant / après, même charge)

**Protocole.** Même machine, même modèle (BERT, CPU, `TORCH_NUM_THREADS=1` pour rapprocher le
temps d'inférence de la production), même tir : `attack/dos.py`, 100 clients simultanés pendant
30 s, texte de 1400 caractères. « Avant » = `api.py` avant ces corrections ; « après » = version
actuelle. Une sonde `curl` (processus séparé) interroge `/health` chaque seconde pendant la
charge. Latences **côté serveur** lues dans le journal JSON de l'API (`latency_ms`).
Fichiers : `attack/results/local-avant*`, `attack/results/local-apres*`.

| Mesure | Avant | Après |
|---|---|---|
| Requêtes traitées (200) | 1729 (100 %) | 1720 (98,5 %) |
| Refus rapides (503) | 0 | 26 (p50 **10 ms**) |
| Latence serveur des 200 — p50 | **1741 ms** | **74 ms** |
| Latence serveur des 200 — p95 | 1927 ms | 124 ms |
| Latence serveur des 200 — max | 2254 ms | 207 ms |
| `/health` pendant la charge | **620 – 830 ms** | **2 – 12 ms** |
| Requête `/model` d'un client tiers pendant la charge | — | 17 – 119 ms (`local-apres_sonde-model.txt`) |
| Corps de 6000 octets (avec / sans Content-Length) | lu et parsé en entier | `413` avant lecture |
| `/docs`, `/openapi.json` | exposés | `404` |

**Lecture.**
- Le débit utile est **identique** (même capacité CPU) : la correction ne fabrique pas de
  capacité, elle change **qui attend**. Avant, toutes les requêtes faisaient la queue (×24 en
  latence) ; après, les requêtes acceptées restent proches du temps d'inférence et le surplus est
  refusé en ~10 ms.
- `/health` : 0,6–0,8 s avant, alors que l'inférence locale ne dure que ~20 ms. En production
  (inférence ~6× plus longue, attente tolérée 20 s), cette attente dépassait le délai de la sonde
  de readiness (1 s par défaut) : pod déclaré « non prêt », retiré du load balancer, puis remis —
  c'est le mécanisme du flapping de la vague 5. Après : quelques millisecondes, sous charge.

> **Limite de la mesure.** La latence vue par `dos.py` lui-même reste élevée dans la version
> « après » (p50 ~1,3 s), alors qu'un client tiers est servi en < 120 ms au même moment : c'est
> le **client** qui sature. Un seul processus Python qui maintient 100 requêtes simultanées, sur
> la même machine que le serveur, plafonne vers ~55 req/s ; ses requêtes attendent dans le client
> avant même d'être envoyées. Pour une mesure côté attaquant fiable, lancer le tir depuis une
> autre machine (ou plusieurs processus). Ce biais existe aussi dans nos tirs à 1000 et 2000
> clients contre l'autre groupe (`dos-palier-*`) : une partie de la latence mesurée y est
> vraisemblablement due à notre propre client.

**Reste à faire** : redéployer (image + `k8s/cloud-armor-setup.sh`, commandes en §7), activer
les logs du load balancer le temps de l'attaque, faire rejouer l'attaque (prof ou autre groupe)
et remplir la dernière colonne du tableau du §7 avec les mesures en production.

### 8e. Pistes non traitées (si l'attaque est distribuée)

Le ban par IP n'arrête pas une attaque répartie sur de nombreuses IP restant chacune sous
200 req/min. Le délestage garde alors le service sain (requêtes acceptées rapides, surplus en
`503`), mais la capacité reste celle des pods en place. Options, par coût croissant :
- **plafond global Cloud Armor** (`--enforce-on-key ALL`, calé sur la capacité réelle) : protège
  le modèle, mais bloque tout le monde une fois atteint ;
- **capacité prête avant le pic** : `minReplicas` plus élevé pendant les périodes d'attaque, modèle
  intégré à l'image (plutôt que lu depuis le bucket) pour démarrer plus vite, pods « ballon » de
  basse priorité pour garder un nœud disponible ;
- **scaling sur la file d'attente** (`toxic_api_queue_waiting`) plutôt que sur le CPU, qui
  nécessite l'adaptateur de métriques custom de Google Managed Prometheus (§4) ;
- **une clé d'API par client** (§1), pour limiter et révoquer par client.

---

## 9. Récapitulatif des fichiers modifiés

| Fichier | Changement |
|---|---|
| `api.py` | `MAX_TEXT_LENGTH` 5000 → 1500 (+ justification) ; délestage rapide (file bornée, 2 s) ; file asynchrone ; limite de corps 413 ; en-têtes CSP/X-Frame-Options/Referrer-Policy ; docs fermées par défaut ; **§10** : réponse réduite à `is_toxic`, file équitable, plafond par IP |
| `toxicity/body_limit.py` | **nouveau** : limite de taille du corps HTTP (413), avec ou sans `Content-Length` |
| `toxicity/fair_queue.py` | **nouveau (§10)** : file d'inférence bornée et équitable entre IP (éjection du spammeur) |
| `toxicity/ip_limit.py` | **nouveau (§10)** : middleware, au plus 4 requêtes `/model` en cours par IP et par pod (429) |
| `toxicity/metrics_prom.py` | **§10** : compteur `toxic_api_shed_total{reason}` des refus par délestage |
| `k8s/cloud-armor-setup.sh` | **nouveau** : ban IP 600 s + rejet corps > 4096 o ; crée la policy si absente, crée ou met à jour chaque règle ; **§10** : seuil de ban 120 → 200 req/min |
| `README.md` | création de la policy via le script (au lieu de l'ancien `throttle`) ; exception de test de charge déplacée en priorité 950 (900 = filtre de taille) ; **§10** : réponse booléenne, seuil 200, plafond par IP à lever pendant les tests de charge |
| `k8s/toxic-api.yaml` | HPA `maxReplicas` 10 → 15 + métrique file d'attente (commentée) ; variables `MAX_QUEUE_LENGTH`, `INFERENCE_QUEUE_TIMEOUT`, `ENABLE_DOCS` ; **§10** : `MAX_IN_FLIGHT_PER_IP` |
| `API.md`, `README.md` | doc alignée sur 1500 caractères ; codes `413` et `503` (surcharge) ; docs fermées par défaut ; **§10** : réponse `{"is_toxic": bool}`, codes `429` |
| `loadtest/locustfile.py` | **§10** : vérifie `is_toxic` (booléen) au lieu de `toxicity_score` |
| `attack/defense_changes.md` | **ce document** |

---

## 10. Durcissement complémentaire (17/11) : extraction du modèle et spam par IP

### 10a. `/model` ne renvoie plus que la décision

**Fichiers :** `api.py`, `API.md`, `README.md`, `loadtest/locustfile.py`.

**Avant :**
```json
{ "model": "bert", "text": "...", "is_toxic": true, "toxicity_score": 96.4,
  "classes": { "severe_toxic": 3.1, "obscene": 41.7, "threat": 0.4, "insult": 88.2, "identity_hate": 1.2 } }
```
**Après :**
```json
{ "is_toxic": true }
```

**Pourquoi.** L'audit de la vague 6 a montré que le modèle était extractible (489 requêtes/min).
Six probabilités par texte sont une étiquette très riche : un attaquant entraîne une copie
(« modèle substitut ») en imitant ces scores, avec bien moins de requêtes qu'avec une simple
étiquette. Ne renvoyer que `is_toxic`, basé sur la seule classe `toxic` (score > 70), retire
cette information : chaque requête n'apporte plus qu'**un bit**. Les champs `model` et `text`
sont aussi retirés : le client les connaît déjà, et renvoyer le texte n'apportait rien.

Le score reste calculé et conservé **côté serveur** : seuil de décision, journal d'accès
(`toxicity_score`, pseudonymisé, sans le texte) et histogramme Prometheus. La supervision et
l'analyse des attaques ne perdent rien.

> Honnêteté sur l'impact : l'extraction reste possible à partir des seules étiquettes
> (« label-only »), mais il faut beaucoup plus de requêtes pour la même fidélité, et les
> limites de débit (§2, §10b, §10c) plafonnent ce nombre. C'est un frein, pas une
> impossibilité. Changement **cassant** pour les clients qui lisaient `toxicity_score` ou `classes`.

### 10b. Cloud Armor : ban à 200 req/min au lieu de 120

**Fichiers :** `k8s/cloud-armor-setup.sh`, `README.md`.

| Paramètre | Avant | Après |
|---|---|---|
| rate-limit (429 sur l'excédent) | 100 req / 60 s | 100 req / 60 s (inchangé) |
| ban-threshold | 120 req / 60 s | **200 req / 60 s** |
| ban-duration | 600 s | 600 s (inchangé) |

**Pourquoi.** Avec 120, l'écart entre « limité » et « banni 10 min » n'était que de 20 requêtes :
un client légitime un peu rapide (rafale, réessais automatiques après un `503`) passait presque
directement au ban. Entre 100 et 200 req/min, une IP est désormais **limitée** (429 sur
l'excédent) sans être bannie. Au-delà de 200, comportement de spam avéré : ban de 10 min.

Ce relâchement est compensé dans l'API par le plafond par IP (§10c), qui coupe une rafale **dès
la première seconde**, sans attendre qu'un seuil par minute soit atteint.

### 10c. Plafond de requêtes simultanées par IP, en middleware devant le délestage

**Fichiers :** `toxicity/ip_limit.py` (nouveau), `api.py`, `k8s/toxic-api.yaml`.

Une IP ne peut pas avoir plus de **4 requêtes `/model` en cours** (en file ou en inférence) sur un
même pod (`MAX_IN_FLIGHT_PER_IP`, défaut 4). Au-delà : **`429` immédiat** avec `Retry-After: 1`.

Ordre de traitement d'une requête, après le changement :

| # | Étape | Refus |
|---|---|---|
| 1 | Cloud Armor (load balancer) : corps > 4096 o, rate limit / ban par IP | 403, 429 |
| 2 | Middleware journal + en-têtes de sécurité | — |
| 3 | **Plafond par IP (nouveau)** | **429** |
| 4 | Limite de taille du corps | 413 |
| 5 | Parsing JSON, validation, contrôle de clé | 422, 401 |
| 6 | File d'inférence équitable (§10d) | 503 |
| 7 | Inférence | — |

**Pourquoi devant.** Le middleware est placé **avant** la lecture du corps, le parsing JSON, le
contrôle de clé et la file d'inférence : une requête de spam est refusée sans coûter de CPU et
sans prendre de place dans la file. Il reste **à l'intérieur** du middleware de journalisation :
les `429` sont journalisés (avec l'IP pseudonymisée) et comptés dans les métriques.

**Pourquoi 4.** Un client légitime attend sa réponse avant d'envoyer la suivante : il a 1 requête
en cours. Avec 4, une IP occupe au pire 1 inférence + 3 places sur les 8 de la file, soit moins de
la moitié. Le plafond est **par pod** : derrière le load balancer, une IP est répartie entre les pods.

**Complémentarité avec Cloud Armor.** Cloud Armor compte des requêtes **par minute** au load
balancer ; le middleware compte des requêtes **en cours** sur chaque pod. Une rafale de 100
requêtes simultanées passe sous le seuil de 200/min mais est coupée ici dès la 5e.

> Limite : pendant un test de charge depuis une seule machine (Locust, 120 utilisateurs), ce
> plafond renverrait des `429`. Le lever le temps du test : `kubectl set env
> deployment/toxic-api MAX_IN_FLIGHT_PER_IP=0`, puis le remettre à 4 (procédure dans `README.md`).

### 10d. File d'attente bornée et équitable : on éjecte le spammeur, pas le nouvel arrivant

**Fichiers :** `toxicity/fair_queue.py` (nouveau), `api.py`, `toxicity/metrics_prom.py`.

**Avant (§8c, correction 2) :** file FIFO bornée à 8 requêtes par pod. File pleine : la
**nouvelle** requête reçoit `503`, quelle que soit son IP. Une IP qui spamme remplissait donc la
file et c'était les autres clients (souvent légitimes) qui étaient refusés.

**Après :** la file reste bornée (8 en attente, 2 s d'attente max), mais elle tient le compte des
requêtes en attente **par IP**. Quand elle est pleine :

- si l'IP la plus présente dans la file y a **au moins 2 requêtes de plus** que l'IP du nouvel
  arrivant, sa requête **la plus récente** est éjectée (`503`) et le nouvel arrivant prend la place ;
- sinon, le nouvel arrivant est refusé (`503`), comme avant.

Le seuil de 2 évite d'échanger sans fin deux IP à égalité. On éjecte la requête la plus récente
du spammeur, celle qui a le moins attendu, pour que ses requêtes les plus anciennes ne soient pas
perdues inutilement.

Une requête qui obtient un créneau puis voit son client se déconnecter rend le créneau ; une
requête qui expire ou est éjectée sort de la file. Aucune fuite de créneau.

**Nouvelle métrique :** `toxic_api_shed_total{reason}`, avec `reason` ∈ `ip_limit` (429, §10c),
`full` (503, file pleine), `evicted` (503, éjectée au profit d'une autre IP) et `timeout` (503).
Elle permet de voir, pendant une attaque, si les refus visent bien l'attaquant (`ip_limit`,
`evicted`) ou touchent tout le monde (`full`, `timeout`).

### 10e. Validation locale

Tests en local (BERT, CPU, `AUTH_DISABLED=1`, `TORCH_NUM_THREADS=1`) :

| Test | Résultat |
|---|---|
| `POST /model` « You are an idiot » / « Have a nice day » | `{"is_toxic": true}` / `{"is_toxic": false}`, aucun score dans la réponse |
| Rafale de 30 requêtes simultanées depuis la même IP | **4 × `200`, 26 × `429`** ; les 26 refus apparaissent dans le journal JSON |
| File (test unitaire) : une IP occupe le créneau + 8 places, puis 3 autres IP arrivent | les 3 nouvelles IP sont servies ; 3 requêtes du spammeur éjectées (`evicted`) |
| File : deux IP à égalité (4/4), une 5e requête de l'une d'elles | refusée (`full`), pas d'échange |
| File : expiration (`timeout`) et client déconnecté pendant l'attente | créneau rendu, file vide, compteurs par IP à zéro |

**Reste à faire :** redéployer (image + `bash k8s/cloud-armor-setup.sh`, commandes en §7),
puis mesurer en production pendant la prochaine attaque : part des `429` applicatifs, répartition
de `toxic_api_shed_total` par raison, et latence des clients légitimes pendant le spam.
