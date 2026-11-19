# Analyse — Attaque du professeur contre notre API (vagues 5 et 6)

**Objet :** confronter l'audit du professeur (vue de l'attaquant) à ce que nous avons constaté côté
serveur, en déduire les causes et les améliorations apportées, et mesurer leur effet.

**Cible :** notre API en production, `http://136.82.246.21` (`POST /model`), le 17/11/2026.
Version déployée : commit `451d731` (texte limité à 1500 caractères, file d'attente de 20 s,
endpoints synchrones, scores renvoyés au client).

**Sources :**
- Côté attaquant : le rapport d'audit du professeur (vague 6, 4 constats), reproduit en §1.
- Côté défenseur : configuration Cloud Armor du projet de production, en-têtes renvoyés par
  l'API déployée (vérifiés le 17/11), et reproduction de l'attaque en local avec
  [bench_defense.py](bench_defense.py) sur la version attaquée.

Détail des corrections : [defense_changes.md §8 et §10](defense_changes.md).
Synthèse pour le rendu : [livrable_tp2/3_defense.md](../livrable_tp2/3_defense.md).

---

## 1. Ce que le professeur a mesuré (vue externe)

| Gravité | Constat de l'audit |
|---|---|
| **HIGH** | DoS sur le vrai endpoint d'inférence (`POST /model`, **découvert via l'OpenAPI**) : **7712 requêtes acceptées en 91,7 s**, latence p50 **1397 ms** contre 126 ms de référence (**×11**). « Première équipe dont la charge soutenue a atteint le modèle directement. » |
| MEDIUM | Modèle extractible mais freiné : 30 échantillons en 4 s puis `429` en masse ; extraction réduite à **489 requêtes/min** (« défense partielle : le substitut reste entraînable, juste plus lent ») |
| MEDIUM | En-têtes manquants : HSTS, CSP, X-Frame-Options, X-Content-Type-Options |
| — | Vague 5 : service instable (« flapping ») ; audit refait en vague 6 avec détection de stabilité |

Remédiation demandée : ajouter les en-têtes, étendre la limitation de débit (« il a tenu 30
requêtes puis a cédé sur la latence pendant le DoS »).

## 2. Ce que nous avons constaté côté serveur

| Constat de l'audit | Vérification côté serveur | Verdict |
|---|---|---|
| Charge acceptée, latence ×11 | La policy Cloud Armor du projet de production n'avait **que l'ancienne règle `throttle`** (100 req/min/IP) : le ban par IP et le filtre de taille, écrits et testés sur un autre projet, n'y avaient jamais été appliqués. Dans l'API, la file faisait patienter jusqu'à 20 s au lieu de refuser | **Confirmé** |
| `/model` découvert via l'OpenAPI | `/docs` et `/openapi.json` étaient publics | **Confirmé** |
| Extraction à 489/min | Les 6 scores de probabilité étaient renvoyés à chaque requête ; 489/min ≈ 5 IP × 100 req/min, compatible avec un throttle par IP sans ban | **Confirmé** |
| En-têtes manquants | HSTS et X-Content-Type-Options **étaient présents** sur les réponses de l'API (vérifié sur `/health`). L'audit a vraisemblablement lu des réponses `429` générées par Cloud Armor, qui ne passent pas par l'API. CSP et X-Frame-Options manquaient réellement | **En partie inexact** |
| Flapping (vague 5) | Endpoints synchrones : sous flood, les 40 threads de FastAPI étaient bloqués dans la file ; `/health` attendait un thread libre, les sondes échouaient et le pod était retiré du trafic | **Confirmé, cause trouvée** |

**Reproduction en local** (même version, flood de 100 requêtes simultanées depuis une IP, banc
`bench_defense.py`) : latence médiane des clients légitimes de 39 ms à **1 001 ms (×26)**, 100 %
des requêtes de l'attaquant atteignent le modèle, débit de modération divisé par 4. Le
phénomène mesuré par le professeur (×11 en production, avec 3 pods) est bien reproduit.

