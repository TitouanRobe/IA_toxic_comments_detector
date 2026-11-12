# Registre des activités de traitement (RGPD, art. 30)

Projet : IA_toxic_comments_detector
Dernière mise à jour : 2026-11-10

## 1. Responsable du traitement

| | |
|---|---|
| Responsable | Cléo Martin-Colleu : Titouan Robe |
| Contact | cleomarinmarie@gmail.com : robe.titouan@gmail.com |
| DPO | Cléo Martin-Colleu : Titouan Robe |

## 2. Traitement : détection de commentaires toxiques

| Rubrique | Description |
|---|---|
| **Finalité** | Entraîner et évaluer un modèle de classification de commentaires toxiques (toxic, severe_toxic, obscene, threat, insult, identity_hate). Projet académique. |
| **Base légale** | intérêt légitime |
| **Personnes concernées** | Auteurs de commentaires Wikipedia présents dans le jeu de données (non identifiés directement par le projet). |
| **Source des données** | Jeu de données public « Toxic Comment Classification » (Jigsaw / Kaggle), fichiers `train.csv`, `test.csv`, `test_labels.csv`. Aucune collecte directe. |
| **Catégories de données** | Texte libre des commentaires, identifiant du commentaire, labels de toxicité. |
| **Données personnelles détectées** | Noms de personnes, organisations, lieux, nationalités/groupes (NER spaCy) ; emails, adresses IP, téléphones, URL, IBAN, cartes bancaires, adresses postales, pseudonymes/`User:` (regex). Comptages dans `data/train_entities_totals.csv`. |
| **Données sensibles (art. 9)** | Possibles de façon incidente dans le texte libre (origine, religion, opinions via les commentaires de type `identity_hate`). Aucune donnée n'est collectée dans ce but. |
| **Destinataires** | Équipe projet uniquement. |
| **Transferts hors UE** | Aucun prévu. Hebergement Cloud UE |

## 2 bis. Traitement : API de scoring et journal d'accès

| Rubrique | Description |
|---|---|
| **Finalité** | Attribuer un score de toxicité aux textes envoyés par les applications clientes ; journaliser les accès pour la sécurité (détection d'abus, audit) et la supervision. |
| **Base légale** | Intérêt légitime (sécurité du service). |
| **Personnes concernées** | Applications clientes (identifiées par leur clé d'API) ; indirectement, les auteurs des textes analysés. |
| **Données traitées** | Texte analysé : traité en mémoire uniquement, **jamais stocké ni journalisé**. Journal d'accès : pseudonyme du client, pseudonyme de l'adresse IP, date, chemin, statut, latence, modèle, longueur du texte, score. |
| **Pseudonymisation** | Client et IP remplacés par un HMAC-SHA256 à clé secrète (`toxicity/audit_log.py`), irréversible sans la clé, stockée dans Secret Manager. Aucune IP en clair : journal d'uvicorn et logs du load balancer désactivés. |
| **Destinataires** | Équipe projet (owners du projet GCP). |
| **Hébergement** | GKE et Cloud Logging, région `europe-west1` (Belgique). |

## 3. Mesures de minimisation et de sécurité

| Étape | Mesure | Script |
|---|---|---|
| Audit | Comptage des entités et données personnelles par commentaire | `count_entities.py` |
| Anonymisation | Remplacement par des pseudonymes cohérents par commentaire (`[PERSON_1]`, `[EMAIL_1]`…) pour `PERSON`, `FAC`, `EMAIL`, `URL`, `IP_ADDRESS`, `PHONE`, `IBAN`, `CREDIT_CARD`, `ADDRESS`, `USERNAME` | `anonymize.py` |
| Conservation volontaire | `NORP`, `GPE`, `LOC`, `ORG` ne sont pas anonymisés (signal utile à la détection de toxicité) | `anonymize.py` |
| Texte d'origine | Supprimé du fichier de sortie par défaut (`KEEP_ORIGINAL = False`) | `anonymize.py` |
| Contrôle | Regex relancées sur le texte anonymisé pour détecter les fuites résiduelles | `anonymize.py` |
| Accès | Stockage local, poste de travail personnel [compléter : chiffrement du disque, droits d'accès] | |

**Limites connues** : le NER et les regex ne garantissent pas une anonymisation complète (noms mal étiquetés, adresses sans suffixe de rue reconnu, lieux conservés). Le résultat est donc à considérer comme **pseudonymisé** et non comme anonyme au sens strict.

## 4. Durée de conservation

| Donnée | Durée |
|---|---|
| Données brutes (`data/*.csv` d'origine) | [À définir, ex : durée du projet] |
| Données anonymisées (`train_anonymized.csv`) | [À définir] |
| Fichiers d'audit (`*_entities*.csv`, contiennent le texte original) | [À définir, à supprimer après usage recommandé] |
| Texte envoyé à l'API | Aucune : durée de la requête (mémoire uniquement) |
| Journal d'accès pseudonymisé (Cloud Logging) | 30 jours (rétention par défaut du bucket `_Default`) |

## 5. Droits des personnes

Les données proviennent d'un jeu public et ne sont pas ré-identifiées par le projet. Une personne concernée peut demander l'effacement d'un commentaire : contacter [email]. La suppression se fait par l'`id` du commentaire dans tous les fichiers dérivés.

## 6. Historique

| Date | Modification |
|---|---|
| 2026-11-10 | Création du registre |
| 2026-11-12 | Ajout du traitement « API de scoring et journal d'accès pseudonymisé » |
