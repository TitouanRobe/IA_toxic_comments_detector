"""Lance un scénario de test de charge (loadtest/config.toml) contre l'API déployée.

Pendant le test, en parallèle :
  - relève le nombre de pods et le CPU vus par l'autoscaler (HPA) -> <test>_hpa.txt ;
  - si le scénario a un failover_at, supprime un pod à cet instant -> <test>_events.txt.
À la fin, affiche le tableau récapitulatif de tous les tests (summarize.py).

Usage (depuis la racine du projet) :
    uv run python loadtest/run.py smoke     # vérification rapide (30 s)
    uv run python loadtest/run.py ramp      # ramp | soak | stress | spike
    uv run python loadtest/run.py --list    # scénarios disponibles
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import summarize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = tomllib.loads((HERE / "config.toml").read_text(encoding="utf-8"))
HPA_JSONPATH = ("{.status.desiredReplicas} {.status.currentReplicas} "
                "{.status.currentMetrics[0].resource.current.averageUtilization}")


def _kubectl(*args: str) -> str | None:
    try:
        r = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def watch_hpa(path: Path, interval: int, stop: threading.Event) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("heure pods_demandes pods_prets cpu_moyen_%\n")
        while not stop.is_set():
            if (status := _kubectl("get", "hpa", "toxic-api", "-o", f"jsonpath={HPA_JSONPATH}")) is not None:
                f.write(f"{time.strftime('%H:%M:%S')} {status}\n")
                f.flush()
            stop.wait(interval)


def failover(at: int, path: Path, stop: threading.Event) -> None:
    if stop.wait(at):  # test terminé avant l'instant prévu
        return
    pods = (_kubectl("get", "pods", "-l", "app=toxic-api", "-o", "jsonpath={.items[*].metadata.name}") or "").split()
    message = (f"{time.strftime('%H:%M:%S')} t={at}s : suppression du pod {pods[0]} ({len(pods)} pod(s) en cours)"
               if pods and _kubectl("delete", "pod", pods[0], "--wait=false") is not None
               else f"{time.strftime('%H:%M:%S')} t={at}s : échec de la suppression d'un pod (kubectl ?)")
    print(f"\n[failover] {message}\n", flush=True)
    path.write_text(message + "\n", encoding="utf-8")


def preflight(host: str) -> None:
    """Une requête de contrôle avant de lancer un test long : clé valide, Cloud Armor ouvert.
    Évite de mesurer pendant 15 min des 401 ou des 429 qui ne sollicitent pas le modèle."""
    key = os.environ.get("API_KEY", "").strip()
    key_file = ROOT / CONFIG["target"]["api_key_file"]
    if not key and key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
    if not (key.startswith("dss_") and len(key) == 47):
        raise SystemExit(f"Clé d'API invalide ({len(key)} caractères) : mettre dans {key_file} la clé "
                         "dss_... de 47 caractères affichée par make_api_key.py")
    req = urllib.request.Request(
        f"{host}/model", method="POST", headers={"X-API-Key": key, "Content-Type": "application/json"},
        data=json.dumps({"text": "Thanks for your help.", "model": CONFIG["target"]["model"]}).encode(),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"Contrôle préalable OK (HTTP {r.status})")
    except urllib.error.HTTPError as e:
        hint = {401: "clé refusée : son empreinte n'est pas dans le secret toxic-api-keys",
                429: "bloqué par Cloud Armor : exception pour cette IP absente ou pas encore active (1-2 min)"}
        raise SystemExit(f"Contrôle préalable en échec : HTTP {e.code} ({hint.get(e.code, e.reason)})") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Contrôle préalable en échec : API injoignable ({e.reason})") from e


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", nargs="?", help="nom du scénario (config.toml)")
    p.add_argument("--list", action="store_true", help="lister les scénarios")
    p.add_argument("--host", default=CONFIG["target"]["host"], help="remplace target.host de la config")
    a = p.parse_args()

    scenarios = CONFIG["scenarios"]
    if a.list or not a.scenario:
        for name, s in scenarios.items():
            duration = s["stages"][-1][0]
            print(f"{name:8} {f'{duration // 60} min' if duration >= 60 else f'{duration} s':>7}  {s.get('description', '')}")
        return
    if a.scenario not in scenarios:
        raise SystemExit(f"Scénario inconnu '{a.scenario}' : {', '.join(scenarios)}")
    scenario = scenarios[a.scenario]
    preflight(a.host)

    results = ROOT / CONFIG["monitoring"]["results_dir"]
    results.mkdir(parents=True, exist_ok=True)
    prefix = results / f"{a.scenario}-{time.strftime('%Y%m%d-%H%M')}"

    stop = threading.Event()
    threads = [threading.Thread(target=watch_hpa, daemon=True,
                                args=(Path(f"{prefix}_hpa.txt"), CONFIG["monitoring"]["hpa_interval"], stop))]
    if "failover_at" in scenario:
        threads.append(threading.Thread(target=failover, daemon=True,
                                        args=(scenario["failover_at"], Path(f"{prefix}_events.txt"), stop)))
    for t in threads:
        t.start()

    print(f"Scénario {a.scenario} ({scenario.get('description', '')}) contre {a.host}\n-> {prefix}_*", flush=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--host", a.host,
             "--headless", "--only-summary", "--csv", str(prefix), "--csv-full-history", "--html", f"{prefix}.html"],
            env={**os.environ, "SCENARIO": a.scenario}, cwd=ROOT,
        )
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=35)

    print()
    summarize.main()


if __name__ == "__main__":
    main()
