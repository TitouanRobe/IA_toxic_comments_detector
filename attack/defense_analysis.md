# Analyse croisée — Attaque reçue du groupe « Report Jungle »

**Objet :** confronter la mesure *externe* de l'attaquant (son script `attack.py`, exécuté
contre `https://toxic-api.cacaland.fr/model`) à la mesure *interne* côté serveur (logs
structurés Cloud Logging de `toxic-api`), pour la même fenêtre de temps.

**Fenêtre de l'attaque :** 13/11/2026, 15:13 → 15:52 UTC (16:13 → 16:52 heure locale),
reconstituée à partir des horodatages de création des fichiers `results/dos_*.{csv,png}`
fournis par le groupe attaquant.

**Sources :**
- Côté attaquant : `attack/` (dossier reçu, voir `README.md`, `ETAPE2_resultats.md`,
  `results/dos_ramp.csv`, `results/dos_sustained.csv`, `results/dos_payload_size.csv`).
- Côté défenseur : Cloud Logging GKE, filtré sur
  `resource.labels.container_name="toxic-api"` et `jsonPayload.event="request"`,
  `jsonPayload.path="/model"`, 2026-11-13T15:10:00Z → 16:00:00Z (1189 requêtes).
  Prometheus/Grafana avaient déjà été désactivés et leur disque supprimé (arrêt des coûts) :
  Cloud Logging est la seule source encore disponible pour cette fenêtre.

---

## 1. Ce que l'attaquant a mesuré (vue externe)

| Volet | Résultat |
|---|---|
| Taille d'entrée (10 → 5000 car.) | Latence médiane ×4 (79 ms → 310 ms) |
| Montée en charge (concurrence) | Rupture dès **20 req. simultanées** (79 % d'échec) ; **100 % de rejet dès 40** |
| Charge soutenue (20 req/s, 60 s) | **100 % d'échec quasi continu**, une seule fenêtre de récupération de ~5 s |

Leur conclusion (`ETAPE2_resultats.md`) : *« le rate limit finit par rejeter tout le trafic,
y compris celui des utilisateurs légitimes »* — l'attaque réussit à rendre le service
indisponible.

## 2. Ce qui s'est réellement passé côté serveur (vue interne)

1189 requêtes `/model` reçues par les pods `toxic-api` pendant exactement cette fenêtre :

| Métrique | Valeur |
|---|---|
| Codes HTTP observés | **1085 × 200**, 104 × 404 (modèle inconnu demandé, sans rapport avec l'attaque) |
| Codes 429 / 503 / 5xx reçus par l'application | **0** |
| Latence p50 | 131 ms |
| Latence p95 | 335 ms |
| Latence p99 | 667 ms |
| Latence max | 935 ms |
| Redémarrage de pod pendant la fenêtre | Aucun (le seul restart observé a eu lieu 33 min après leur dernière requête, sans rapport) |

**Conclusion : le backend a traité 100 % des requêtes qui l'ont atteint, sans la moindre
erreur, avec une latence qui reste sous la seconde même au pic de charge.**

## 3. Rapprochement des deux mesures

| | Vue attaquant (externe) | Vue défenseur (interne) |
|---|---|---|
| Taux d'échec mesuré | jusqu'à 100 % | **0 %** |
| Codes d'erreur | 429 (rate limit) | aucun |
| Latence sous charge | p95 jusqu'à 2,8 s (ramp), ~1 s (sustained) | p95 335 ms, p99 667 ms |
| Service indisponible ? | oui, pendant ~50 s sur 60 s | non, jamais |

L'écart s'explique par la **position du rejet dans la chaîne** : les `429` que l'attaquant
observe n'apparaissent **jamais** dans les logs applicatifs de `toxic-api`. Ils sont donc
émis **en amont du pod** — très probablement par Cloud Armor au niveau du Load Balancer
(couche non instrumentée ici : logging Cloud Armor non activé sur le projet, donc pas de
preuve directe, mais c'est la seule couche de la chaîne qui explique que ces requêtes
n'atteignent jamais l'application).

## 4. Interprétation pour l'étape 3 (efficacité de la défense)

**La défense a fonctionné comme prévu, pas comme un échec.** Le rapport de l'attaquant décrit
à juste titre un risque réel : un rate limit agressif peut bloquer des utilisateurs légitimes
en même temps qu'un attaquant (c'est une vraie limite de conception à corriger — voir leurs
pistes de défense, section 4 de leur `ETAPE2_resultats.md`). Mais sur **cette attaque précise**,
les faits mesurés côté serveur montrent que :

- le **modèle IA n'a jamais été mis en danger** (0 erreur, latence stable) ;
- la dégradation perçue par l'attaquant est le **signe que la protection absorbe le choc en
  amont**, exactement son rôle ;
- un attaquant qui ne regarde que les codes HTTP qu'il reçoit **ne peut pas distinguer**
  « le serveur a planté » de « le pare-feu m'a filtré avant le serveur » — d'où l'intérêt de
  croiser mesure externe et logs internes pour évaluer une vraie défense.

**Limite de cette analyse** : sans logs Cloud Armor/LB activés, l'endroit exact du rejet
(Cloud Armor ? autre règle de l'Ingress ?) reste une déduction, pas une preuve directe. Pour
une preuve complète, il faudrait activer le logging Cloud Armor *avant* la prochaine attaque.

## 5. Recommandation

- **Documenter cette limite dans le rapport final** : activer le logging Cloud Armor/Load
  Balancer pour la prochaine campagne de tests, afin de confirmer directement la couche de
  rejet plutôt que de la déduire par élimination.
- Garder la remarque du groupe attaquant sur le risque de rate limit trop brutal — elle reste
  valable en théorie même si elle ne s'est pas matérialisée ici (le trafic légitime n'a pas été
  mesuré en parallèle de l'attaque, donc on ne peut pas non plus exclure qu'il ait, lui aussi,
  été bloqué par Cloud Armor pendant la même fenêtre).
