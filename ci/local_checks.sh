#!/usr/bin/env bash
# Rejoue en local les contrôles de la pipeline qui ne demandent pas Docker, avec les mêmes
# commandes que .github/workflows/devsecops.yml, et enregistre un log par contrôle.
#
# Usage (racine du projet) : bash ci/local_checks.sh [dossier_des_logs]
# Défaut : livrable_tp2/logs/. Code de sortie : nombre de contrôles en échec.
set -u
cd "$(dirname "$0")/.."
OUT="${1:-livrable_tp2/logs}"
mkdir -p "$OUT"
export PYTHONIOENCODING=utf-8
REQ="$(mktemp)"
failures=0

check() {  # check <nom> <commande...>
  local name=$1; shift
  local log="$OUT/$name.log" rc
  {
    echo "\$ $*"
    echo "# $(date '+%Y-%m-%d %H:%M:%S %z') - commit $(git rev-parse --short HEAD)$(git diff --quiet HEAD || echo ' + modifications non commitées')"
    echo
  } > "$log"
  "$@" >> "$log" 2>&1
  rc=$?
  printf '\n# code de sortie : %s\n' "$rc" >> "$log"
  if [ "$rc" -eq 0 ]; then echo "OK      $name"; else echo "ÉCHEC   $name (voir $log)"; failures=$((failures + 1)); fi
}

check ruff uvx ruff check .
check bandit uvx bandit -c pyproject.toml -r . -ll
uv export --frozen --no-default-groups --no-emit-project --no-hashes -o "$REQ" > /dev/null 2>&1
check pip-audit uvx pip-audit -r "$REQ" --no-deps --disable-pip
check pytest uv run pytest -v
check model-card python ci/check_model_card.py
rm -f "$REQ"
exit "$failures"
