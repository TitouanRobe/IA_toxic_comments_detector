# Étape 4 — Fiche AI Act : détecteur de toxicité « Digital Social Score »

Règlement (UE) 2024/1689. Dernière mise à jour : 2026-11-19. La fiche d'identité du modèle est dans [model_card.json](../model_card.json), à la racine du projet ; sa conformité est vérifiée à chaque push par la CI (job `model-card`, voir [5_devsecops.md](5_devsecops.md)).

## 1. Le système de détection

| | |
|---|---|
| Fonction | Reçoit un texte, calcule 6 probabilités (classes Jigsaw) et renvoie uniquement la décision `is_toxic` (probabilité de la classe `toxic` > 70 %) |
| Entrée / sortie | Texte anglais (1500 caractères max) → `{"is_toxic": true/false}`. Le système signale un texte ; il ne prend aucune décision sur une personne |
| Modèles | DistilBERT affiné (`bert`, par défaut) et BiLSTM (`lstm`) |
| Données | Jigsaw Toxic Comment (commentaires Wikipedia), anonymisées avant entraînement |
| Rôle dans l'AI Act | Nous sommes **fournisseur** du système. Les clients de l'API sont **déployeurs** |

## 2. Catégorie de risque retenue : **risque minimal**

Avec une condition d'usage forte (voir §4). On l'a obtenue en parcourant les niveaux du règlement dans l'ordre.

| Niveau | Texte | Notre système en relève-t-il ? | Justification |
|---|---|---|---|
| **Risque inacceptable** (interdit) | Art. 5 | **Non, à condition de ne pas noter des personnes** | L'art. 5.1.c interdit la notation sociale : évaluer des personnes sur leur comportement social, avec un traitement défavorable à la clé. Notre score porte sur **un texte**, pas sur une personne : l'API ne stocke ni texte, ni historique par auteur, et ne calcule aucun score cumulé. Le nom « Social Score » est trompeur et ne doit pas inspirer une agrégation par personne |
| **Haut risque** | Art. 6 + Annexe III | **Non** | Aucun des 8 domaines de l'Annexe III n'est visé : ni biométrie, ni infrastructures critiques, ni éducation, ni emploi, ni accès à des services essentiels, ni police, ni migration, ni justice. La modération de contenu n'y figure pas. Le système n'est pas non plus un composant de sécurité d'un produit (Annexe I) |
| **Risque limité** (transparence) | Art. 50 | **Non** | Il n'interagit pas avec des personnes comme un agent conversationnel, ne génère ni image ni voix ni texte synthétique, et ne reconnaît pas d'émotions ni ne catégorise de personnes par données biométriques |
| **Risque minimal** | Art. 95 (codes de conduite volontaires) | **Oui** | Il reste ce niveau par élimination. Aucune obligation spécifique, mais les bonnes pratiques de ce document sont appliquées volontairement |
| **IA à usage général** (GPAI) | Art. 51 et suivants | **Non** | Notre modèle est spécialisé (6 classes). DistilBERT et ModernBERT sont des briques amont dont les fournisseurs portent les obligations GPAI. Nous les réutilisons sans les distribuer |

