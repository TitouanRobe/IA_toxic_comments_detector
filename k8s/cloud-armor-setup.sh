#!/usr/bin/env bash
# Configuration Cloud Armor de l'API toxic-api (défenses TP2, étape 3).
#
# Deux défenses au niveau du load balancer, AVANT que le trafic n'atteigne les pods :
#   1. Ban temporaire par IP : une IP qui dépasse durablement le quota est bloquée pour un temps
#      (pas seulement throttlée le temps d'une fenêtre). Le ban porte sur l'IP SOURCE et non sur
#      la clé d'API : comme toute l'équipe partage UNE SEULE clé, bannir sur la clé bloquerait
#      tout le monde. Sur l'IP, seule la source abusive est bloquée ; les autres clients
#      (mêmes clé, IP différente) continuent de passer.
#   2. Rejet des corps de requête surdimensionnés (sponge input) au load balancer, pour ne pas
#      consommer de ressources pod à parser un texte que le modèle tronquera de toute façon.
#
# Idempotent et utilisable sur un projet neuf comme existant : crée la policy si elle n'existe
# pas, crée chaque règle si elle est absente, la met à jour sinon (y compris l'ancienne règle
# 1000 "throttle" du README, convertie en "rate-based-ban").
# Prérequis : gcloud authentifié, projet positionné (gcloud config set project ...).
# La policy est attachée au load balancer par le BackendConfig de k8s/toxic-api.yaml.
#
# Usage : bash k8s/cloud-armor-setup.sh
set -euo pipefail

POLICY="toxic-api-policy"

if ! gcloud compute security-policies describe "$POLICY" &>/dev/null; then
  gcloud compute security-policies create "$POLICY" \
    --description "Protection de toxic-api : ban par IP + rejet des corps surdimensionnes"
fi

# Crée la règle si elle n'existe pas, la met à jour sinon. $1 = priorité, la suite = options.
upsert_rule() {
  local priority="$1"; shift
  if gcloud compute security-policies rules describe "$priority" --security-policy "$POLICY" &>/dev/null; then
    gcloud compute security-policies rules update "$priority" --security-policy "$POLICY" "$@"
  else
    gcloud compute security-policies rules create "$priority" --security-policy "$POLICY" "$@"
  fi
}

# --- Règle 900 : rejet des corps surdimensionnés (défense en profondeur du filtre applicatif) ---
# 4096 octets : au-dessus de la limite applicative (MAX_TEXT_LENGTH = 1500 car. ~ 1,5 Ko de corps
# JSON, + marge pour l'UTF-8 multioctet et les caractères échappés). Cloud Armor n'autorise pas
# deny(413) : on renvoie 403.
# Limite connue : un corps envoyé en "chunked" (sans Content-Length) n'est pas vu par cette règle.
# Il est alors lu en entier par le pod avant la validation (422) : la règle coupe le gros du trafic
# trivialement surdimensionné, elle ne remplace pas une limite de taille côté application.
upsert_rule 900 \
  --expression "has(request.headers['content-length']) && int(request.headers['content-length']) > 4096" \
  --action deny-403 \
  --description "Rejet des corps de requete surdimensionnes (sponge input)"

# --- Règle 1000 : limite de débit par IP AVEC ban temporaire -----------------------------------
# conform (<= 100 req/60s par IP) : autorisé.
# exceed (> 100 req/60s)          : 429.
# ban (> 120 req/60s par IP)      : l'IP est BANNIE 600 s (toutes ses requêtes -> 429), même si
#                                   elle ralentit ensuite. C'est la différence avec un simple
#                                   throttle : l'attaquant ne peut plus « pulser » sous le seuil.
upsert_rule 1000 \
  --src-ip-ranges "*" \
  --action rate-based-ban \
  --enforce-on-key IP \
  --rate-limit-threshold-count 100 \
  --rate-limit-threshold-interval-sec 60 \
  --exceed-action deny-429 \
  --conform-action allow \
  --ban-threshold-count 120 \
  --ban-threshold-interval-sec 60 \
  --ban-duration-sec 600 \
  --description "Rate limit par IP + ban temporaire 600s au-dela de 120 req/60s"

echo "OK. Règles de la policy $POLICY :"
gcloud compute security-policies describe "$POLICY" \
  --format="table(rules.priority, rules.action, rules.rateLimitOptions.banDurationSec)"
echo "Backends protégés :"
gcloud compute backend-services list --format="table(name, securityPolicy.basename())"
