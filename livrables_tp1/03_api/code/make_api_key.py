"""Génère une clé d'API et son empreinte SHA-256.

- La CLÉ est donnée au client (une seule fois) : elle n'est stockée nulle part côté serveur.
- L'EMPREINTE est mise dans Google Secret Manager : c'est tout ce que l'API connaît.

Usage :
    python make_api_key.py              # affiche la clé et son empreinte
    python make_api_key.py --hash-only  # n'affiche que l'empreinte d'une clé lue sur l'entrée standard
"""

import argparse
import hashlib
import secrets
import sys

PREFIX = "dss_"  # "Digital Social Score" : une clé qui fuite (log, dépôt git) est facile à repérer


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()  # même calcul que api.hash_api_key


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hash-only", action="store_true", help="empreinte d'une clé existante (lue sur stdin)")
    if p.parse_args().hash_only:
        print(hash_api_key(sys.stdin.read().strip()))
        return
    key = PREFIX + secrets.token_urlsafe(32)  # 256 bits aléatoires
    print(f"Clé (à donner au client, ne pas stocker) : {key}")
    print(f"Empreinte (à mettre dans Secret Manager) : {hash_api_key(key)}")


if __name__ == "__main__":
    main()