## 3. Différence avec l'attaque de « Report Jungle »

Le 13/11, le groupe « Report Jungle » avait été arrêté par Cloud Armor, **en amont** des pods
(0 erreur côté serveur, voir [defense_analysis.md](defense_analysis.md)). Le professeur, lui, est
resté sous les seuils du load balancer (ou a réparti sa charge sur plusieurs IP) : la charge est
arrivée jusqu'à l'application, et c'est **l'application elle-même** qui a cédé. Conclusion : une
défense au load balancer ne suffit pas, il faut aussi que l'API refuse vite et équitablement ce
qu'elle ne peut pas traiter.

## 4. Améliorations qui en découlent

| Constat | Amélioration | Fichier | Effet mesuré (banc local, même attaque) |
|---|---|---|---|
| Ban jamais déployé | Script Cloud Armor qui crée la policy et les règles sur un projet neuf ; ban 10 min au-delà de 200 req/min | `k8s/cloud-armor-setup.sh` | Non mesurable en local (à valider en production) |
| Latence ×11 : tout est accepté | **Plafond de 4 requêtes en cours par IP** (`429` immédiat) ; **délestage rapide** : file de 8 places, 2 s d'attente au plus (`503`) | `toxicity/ip_limit.py`, `api.py` | Latence médiane des clients légitimes **1 001 → 81 ms** ; débit de modération 3,2 → 11,9 commentaires/s |
| Une IP qui spamme remplit la file | **File équitable** : file pleine, on éjecte une requête de l'IP la plus présente | `toxicity/fair_queue.py` | Flood réparti sur 25 IP : clients légitimes servis en 223 ms au lieu de 2 s, à hauteur de leur part équitable |
| Flapping | Endpoints asynchrones : une requête en attente n'occupe aucun thread | `api.py` | `/health` sous charge : 620–830 ms → **2–12 ms** ([defense_changes.md §8d](defense_changes.md)) |
| Extraction via les scores | Réponse réduite à `{"is_toxic": …}` | `api.py` | Un bit d'information par requête au lieu de 6 probabilités ; vérifié par les tests |
| `/model` trouvé via l'OpenAPI | `/docs`, `/redoc`, `/openapi.json` fermés en production | `api.py` | `404` (tests automatiques) |
| En-têtes | CSP, X-Frame-Options, Referrer-Policy ajoutés | `api.py` | Présents sur toutes les réponses de l'API, refus compris (tests automatiques) |
| Corps lus en entier avant validation | Limite de 4096 octets vérifiée avant lecture, même sans `Content-Length` | `toxicity/body_limit.py` | `413` avant le contrôle de clé (tests automatiques) |

Toutes ces défenses sont vérifiées à chaque push par la pipeline (job `tests`, voir
[livrable_tp2/5_devsecops.md](../livrable_tp2/5_devsecops.md)).

## 5. Limites et suite

- **Pas encore de nouvelle attaque en production** : l'effet du ban Cloud Armor et des défenses
  applicatives sur l'API déployée reste à mesurer lors de la prochaine vague (tableau prêt dans
  [livrable_tp2/3_defense.md §6](../livrable_tp2/3_defense.md)). Activer les logs du load
  balancer le temps de l'attaque pour voir les requêtes bloquées par Cloud Armor.
- **Attaque distribuée** : si le professeur répartit sa charge sur beaucoup d'IP restant chacune
  sous les seuils, les défenses par IP ne le distinguent pas des clients légitimes. Mesuré en
  local avec 25 IP : 57 % des requêtes légitimes refusées (rapidement). Pour savoir si la vague 6
  était distribuée, compter les IP distinctes dans les logs (commande dans
  [defense_changes.md §7](defense_changes.md)).
- **Extraction** : réduire la réponse à un booléen freine l'extraction sans la rendre impossible
  (attaque « label-only »), comme le notait l'audit.
