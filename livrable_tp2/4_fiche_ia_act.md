# Fiche AI Act : détecteur de toxicité « Digital Social Score »

Règlement (UE) 2024/1689. Dernière mise à jour : 2026-11-16. La fiche d'identité du modèle est dans [model_card.json](model_card.json).

## 1. Le système de détection

| | |
|---|---|
| Fonction | Reçoit un texte, renvoie un score de toxicité de 0 à 100 et 6 scores par classe |
| Entrée / sortie | Texte anglais (1500 caractères max) → scores. Aucune décision n'est prise par le système |
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
| Transparence (art. 13) | Scores par classe renvoyés, limites et usages exclus documentés |
| Surveillance humaine (art. 14) | Score = aide à la décision ; relecture humaine exigée avant toute action |
| Exactitude, robustesse, cybersécurité (art. 15) | Métriques sur jeu de test, test d'attaque (`attack/`), authentification, HTTPS, Cloud Armor, supervision Prometheus/Grafana |

## 5. Écarts restants et actions

| # | Écart | Action | Priorité |
|---|---|---|---|
| 1 | **Biais identitaires** mesurés sur un test minimal | Évaluer sur un jeu dédié (ex. Jigsaw Unintended Bias), rééquilibrer ou augmenter les données, recalibrer le seuil | Haute |
| 2 | **Précision d'environ 44 %** à seuil 50 : beaucoup de faux positifs | Choisir le seuil selon le coût d'une erreur, évaluer sur tout le jeu de test | Haute |
| 3 | Aucune interdiction contractuelle des usages exclus | Ajouter des conditions d'utilisation de l'API | Moyenne |
| 4 | Anglais uniquement | Le préciser à chaque client, ou entraîner un modèle multilingue | Moyenne |
| 5 | Durées de conservation des données d'entraînement non définies (registre §4) | Compléter le registre | Moyenne |
| 6 | Information des utilisateurs finaux | Les déployeurs doivent informer les auteurs qu'un système automatique analyse leurs textes | Moyenne |

## 6. Conclusion

Le détecteur relève du **risque minimal** de l'AI Act : il n'est ni interdit, ni à haut risque, ni soumis aux obligations de transparence, **parce que** il évalue des textes, pas des personnes, et qu'un humain reste dans la boucle. Ce classement ne tient que si ces deux conditions sont respectées. 