**Obligation qui s'applique quand même :** l'art. 4 (maîtrise de l'IA) oblige l'équipe à avoir un niveau suffisant de compréhension du système, ce que ce document et la model card servent à montrer.


## 3. Quand le classement changerait

| Usage du score par un client | Classement | Raison |
|---|---|---|
| Aide à la modération, relecture humaine avant toute action | Minimal (cas actuel) | Le texte est signalé, pas la personne |
| Suppression ou sanction automatique des auteurs | À risque : proche de l'art. 5.1.c | Traitement défavorable fondé sur le comportement |
| Score utilisé pour trier des candidats, évaluer des salariés ou des étudiants | **Haut risque** (Annexe III, points 3 et 4) | Emploi et éducation |
| Score cumulé par personne (« réputation ») | **Interdit** (art. 5.1.c) | Notation sociale |

Ces usages sont exclus dans `model_card.json` (`intended_use.out_of_scope`). Pour le contrôle, l'API devrait interdire contractuellement ces usages dans ses conditions d'utilisation.

## 4. Mesures de conformité en place (bonnes pratiques volontaires)

| Exigence de référence | Ce qui existe dans le projet |
|---|---|
| Gestion des données (art. 10) | Anonymisation NER + regex, registre RGPD (`REGISTRE_TRAITEMENT.md`), limites connues documentées |
| Documentation technique (art. 11) | `model_card.json`, ce document, `README.md`, schéma d'architecture |
| Traçabilité (art. 12) | Journal d'accès pseudonymisé (30 jours), `X-Request-ID` dans chaque réponse |
| Transparence (art. 13) | Fonctionnement, seuil, limites et usages exclus documentés (model card, `API.md`). Les scores détaillés ne sont plus renvoyés au client (voir ci-dessous) |
| Surveillance humaine (art. 14) | Décision = aide à la modération ; relecture humaine exigée avant toute action sur un texte signalé |
| Exactitude, robustesse, cybersécurité (art. 15) | Métriques sur jeu de test ; attaque DoS simulée puis défenses mesurées avant/après ([3_defense.md](3_defense.md)) : la précision et le taux de faux positifs restent identiques sous attaque, la disponibilité est protégée ; authentification, HTTPS, Cloud Armor (ban par IP), délestage, supervision Prometheus/Grafana ; pipeline DevSecOps à chaque push |

**Arbitrage transparence / sécurité.** Jusqu'au 17/11, l'API renvoyait les 6 scores. L'audit du professeur a montré qu'ils permettaient d'extraire le modèle (entraîner une copie à partir des scores). Nous ne renvoyons plus que la décision : c'est moins transparent pour le client, mais le modèle de risque minimal n'impose pas de transparence sur les scores (art. 13 vise les systèmes à haut risque), et les scores restent conservés côté serveur (journal pseudonymisé) pour l'analyse par l'équipe. Si un client avait besoin des scores pour sa relecture humaine, il faudrait les lui ouvrir avec une clé dédiée et un quota bas, plutôt que publiquement.

## 4 bis. Registre des risques du système

Probabilité et gravité : faible / moyenne / forte, évaluées par l'équipe.

| Risque | Probabilité | Gravité | Mesures en place | Risque résiduel |
|---|---|---|---|---|
| **Biais** : sur-signalement des textes mentionnant des minorités (gay, black, muslim…) | Forte (mesuré) | Forte : discrimination, censure de groupes visés | Relecture humaine obligatoire, usage documenté comme aide, biais déclaré dans la model card | **Fort** tant que le modèle n'est pas rééquilibré |
| **Faux positifs** (texte sain signalé) | Forte : 13 % des textes sains signalés au seuil 70 (mesuré sur 200 textes sains, [3_defense.md](3_defense.md)) | Moyenne : charge de relecture, frustration, sentiment de censure | Seuil à 70 plutôt que 50, relecture humaine | Moyen |
| **Indisponibilité (DoS)** : la modération s'arrête, les textes toxiques passent | Forte (subi : vagues 5 et 6) | Forte : exposition des utilisateurs à des contenus haineux | Cloud Armor (ban IP), plafond par IP, file équitable, délestage rapide, autoscaling | Moyen : une attaque très distribuée reste possible |
| **Extraction du modèle** | Moyenne (constatée par l'audit) | Moyenne : copie du modèle, contournement préparé hors ligne | Décision seule renvoyée, limites de débit, ban | Faible à moyen |
| **Fuite de données personnelles** (textes, IP) | Faible | Forte (RGPD) | Texte jamais stocké, journal pseudonymisé (HMAC), HTTPS, secrets dans Secret Manager | Faible |
| **Usage détourné** : note de « réputation » par personne (notation sociale) | Faible | Très forte : pratique interdite (art. 5.1.c) | Usages exclus dans la model card, aucun historique par auteur | Moyen : pas encore d'interdiction contractuelle |

## 5. Écarts restants et actions

| # | Écart | Action | Priorité |
|---|---|---|---|
| 1 | **Biais identitaires** mesurés sur un test minimal | Évaluer sur un jeu dédié (ex. Jigsaw Unintended Bias), rééquilibrer ou augmenter les données, recalibrer le seuil | Haute |
| 2 | **Précision d'environ 44 %** à seuil 50 sur le jeu de test réel (10 % de toxiques) : beaucoup de faux positifs. Le seuil de l'API est à 70 | Choisir le seuil selon le coût d'une erreur, évaluer sur tout le jeu de test | Haute |
| 3 | Aucune interdiction contractuelle des usages exclus | Ajouter des conditions d'utilisation de l'API | Moyenne |
| 4 | Anglais uniquement | Le préciser à chaque client, ou entraîner un modèle multilingue | Moyenne |
| 5 | Durées de conservation des données d'entraînement non définies (registre §4) | Compléter le registre | Moyenne |
| 6 | Information des utilisateurs finaux | Les déployeurs doivent informer les auteurs qu'un système automatique analyse leurs textes | Moyenne |

## 6. Conclusion

Le détecteur relève du **risque minimal** de l'AI Act : il n'est ni interdit, ni à haut risque, ni soumis aux obligations de transparence, **parce que** il évalue des textes, pas des personnes, et qu'un humain reste dans la boucle. Ce classement ne tient que si ces deux conditions sont respectées. 