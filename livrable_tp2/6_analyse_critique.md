# Analyse critique et recommandations

## 1. Ce qui a fonctionné

- **Les défenses en couches.** Aucune barrière ne suffit seule : Cloud Armor arrête le gros volume avant les pods, mais l'attaque du professeur est passée sous ses seuils ; dans l'API, le plafond par IP, la file équitable et le délestage ont pris le relais. Le banc ([3_defense.md](3_defense.md)) montre l'effet de la couche applicative seule.
- **Croiser la vue de l'attaquant et la vue du serveur.** Le 13/11, l'attaquant mesurait jusqu'à 100 % d'échecs et concluait que le service était tombé ; nos logs montraient 0 erreur et une latence normale, car Cloud Armor filtrait en amont. Sans les deux vues, chaque camp aurait tiré une conclusion fausse.
- **Mesurer la précision et les faux positifs pendant l'attaque**, et pas seulement la latence : cela permet d'affirmer, chiffres à l'appui, qu'un DoS ne change pas les décisions du modèle (100 % de décisions identiques) mais réduit le nombre de textes modérés.

## 2. Nos erreurs, et ce qu'elles enseignent

| Erreur | Conséquence | Leçon |
|---|---|---|
| Le ban par IP était écrit et testé, mais **jamais appliqué** sur le projet GCP de production (le script ne gérait pas un projet neuf) | Vague 6 : seule l'ancienne limite de débit protégeait l'API | Une défense non déployée n'existe pas. Il faut une infrastructure décrite en code et appliquée par la pipeline, avec une vérification après déploiement (la règle est-elle active ?) |
| File d'attente qui faisait patienter jusqu'à 20 s | L'attaque était « acceptée mais lente » : latence ×11 | Pour une API temps réel, refuser vite (`503` en quelques ms) vaut mieux que servir trop tard |
| Endpoints synchrones : une requête en attente bloquait un thread | `/health` ne répondait plus sous charge, pods retirés du trafic (flapping) | La santé d'un service ne doit pas dépendre de la charge métier |
| Scores détaillés renvoyés au client | Modèle extractible (489 requêtes/min) | Ne renvoyer que ce dont le client a besoin : chaque information exposée est une surface d'attaque |
| Biais de notre propre outil de mesure : un client Python plafonne vers 55 requêtes/s | Une partie des latences mesurées contre l'autre groupe vient de notre client | Avant de conclure sur la cible, vérifier que l'outil de mesure n'est pas le goulot (on l'a corrigé dans le banc : attaquants et clients légitimes dans des processus séparés) |

## 3. Limites de nos résultats

- **Les défenses par IP ne suffisent pas contre une attaque distribuée.** Mesuré : avec 25 IP attaquantes restant chacune sous le plafond, 57 % des requêtes légitimes sont refusées. La file équitable donne aux clients légitimes leur juste part (11 % des inférences pour 4 IP sur 29), mais une juste part reste petite face à beaucoup d'attaquants. Seules plus de capacité ou une identification par client (clé par client) changent cela.
- **Intensité d'attaque bornée par notre client.** Nos attaquants plafonnent entre 48 et 91 requêtes/s (client Python, même machine que le serveur). C'est du même ordre que la vague 6 (84 requêtes/s acceptées), mais pas un DDoS de grande ampleur.
- **Cloud Armor et l'autoscaling ne sont pas dans le banc.** Le banc mesure la couche applicative seule, sur une machine locale. En production, Cloud Armor bannirait en plus le flood depuis 1 IP en quelques secondes ; l'autoscaling ajouterait des pods, mais en 1 à 2 minutes, après la fin d'un pic court.
- **Pas encore de mesure en production après défense.** La dernière colonne du tableau de [3_defense.md §6](3_defense.md) sera remplie lors de la prochaine attaque, avec les logs du load balancer activés le temps du test.
- **Échantillon équilibré** (50 % de toxiques) : la précision mesurée (89 %) est plus élevée que sur le flux réel, où environ 10 % des commentaires sont toxiques (précision ~44 % à seuil 50 sur le jeu de test complet). Le taux de faux positifs, lui, ne dépend pas de cette proportion.
- **Une seule attaque étudiée.** L'évasion adversariale (fautes volontaires, caractères spéciaux pour tromper le modèle) n'a pas été testée ; c'est probablement la faiblesse principale du modèle lui-même, indépendamment de l'infrastructure.

## 4. Arbitrages assumés

| Arbitrage | Notre choix | Ce qu'on perd |
|---|---|---|
| **Latence vs disponibilité** | Refuser vite le surplus (`503`, `429`) plutôt que tout accepter | Sous attaque, une partie des requêtes légitimes est refusée au lieu d'être servie lentement |
| **Transparence vs extraction du modèle** | Renvoyer seulement `is_toxic` | Le client ne voit plus les scores par classe ; un modérateur humain a moins d'éléments (voir [4_fiche_ia_act.md](4_fiche_ia_act.md)) |
| **Ban par IP vs clé partagée** | Limiter par IP, puisque toute l'équipe partage une seule clé | Des utilisateurs derrière la même IP que l'attaquant (réseau d'école, NAT d'entreprise) sont bannis avec lui |
| **Modération « fail-open »** | Quand l'API refuse, le client décide ; un texte non analysé n'est pas signalé | Pendant une attaque, des textes toxiques peuvent passer. L'alternative « fail-closed » (bloquer les textes non analysés) censurerait tout le monde : c'est au client de mettre ces textes en attente de modération plutôt que de les publier |

## 5. Recommandations, par priorité

| Priorité | Recommandation | Gain attendu |
|---|---|---|
| 1 | **Déploiement continu** : la pipeline applique l'image, le manifeste et `cloud-armor-setup.sh` sur GKE uniquement si tous les contrôles passent, puis vérifie que les règles sont actives | Plus de défense « écrite mais pas déployée » (cause de la vague 6) |
| 2 | **Une clé d'API par client**, avec quota et révocation individuels | Limiter et bannir par client plutôt que par IP : plus de dommages collatéraux sur une IP partagée, et une réponse aux attaques réparties sur de nombreuses IP |
| 3 | **Capacité prête avant le pic** : `minReplicas` plus haut en période d'attaque, modèle dans l'image plutôt que lu depuis le bucket, scaling sur la longueur de file plutôt que sur le CPU | Moins de refus légitimes face à une attaque répartie que les filtres par IP ne voient pas |
| 4 | **Côté client, file de modération** : un texte refusé (`503`/`429`) est mis en attente et réessayé, jamais publié sans analyse | Supprime l'impact éthique du DoS (les textes toxiques ne passent plus pendant l'attaque) |
| 5 | **Réduire le biais identitaire** : jeu d'évaluation dédié, rééquilibrage, seuil calibré | Principal risque éthique du système, plus grave que le DoS au quotidien |
| 6 | **Tester l'évasion adversariale** et l'ajouter au banc (`bench_defense.py`), lancé chaque semaine par la CI | Couvre la menace sur le modèle lui-même, et détecte une régression des défenses |
| 7 | **Logs du load balancer activables à la demande** pendant un incident, conservés peu de temps | Preuve directe de ce que Cloud Armor a bloqué, sans garder d'IP en clair au quotidien (RGPD) |
