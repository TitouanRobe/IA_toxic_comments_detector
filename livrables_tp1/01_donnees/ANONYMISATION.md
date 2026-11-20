# Étape 1 : exploration et anonymisation des données

## 1. Jeu de données

**Toxic Comment Classification** (Jigsaw / Kaggle) : commentaires de pages de discussion Wikipedia, en anglais, annotés selon 6 classes (`toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`).

| Fichier | Lignes | Usage |
|---|---|---|
| `data/train.csv` | 159 571 | Entraînement (90 %) et validation (10 %) |
| `data/test.csv` + `test_labels.csv` | 153 164 | Test (lignes étiquetées `-1` exclues) |

## 2. Repérage des données personnelles

Script : `count_entities.py` (spaCy `en_core_web_sm` + expressions régulières). Résultat par commentaire dans `data/test_entities.csv` (nom historique ; il porte sur les 159 571 commentaires du jeu d'entraînement).

| Type | Détecteur | Occurrences | Commentaires concernés | Donnée personnelle ? |
|---|---|---|---|---|
| `PERSON` | spaCy | 119 297 | 51 335 (32 %) | **Oui** : nom d'une personne |
| `ORG` | spaCy | 180 226 | 72 478 | Rarement (organisations) |
| `GPE` | spaCy | 61 916 | 27 285 | Non seule (pays, villes) |
| `NORP` | spaCy | 51 252 | 21 573 | Non seule (nationalités, religions, groupes) |
| `LOC` | spaCy | 7 379 | 4 990 | Non seule (lieux) |
| `FAC` | spaCy | 4 454 | 3 704 | Parfois (bâtiment, adresse d'un lieu précis) |
| `IP_ADDRESS` | regex | 11 145 | 9 956 | **Oui** : les contributeurs anonymes de Wikipedia signent par leur IP |
| `PHONE` | regex | 8 700 | 7 580 | **Oui** |
| `URL` | regex | 7 974 | 5 092 | Parfois (page personnelle, profil) |
| `EMAIL` | regex | 473 | 391 | **Oui** |
| `CREDIT_CARD` | regex | 31 | 22 | **Oui** |
| `IBAN` | regex | 5 | 3 | **Oui** |
| `USERNAME` (`User:…`) | regex | — | — | **Oui** : pseudonyme Wikipedia, identifiant indirect |
| `ADDRESS` | regex | — | — | **Oui** : adresse postale |

## 3. Anonymisation

Script : `anonymize.py`. Chaque donnée personnelle est remplacée par un **pseudonyme typé et numéroté, cohérent au sein d'un même commentaire** : la 1re personne citée devient `[PERSON_1]`, la 2e `[PERSON_2]`, et une même personne citée deux fois garde le même pseudonyme.

| Choix | Justification |
|---|---|
| **Pseudonymes typés** (`[PERSON_1]`) plutôt que suppression ou `[MASK]` | Le modèle garde la structure de la phrase (« [PERSON_1] is an idiot » reste une insulte visant quelqu'un) ; la suppression casserait la syntaxe |
| **Numérotation par commentaire** | Distingue « A insulte B » de « A parle de A », sans permettre de relier un même nom d'un commentaire à l'autre (pas de table de correspondance globale) |
| **Anonymisés** : `PERSON`, `FAC`, `EMAIL`, `URL`, `IP_ADDRESS`, `PHONE`, `IBAN`, `CREDIT_CARD`, `ADDRESS`, `USERNAME` | Identifient directement ou indirectement une personne |
| **Conservés** : `NORP`, `GPE`, `LOC`, `ORG` | Ce ne sont pas des données personnelles seules, et c'est le **signal même** de la classe `identity_hate` (« all [NORP] are… ») : les masquer dégraderait la détection des propos racistes ou haineux |
| **Regex prioritaires sur spaCy** en cas de chevauchement | Les regex sont exactes sur les formats structurés (IP, email, téléphone) ; spaCy se trompe sur ces chaînes |
| **Texte original supprimé** du fichier de sortie (`KEEP_ORIGINAL = False`) | Minimisation : le fichier anonymisé ne contient plus le texte d'origine |
| **Contrôle a posteriori** : les regex sont relancées sur le texte anonymisé | Détecte les fuites résiduelles |
| **Nettoyage après anonymisation** (`clean_text.py` : casse, ponctuation, emojis), pseudonymes protégés | Les pseudonymes restent intacts (`[PERSON_1]` n'est pas mis en minuscules), l'ordre garantit qu'aucune PII n'est « cassée » par le nettoyage avant d'être détectée |

## 4. Comparaison version initiale / anonymisée

### 4.1 Volume

| Jeu | Commentaires | Modifiés | Pseudonymes insérés (principaux) |
|---|---|---|---|
| Entraînement | 159 571 | **63 803 (40,0 %)** | `PERSON` 120 354 · `IP_ADDRESS` 10 755 · `URL` 7 974 · `USERNAME` 5 028 · `FAC` 4 399 · `EMAIL` 468 · `PHONE` 252 · `ADDRESS` 189 · `CREDIT_CARD` 24 · `IBAN` 1 |
| Test | 153 164 | **53 637 (35,0 %)** | `PERSON` 122 070 · `URL` 6 391 · `FAC` 3 976 · `USERNAME` 1 283 · `IP_ADDRESS` 868 · `EMAIL` 534 · `PHONE` 372 · `ADDRESS` 176 · `CREDIT_CARD` 16 |

Les labels de toxicité ne sont pas modifiés. Les données personnelles des exemples ci-dessous sont partiellement masquées (`▒`) dans ce document.


### 4.3 Bilan de la comparaison

- **Ce qui marche** : les formats structurés (IP, email, téléphone, URL, `User:`) sont remplacés de façon fiable. Ce sont les données les plus directement identifiantes du jeu (signatures des contributeurs).
- **Faux positifs** (masqués à tort) : mots en majuscule pris pour des noms (« Headquarters »), numéros longs pris pour des cartes bancaires. Coût faible : un peu de contexte perdu pour le modèle, aucun risque pour la vie privée.
- **Faux négatifs** (non masqués) : pseudonymes Wikipedia sans préfixe `User:` (« animeking▒▒▒ », « Parzival▒▒▒ »), noms écrits en minuscules ou mal étiquetés par spaCy. C'est le vrai risque résiduel.
- **Impact sur le modèle** : BERT entraîné sur les données anonymisées et nettoyées atteint une ROC-AUC moyenne de 0,985 sur le test anonymisé ([comparaison des modèles](../02_modeles/COMPARAISON_MODELES.md)) : l'anonymisation ne gêne pas la détection.

**Conclusion** : le résultat est **pseudonymisé**, pas anonyme au sens strict du RGPD (considérant 26) : un nom mal reconnu ou un pseudonyme peut subsister. Il reste donc soumis au RGPD et décrit dans le [registre de traitement](../04_securite_rgpd/REGISTRE_TRAITEMENT.md).

