"""Résume les résultats Locust de loadtest/results/ en un tableau Markdown pour le rapport.

Pour chaque test : requêtes, débit moyen et max (req/s), latences (moyenne, P50, P95, P99,
max), taux d'erreur, nombre max de pods (HPA), et les erreurs les plus fréquentes.

Usage : uv run python loadtest/summarize.py
"""

import csv
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CONFIG = tomllib.loads((_HERE / "config.toml").read_text(encoding="utf-8"))
RESULTS = _HERE.parent / _CONFIG["monitoring"]["results_dir"]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _num(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "", "N/A") else 0.0
    except ValueError:
        return 0.0


def _max_pods(path: Path) -> str:
    pods = [int(p) for line in path.read_text().splitlines()[1:] if len(parts := line.split()) >= 3
            for p in [parts[2]] if p.isdigit()] if path.exists() else []
    return str(max(pods)) if pods else "?"


def summarize(prefix: Path) -> tuple[str, list[str]]:
    total = next((r for r in _rows(Path(f"{prefix}_stats.csv")) if r["Name"] == "Aggregated"), None)
    if total is None:
        return "", []
    history = [r for r in _rows(Path(f"{prefix}_stats_history.csv")) if r["Name"] == "Aggregated"]
    requests, failures = _num(total["Request Count"]), _num(total["Failure Count"])
    row = " | ".join([
        prefix.name,
        f"{requests:.0f}",
        f"{_num(total['Requests/s']):.1f}",
        f"{max((_num(r['Requests/s']) for r in history), default=0):.1f}",
        f"{_num(total['Average Response Time']):.0f}",
        f"{_num(total['50%']):.0f}",
        f"{_num(total['95%']):.0f}",
        f"{_num(total['99%']):.0f}",
        f"{_num(total['Max Response Time']):.0f}",
        f"{100 * failures / requests:.2f}" if requests else "0",
        _max_pods(Path(f"{prefix}_hpa.txt")),
    ])
    errors = [f"- {prefix.name} : {r['Error']} × {r['Occurrences']}"
              for r in sorted(_rows(Path(f"{prefix}_failures.csv")), key=lambda r: -_num(r["Occurrences"]))[:3]]
    return f"| {row} |", errors


def main() -> None:
    prefixes = sorted(RESULTS.glob("*_stats.csv"))
    if not prefixes:
        raise SystemExit(f"Aucun résultat dans {RESULTS}")
    print("| Test | Requêtes | Débit moy. (req/s) | Débit max (req/s) | Latence moy. (ms) | P50 | P95 | P99 "
          "| Max (ms) | Erreurs (%) | Pods max |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    all_errors = []
    for stats in prefixes:
        line, errors = summarize(Path(str(stats).removesuffix("_stats.csv")))
        if line:
            print(line)
            all_errors += errors
    if all_errors:
        print("\nErreurs les plus fréquentes :")
        print("\n".join(all_errors))


if __name__ == "__main__":
    main()
