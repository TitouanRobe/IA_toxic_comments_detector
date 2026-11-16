# Étape 3 — Défenses mises en place (changements documentés)

**Projet :** Digital Social Score — API de détection de toxicité.
**Objet :** contre-mesures déployées après les attaques DoS de l'étape 2, et comment les valider.
**Statut :** défenses implémentées ; mesure d'efficacité **à compléter** avec les prochaines
attaques (section 7).

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
| ban-threshold | 120 req / 60 s | seuil de bascule en ban |
| **ban-duration** | **600 s** | l'IP est **bloquée 10 min**, même si elle ralentit ensuite |

L'attaquant ne peut donc plus contourner la limite en pulsant juste sous le seuil : au-delà de
120 req/min, il est banni 10 minutes d'un bloc. Le trafic légitime venant d'autres IP n'est pas
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
- Distribution réelle du jeu de données (312 735 commentaires) : médiane 194 car., **p95 = 1319**,
  p99 = 3508. **1500 caractères couvrent 96 % des commentaires réels.**
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

> Limite connue : contournable en `Transfer-Encoding: chunked` (pas de `Content-Length`). C'est
> pourquoi la vérification applicative (§3a, `422`) reste le garde-fou **fiable** ; la règle LB
> coupe juste le gros du trafic trivialement surdimensionné au plus tôt. Cloud Armor n'autorise
> pas `deny(413)`, d'où le `403`.

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
> délestage `503` déjà en place (`INFERENCE_QUEUE_TIMEOUT` dans `api.py`).

---

## 5. Défenses déjà présentes (à valoriser dans le rapport)

Ces mécanismes existaient déjà et nous ont protégés pendant l'attaque de l'étape 2 — l'analyse
`defense_analysis.md` montre que le backend n'a eu **aucune erreur**. C'est précisément ce qui
**manque** au groupe « Report Jungle » (cf. `analysis_vs_report_jungle.md`) :

| Mécanisme | Fichier | Rôle |
|---|---|---|
| Délestage `503` (file bornée + timeout) | `api.py` (`INFERENCE_QUEUE_TIMEOUT`) | un pod saturé refuse vite au lieu de s'effondrer |
| 1 inférence à la fois par pod | `api.py` (`MAX_CONCURRENT_INFERENCES`) | évite que N requêtes se disputent le CPU |
| Authentification obligatoire sur `/model` | `api.py` (`require_api_key`) | pas d'inférence sans clé (401) |
| Rate limit par IP (Cloud Armor) | policy `toxic-api-policy` | filtrage en amont, renforcé ici en ban |

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
# 1. Cloud Armor (ban IP + filtre taille) — déjà appliqué, réexécutable (idempotent)
bash k8s/cloud-armor-setup.sh

# 2. API (MAX_TEXT_LENGTH) + HPA : rebuild image puis
kubectl apply -f k8s/toxic-api.yaml
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

| Métrique | Avant (étape 2) | Après (à mesurer) |
|---|---|---|
| Taux d'erreur vu par l'attaquant | 79–100 % (429) | _à mesurer_ |
| Erreurs côté backend (logs API) | 0 % | _à mesurer_ |
| Latence p95 backend | 335 ms | _à mesurer_ |
| IP attaquante bannie ? | non (throttle only) | _à vérifier dans les logs LB_ |
| Corps > 4096 o rejetés au LB (403) ? | n/a | _à vérifier_ |
| Textes > 1500 car. rejetés (422) ? | n/a (limite 5000) | _à vérifier_ |

### Protocole suggéré
1. Rejouer la **même** attaque qu'à l'étape 2 (`attack/dos.py` et/ou leur `attack.py`).
2. Relever côté attaquant (codes HTTP, latence) **et** côté serveur (logs Cloud Logging + logs LB).
3. Vérifier qu'une IP qui dépasse 120 req/min disparaît des logs ~10 min (ban actif).
4. Remplir le tableau ci-dessus et conclure sur l'efficacité.

---

## 8. Récapitulatif des fichiers modifiés

| Fichier | Changement |
|---|---|
| `api.py` | `MAX_TEXT_LENGTH` 5000 → 1500 (+ justification) |
| `k8s/cloud-armor-setup.sh` | **nouveau** : ban IP 600 s + rejet corps > 4096 o |
| `k8s/toxic-api.yaml` | HPA `maxReplicas` 10 → 15 + métrique file d'attente (commentée) |
| `API.md`, `README.md` | doc alignée sur 1500 caractères |
| `attack/defense_changes.md` | **ce document** |
