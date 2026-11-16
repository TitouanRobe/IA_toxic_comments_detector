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
# Idempotent : relançable sans risque (update si la règle existe, create sinon).
# Prérequis : gcloud authentifié, projet positionné (gcloud config set project ...).
set -euo pipefail

POLICY="toxic-api-policy"

# --- Règle 1000 : limite de débit par IP AVEC ban temporaire -----------------------------------
# conform (<= 100 req/60s par IP) : autorisé.
# exceed (> 100 req/60s)          : 429.
# ban (> 120 req/60s par IP)      : l'IP est BANNIE 600 s (toutes ses requêtes -> 429), même si
#                                   elle ralentit ensuite. C'est la différence avec un simple
#                                   throttle : l'attaquant ne peut plus « pulser » sous le seuil.
gcloud compute security-policies rules update 1000 \
  --security-policy "$POLICY" \
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

# --- Règle 900 : rejet des corps surdimensionnés (défense en profondeur du filtre applicatif) ---
# 4096 octets : au-dessus de la limite applicative (MAX_TEXT_LENGTH = 1500 car. ~ 1,5 Ko de corps
# JSON, + marge pour l'UTF-8 multioctet). Cloud Armor n'autorise pas deny(413) : on renvoie 403.
# Limite connue : contournable en « chunked » (pas de Content-Length) -> la vérif applicative
# (api.py, 422 si texte > 1500) reste le garde-fou fiable ; ceci coupe juste le gros du trafic tôt.
EXPR="has(request.headers['content-length']) && int(request.headers['content-length']) > 4096"
if gcloud compute security-policies rules describe 900 --security-policy "$POLICY" &>/dev/null; then
  gcloud compute security-policies rules update 900 \
    --security-policy "$POLICY" \
    --expression "$EXPR" --action deny-403 \
    --description "Rejet des corps de requete surdimensionnes (sponge input)"
else
  gcloud compute security-policies rules create 900 \
    --security-policy "$POLICY" \
    --expression "$EXPR" --action deny-403 \
    --description "Rejet des corps de requete surdimensionnes (sponge input)"
fi

echo "OK. Etat de la policy :"
gcloud compute security-policies describe "$POLICY" \
  --format="table(rules.priority, rules.action, rules.rateLimitOptions.banDurationSec)"
