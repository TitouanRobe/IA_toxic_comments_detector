"""Banc d'essai avant / après défense (TP2, étapes 2 et 3).

Lance NOTRE API en local, l'attaque par déni de service (DoS L7) et mesure, PENDANT l'attaque,
ce que vivent des clients légitimes qui envoient de vrais commentaires étiquetés du jeu de test
Jigsaw : précision du modèle, taux de faux positifs, latence, disponibilité.

Deux versions de l'API sont comparées sur exactement le même protocole :
  - « avant » : la version attaquée par le professeur (vague 6), commit 451d731 ;
  - « après » : la version actuelle du dépôt (défenses de attack/defense_changes.md).

Usage (depuis la racine du projet) :
  uv run python attack/bench_defense.py run --ref 451d731 --label avant
  uv run python attack/bench_defense.py run --label apres
  uv run python attack/bench_defense.py report     # tableau + graphiques dans livrable_tp2/

Scénarios (même ordre et mêmes durées pour les deux versions) :
  repos            aucun attaquant : qualité et latence de référence ;
  flood_1ip        1 IP attaquante, 100 requêtes simultanées en continu (type vague 6) ;
  flood_distribue  25 IP attaquantes x 4 requêtes simultanées (attaque répartie, chaque IP
                   reste sous le plafond par IP : teste la limite des défenses par IP).
Clients légitimes : 4 utilisateurs (4 IP), chacun envoie un commentaire, attend la réponse,
pause 0,25 s, recommence. 400 commentaires du jeu de test (200 toxiques, 200 non toxiques,
tirage fixe, graine 42).

Ce qui est simulé fidèlement : l'IP client (en-tête X-Forwarded-For, lu par l'API comme derrière
le load balancer Google), la file d'inférence, le délestage, le plafond par IP, la limite de
corps. Ce qui ne l'est PAS : Cloud Armor (load balancer), qui en production bannit en plus toute
IP au-delà de 200 requêtes/min ; l'autoscaling. Le banc mesure donc la couche applicative seule,
c'est-à-dire le cas défavorable d'une attaque qui passe le load balancer.

Attaquants et clients légitimes tournent dans des processus séparés, pour que la saturation du
client d'attaque ne fausse pas la latence mesurée côté légitime.
"""

import argparse
import asyncio
import csv
import json
import math
import multiprocessing as mp
import os
import random
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "livrable_tp2"
DATA_OUT = OUT_DIR / "donnees"
FIG_OUT = OUT_DIR / "figures"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Protocole (identique pour les deux versions) -------------------------------------------
SEED = 42
N_PER_CLASS = 200
LEGIT_USERS = 4
LEGIT_THINK_S = 0.25
WARMUP_S = 3.0          # l'attaque démarre, les clients légitimes arrivent 3 s après
MEASURE_S = 40.0        # durée de mesure côté légitime
DRAIN_S = 25.0          # pause entre scénarios : la version « avant » garde des requêtes
                        # en file jusqu'à 20 s après la fin de l'attaque
TIMEOUT_S = 30.0
ATTACK_TEXT = ("This edit removed a lot of useful context about the history of the topic, " * 20)[:1400]
LB_IP = "130.211.0.1"   # dernière entrée de X-Forwarded-For : le load balancer Google
SCENARIOS = {
    "repos": {"attack_ips": 0, "per_ip": 0},
    "flood_1ip": {"attack_ips": 1, "per_ip": 100},
    "flood_distribue": {"attack_ips": 25, "per_ip": 4},
}
SERVER_ENV = {
    "AUTH_DISABLED": "1",          # clé identique pour les deux versions : on mesure le reste
    "METRICS_PORT": "0",
    "PRELOAD_MODELS": "bert",
    "TORCH_NUM_THREADS": "2",      # 2 vCPU comme un pod de production
    "CUDA_VISIBLE_DEVICES": "",    # CPU uniquement, comme en production
    "HF_HUB_OFFLINE": "1",
}


# --- Données ----------------------------------------------------------------------------------
def load_samples() -> list[dict]:
    """400 commentaires étiquetés du jeu de test (200 toxiques / 200 non toxiques), tirage fixe.
    Seuls les textes acceptés par les DEUX versions sont gardés (<= 1500 caractères, corps < 4 Ko),
    pour que les deux API reçoivent exactement les mêmes requêtes."""
    csv.field_size_limit(10**7)
    with open(ROOT / "data" / "test_labels.csv", encoding="utf-8") as f:
        labels = {r["id"]: int(r["toxic"]) for r in csv.DictReader(f) if r["toxic"] in ("0", "1")}
    pools: dict[int, list[dict]] = {0: [], 1: []}
    with open(ROOT / "data" / "test.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            label = labels.get(r["id"])
            text = r["comment_text"].strip()
            if label is None or not text or len(text) > 1500:
                continue
            if len(json.dumps({"text": text}, ensure_ascii=False).encode()) > 4000:
                continue
            pools[label].append({"text": text, "label": label})
    rng = random.Random(SEED)
    samples = rng.sample(pools[1], N_PER_CLASS) + rng.sample(pools[0], N_PER_CLASS)
    rng.shuffle(samples)
    return samples


# --- Préparation et lancement du serveur ------------------------------------------------------
def prepare_app(ref: str | None) -> tuple[Path, str, Path | None]:
    """Répertoire de l'API à lancer. ref=None : le dépôt tel quel. Sinon, extrait api.py et
    toxicity/ de ce commit dans un dossier temporaire, et y relie artifacts/ (modèles entraînés,
    hors git)."""
    commit = subprocess.run(["git", "rev-parse", "--short", ref or "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    if ref is None:
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "api.py", "toxicity"],
                               cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return ROOT, commit + ("+modifs" if dirty else ""), None
    tmp = Path(tempfile.mkdtemp(prefix=f"bench-{commit}-"))
    archive = subprocess.run(["git", "archive", "--format=tar", ref, "api.py", "toxicity"],
                             cwd=ROOT, capture_output=True, check=True).stdout
    archive_path = tmp / "src.tar"
    archive_path.write_bytes(archive)
    with tarfile.open(archive_path) as t:
        t.extractall(tmp, filter="data")
    archive_path.unlink()
    link = tmp / "artifacts"
    if os.name == "nt":  # jonction : pas de droit administrateur nécessaire, contrairement au lien symbolique
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(ROOT / "artifacts")],
                       check=True, capture_output=True)
    else:
        link.symlink_to(ROOT / "artifacts", target_is_directory=True)
    return tmp, commit, tmp


def cleanup(tmp: Path) -> None:
    """Supprime le dossier temporaire. Le lien vers artifacts/ est retiré SEUL d'abord : un
    nettoyage récursif ne doit jamais descendre dans les vrais poids des modèles."""
    link = tmp / "artifacts"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)  # sans /s : le lien seul
    elif link.is_symlink():
        link.unlink()
    if link.exists():
        print(f"[attention] lien {link} non supprimé : dossier temporaire conservé")
        return
    shutil.rmtree(tmp, ignore_errors=True)


def start_server(app_dir: Path, port: int, log_path: Path) -> subprocess.Popen:
    env = {**os.environ, **SERVER_ENV}
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app", "--app-dir", str(app_dir), "--port", str(port),
         "--log-level", "warning", "--no-access-log"],
        cwd=app_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
    import httpx
    deadline = time.time() + 240
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"Le serveur s'est arrêté au démarrage, voir {log_path}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(1)
    proc.kill()
    raise SystemExit(f"Serveur non prêt après 240 s, voir {log_path}")


# --- Processus attaquant et processus légitime ------------------------------------------------
def _xff(ip: str) -> dict:
    return {"X-Forwarded-For": f"{ip}, {LB_IP}", "Content-Type": "application/json"}


def _event_loop_policy() -> None:
    if sys.platform == "win32":  # même raison que dos.py : bruit du Proactor sous forte concurrence
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def attacker_process(base_url: str, ips: list[str], per_ip: int, start_at: float, stop_at: float,
                     queue: mp.Queue) -> None:
    _event_loop_policy()
    queue.put(asyncio.run(_attack(base_url, ips, per_ip, start_at, stop_at)))


async def _attack(base_url: str, ips: list[str], per_ip: int, start_at: float, stop_at: float) -> dict:
    import httpx
    body = json.dumps({"text": ATTACK_TEXT}).encode()
    codes: dict[str, int] = {}
    latencies: list[float] = []
    n = len(ips) * per_ip
    limits = httpx.Limits(max_connections=n + 10, max_keepalive_connections=n + 10)
    async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT_S, limits=limits) as client:
        async def worker(ip: str) -> None:
            headers = _xff(ip)
            while time.time() < stop_at:
                t = time.perf_counter()
                try:
                    code = (await client.post("/model", content=body, headers=headers)).status_code
                except (httpx.HTTPError, OSError):
                    code = 0
                codes[str(code)] = codes.get(str(code), 0) + 1
                if len(latencies) < 200_000:
                    latencies.append((time.perf_counter() - t) * 1000)

        await asyncio.sleep(max(0.0, start_at - time.time()))
        tasks = [asyncio.create_task(worker(ip)) for ip in ips for _ in range(per_ip)]
        # Fin de l'attaque : les requêtes encore en vol sont abandonnées (client déconnecté).
        await asyncio.wait(tasks, timeout=max(0.0, stop_at - time.time()) + 1)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {"codes": codes, "latencies_ms": latencies}


def legit_process(base_url: str, samples: list[dict], start_at: float, stop_at: float,
                  queue: mp.Queue) -> None:
    _event_loop_policy()
    queue.put(asyncio.run(_legit(base_url, samples, start_at, stop_at)))


async def _legit(base_url: str, samples: list[dict], start_at: float, stop_at: float) -> list[dict]:
    import httpx
    records: list[dict] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT_S) as client:
        async def user(k: int) -> None:
            headers = _xff(f"10.1.0.{k + 1}")
            i = k
            while time.time() < stop_at:
                idx = i % len(samples)
                s = samples[idx]
                i += LEGIT_USERS
                body = json.dumps({"text": s["text"]}, ensure_ascii=False).encode()
                t_send = time.time()
                t = time.perf_counter()
                pred = None
                try:
                    r = await client.post("/model", content=body, headers=headers)
                    code = r.status_code
                    if code == 200:
                        pred = int(bool(r.json()["is_toxic"]))
                except (httpx.HTTPError, OSError):
                    code = 0
                records.append({"t": round(t_send - start_at, 3), "status": code,
                                "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                                "sample": idx, "label": s["label"], "pred": pred})
                await asyncio.sleep(LEGIT_THINK_S)

        await asyncio.sleep(max(0.0, start_at - time.time()))
        await asyncio.gather(*(user(k) for k in range(LEGIT_USERS)))
    return records


def run_scenario(base_url: str, samples: list[dict], attack_ips: int, per_ip: int) -> dict:
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    now = time.time() + 4  # temps de démarrage des processus (spawn)
    attack_start, legit_start = now, now + WARMUP_S
    legit_stop = legit_start + MEASURE_S
    attack_stop = legit_stop + 1
    ips = [f"203.0.113.{i + 1}" for i in range(attack_ips)]
    # Attaquants répartis sur 2 processus (un seul client Python plafonne vers quelques
    # centaines de requêtes/s et deviendrait le goulot d'étranglement).
    groups = [ips] if len(ips) <= 1 else [ips[0::2], ips[1::2]]
    per_proc = [per_ip // 2, per_ip - per_ip // 2] if len(ips) == 1 else [per_ip, per_ip]
    procs = []
    if attack_ips:
        for g, p in zip(groups, per_proc):
            procs.append(ctx.Process(target=attacker_process,
                                     args=(base_url, g, p, attack_start, attack_stop, queue)))
    procs.append(ctx.Process(target=legit_process, args=(base_url, samples, legit_start, legit_stop, queue)))
    for p in procs:
        p.start()
    results = [queue.get() for _ in procs]
    for p in procs:
        p.join()
    legit = next(r for r in results if isinstance(r, list))
    attacks = [r for r in results if isinstance(r, dict)]
    codes: dict[str, int] = {}
    lat: list[float] = []
    for a in attacks:
        for c, n in a["codes"].items():
            codes[c] = codes.get(c, 0) + n
        lat += a["latencies_ms"]
    duration = attack_stop - attack_start
    return {"legit_records": legit,
            "attacker": {"requests": sum(codes.values()), "codes": codes,
                         "rps": round(sum(codes.values()) / duration, 1) if attack_ips else 0,
                         "reached_model_pct": round(100 * codes.get("200", 0) / max(1, sum(codes.values())), 1),
                         "latency_p50_ms": round(percentile(lat, 50), 1) if lat else None}}


# --- Indicateurs ------------------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), min(math.floor(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize_legit(records: list[dict]) -> dict:
    served = [r for r in records if r["status"] == 200]
    tp = sum(1 for r in served if r["pred"] == 1 and r["label"] == 1)
    fp = sum(1 for r in served if r["pred"] == 1 and r["label"] == 0)
    tn = sum(1 for r in served if r["pred"] == 0 and r["label"] == 0)
    fn = sum(1 for r in served if r["pred"] == 0 and r["label"] == 1)
    toxic_sent = sum(1 for r in records if r["label"] == 1)
    codes: dict[str, int] = {}
    for r in records:
        codes[str(r["status"])] = codes.get(str(r["status"]), 0) + 1
    lat_served = [r["latency_ms"] for r in served]
    lat_all = [r["latency_ms"] for r in records]

    def ratio(a: int, b: int) -> float | None:
        return round(100 * a / b, 1) if b else None

    return {
        "requests": len(records), "served": len(served), "codes": codes,
        # Capacité de modération : commentaires légitimes effectivement analysés par seconde.
        "served_per_s": round(len(served) / MEASURE_S, 1),
        "availability_pct": ratio(len(served), len(records)),
        "latency_served_p50_ms": round(percentile(lat_served, 50), 1) if lat_served else None,
        "latency_served_p95_ms": round(percentile(lat_served, 95), 1) if lat_served else None,
        "latency_all_p95_ms": round(percentile(lat_all, 95), 1) if lat_all else None,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "precision_pct": ratio(tp, tp + fp),
        "false_positive_rate_pct": ratio(fp, fp + tn),
        "recall_pct": ratio(tp, tp + fn),
        "accuracy_pct": ratio(tp + tn, len(served)),
        # Modération « fail-open » : une requête refusée n'est pas filtrée, le texte toxique passe.
        "toxic_effectively_flagged_pct": ratio(tp, toxic_sent),
        # En valeur absolue : commentaires toxiques correctement signalés par minute.
        "toxic_flagged_per_min": round(tp / MEASURE_S * 60, 1),
    }


# --- Commandes --------------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> None:
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    samples = load_samples()
    app_dir, commit, tmp = prepare_app(args.ref)
    log_path = Path(tempfile.gettempdir()) / f"bench-server-{args.label}.log"
    print(f"[{args.label}] API du commit {commit} ({app_dir}), journal serveur : {log_path}")
    server = start_server(app_dir, args.port, log_path)
    base_url = f"http://127.0.0.1:{args.port}"
    out = {"label": args.label, "commit": commit, "ref": args.ref or "HEAD",
           "date": time.strftime("%Y-%m-%d %H:%M"),
           "protocol": {"samples": len(samples), "seed": SEED, "legit_users": LEGIT_USERS,
                        "legit_think_s": LEGIT_THINK_S, "measure_s": MEASURE_S, "warmup_s": WARMUP_S,
                        "timeout_s": TIMEOUT_S, "server_env": SERVER_ENV, "scenarios": SCENARIOS,
                        "decision": "is_toxic renvoyé par l'API (score de la classe toxic > 70)"},
           "scenarios": {}}
    try:
        for i, (name, sc) in enumerate(SCENARIOS.items()):
            if i:
                time.sleep(DRAIN_S)
            print(f"[{args.label}] scénario {name} ...", flush=True)
            res = run_scenario(base_url, samples, sc["attack_ips"], sc["per_ip"])
            res["legit"] = summarize_legit(res["legit_records"])
            out["scenarios"][name] = res
            lg, at = res["legit"], res["attacker"]
            print(f"   légitimes : {lg['served']}/{lg['requests']} servies ({lg['availability_pct']} %), "
                  f"p50 {lg['latency_served_p50_ms']} ms, p95 {lg['latency_served_p95_ms']} ms, "
                  f"précision {lg['precision_pct']} %, FPR {lg['false_positive_rate_pct']} %")
            if sc["attack_ips"]:
                print(f"   attaquant : {at['requests']} requêtes ({at['rps']} req/s), codes {at['codes']}")
        # Le modèle est déterministe : sous attaque, un même texte doit recevoir la même décision
        # qu'au repos. Les écarts de précision entre scénarios ne viennent alors que de l'ensemble
        # des textes qui ont eu le temps d'être analysés.
        ref = {r["sample"]: r["pred"] for r in out["scenarios"]["repos"]["legit_records"] if r["status"] == 200}
        for res in out["scenarios"].values():
            pairs = [(r["pred"], ref[r["sample"]]) for r in res["legit_records"]
                     if r["status"] == 200 and r["sample"] in ref]
            res["legit"]["same_prediction_as_repos_pct"] = (
                round(100 * sum(a == b for a, b in pairs) / len(pairs), 1) if pairs else None)
    finally:
        server.kill()
        server.wait()
        if tmp:
            cleanup(tmp)
    path = DATA_OUT / f"bench-{args.label}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Résultats : {path.relative_to(ROOT)}")


# --- Rapport : tableau Markdown + graphiques SVG (sans dépendance) ----------------------------
COLORS = {"avant": "#c0392b", "apres": "#1e7b4f"}
NAMES = {"avant": "Avant (version attaquée, vague 6)", "apres": "Après défenses"}
SC_NAMES = {"repos": "Sans attaque", "flood_1ip": "Flood 1 IP", "flood_distribue": "Flood 25 IP"}


def svg_bars(title: str, unit: str, groups: list[str], series: dict[str, list[float | None]],
             log: bool = False, ymax: float | None = None, note: str = "") -> str:
    """Barres groupées : un groupe par scénario, une barre par version."""
    W, H, L, R, T, B = 760, 380, 70, 20, 56, 70
    pw, ph = W - L - R, H - T - B
    vals = [v for s in series.values() for v in s if v is not None]
    raw = ymax or (max(vals) * 1.15 if vals else 1)
    # Échelle à graduations rondes (1, 2, 2,5 ou 5 x 10^k) : 5 intervalles au plus.
    mag = 10 ** math.floor(math.log10(raw / 5))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag * 5 >= raw)
    top = step * math.ceil(raw / step)
    lo = 10 if log else 0
    if log:
        top = 10 ** math.ceil(math.log10(max(vals + [100])))

    def y(v: float) -> float:
        if log:
            v = max(v, lo)
            return T + ph - ph * (math.log10(v) - math.log10(lo)) / (math.log10(top) - math.log10(lo))
        return T + ph - ph * v / top

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{W / 2}" y="24" text-anchor="middle" font-size="16" font-weight="bold" fill="#222">{title}</text>']
    if note:
        out.append(f'<text x="{W / 2}" y="42" text-anchor="middle" fill="#666">{note}</text>')
    ticks = [10 ** k for k in range(int(math.log10(lo)), int(math.log10(top)) + 1)] if log else \
        [step * k for k in range(round(top / step) + 1)]
    for t in ticks:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="#e3e3e3"/>')
        label = f"{t:,.0f}".replace(",", " ")
        out.append(f'<text x="{L - 8}" y="{y(t) + 4:.1f}" text-anchor="end" fill="#555">{label}</text>')
    out.append(f'<text x="16" y="{T + ph / 2}" transform="rotate(-90 16 {T + ph / 2})" text-anchor="middle" fill="#555">{unit}</text>')
    gw = pw / len(groups)
    keys = list(series)
    bw = min(70, gw * 0.7 / len(keys))
    for gi, g in enumerate(groups):
        gx = L + gi * gw + (gw - bw * len(keys)) / 2
        for ki, k in enumerate(keys):
            v = series[k][gi]
            x = gx + ki * bw
            if v is None:
                out.append(f'<text x="{x + bw / 2:.1f}" y="{T + ph - 6}" text-anchor="middle" fill="#999">n/a</text>')
                continue
            yy = y(v)
            out.append(f'<rect x="{x + 2:.1f}" y="{yy:.1f}" width="{bw - 4:.1f}" height="{T + ph - yy:.1f}" fill="{COLORS[k]}"/>')
            txt = f"{v:,.0f}".replace(",", " ") if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".")
            out.append(f'<text x="{x + bw / 2:.1f}" y="{yy - 5:.1f}" text-anchor="middle" fill="#222">{txt}</text>')
        out.append(f'<text x="{L + gi * gw + gw / 2:.1f}" y="{T + ph + 20}" text-anchor="middle" fill="#222" font-weight="bold">{g}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}" stroke="#888"/>')
    lx = L
    for k in keys:
        out.append(f'<rect x="{lx}" y="{H - 24}" width="14" height="14" fill="{COLORS[k]}"/>')
        out.append(f'<text x="{lx + 20}" y="{H - 12}" fill="#222">{NAMES[k]}</text>')
        lx += 290
    out.append("</svg>")
    return "\n".join(out)


def svg_timeline(title: str, runs: dict[str, list[dict]], note: str) -> str:
    """Latence de chaque requête légitime au cours du temps (refus en croix sur l'axe bas)."""
    W, H, L, R, T, B = 760, 380, 70, 20, 56, 70
    pw, ph = W - L - R, H - T - B
    lo, top = 10, 100_000
    xmax = MEASURE_S

    def y(v: float) -> float:
        v = min(max(v, lo), top)
        return T + ph - ph * (math.log10(v) - 1) / 4

    def x(t: float) -> float:
        return L + pw * min(max(t, 0), xmax) / xmax

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{W / 2}" y="24" text-anchor="middle" font-size="16" font-weight="bold" fill="#222">{title}</text>',
           f'<text x="{W / 2}" y="42" text-anchor="middle" fill="#666">{note}</text>']
    for t in (10, 100, 1000, 10_000, 100_000):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="#e3e3e3"/>')
        out.append(f'<text x="{L - 8}" y="{y(t) + 4:.1f}" text-anchor="end" fill="#555">{t:,}</text>'.replace(",", " "))
    for s in range(0, int(xmax) + 1, 10):
        out.append(f'<text x="{x(s):.1f}" y="{T + ph + 18}" text-anchor="middle" fill="#555">{s} s</text>')
    out.append(f'<text x="16" y="{T + ph / 2}" transform="rotate(-90 16 {T + ph / 2})" text-anchor="middle" fill="#555">latence (ms, échelle log)</text>')
    for k, recs in runs.items():
        for r in recs:
            cx, c = x(r["t"]), COLORS[k]
            if r["status"] == 200:
                out.append(f'<circle cx="{cx:.1f}" cy="{y(r["latency_ms"]):.1f}" r="2.2" fill="{c}" fill-opacity="0.6"/>')
            else:  # requête refusée ou en échec : croix, à la latence du refus
                cy = y(r["latency_ms"])
                out.append(f'<path d="M{cx - 3:.1f},{cy - 3:.1f} L{cx + 3:.1f},{cy + 3:.1f} M{cx - 3:.1f},{cy + 3:.1f} L{cx + 3:.1f},{cy - 3:.1f}" stroke="{c}" stroke-width="1.2"/>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}" stroke="#888"/>')
    lx = L
    for k in runs:
        out.append(f'<circle cx="{lx + 7}" cy="{H - 17}" r="5" fill="{COLORS[k]}"/>')
        out.append(f'<text x="{lx + 18}" y="{H - 12}" fill="#222">{NAMES[k]}</text>')
        lx += 290
    out.append(f'<text x="{W - R}" y="{H - 12}" text-anchor="end" fill="#666">rond : servie, croix : refusée</text>')
    out.append("</svg>")
    return "\n".join(out)


def fmt(v: float | None, unit: str = "") -> str:
    if v is None:
        return "n/a"
    s = f"{v:,.0f}".replace(",", " ") if abs(v) >= 1000 else (f"{v:.1f}".rstrip("0").rstrip("."))
    return s + unit


def cmd_report(_: argparse.Namespace) -> None:
    runs = {k: json.loads((DATA_OUT / f"bench-{k}.json").read_text(encoding="utf-8")) for k in ("avant", "apres")}
    # Indicateurs recalculés depuis les requêtes brutes : un indicateur ajouté au script
    # s'applique aux résultats existants sans relancer les mesures.
    for run in runs.values():
        for res in run["scenarios"].values():
            res["legit"] = {**res["legit"], **summarize_legit(res["legit_records"])}
    FIG_OUT.mkdir(parents=True, exist_ok=True)
    scs = list(SCENARIOS)
    groups = [SC_NAMES[s] for s in scs]

    def series(key: str) -> dict[str, list]:
        return {k: [runs[k]["scenarios"][s]["legit"][key] for s in scs] for k in runs}

    figs = {
        "bench_latence_p95.svg": svg_bars("Latence p95 des clients légitimes", "ms (échelle log)", groups,
                                          series("latency_all_p95_ms"), log=True,
                                          note="Temps jusqu'à la réponse, requêtes refusées comprises"),
        "bench_disponibilite.svg": svg_bars("Requêtes légitimes servies", "% de requêtes en 200", groups,
                                            series("availability_pct"), ymax=100),
        "bench_debit_moderation.svg": svg_bars("Commentaires légitimes analysés par seconde", "commentaires / s",
                                               groups, series("served_per_s"),
                                               note="4 utilisateurs légitimes, chacun attend sa réponse avant d'envoyer le suivant"),
        "bench_precision.svg": svg_bars("Précision du modèle sur les requêtes servies", "%", groups,
                                        series("precision_pct"), ymax=100,
                                        note="Décisions identiques à 100 % sous attaque : les écarts viennent des textes servis"),
        "bench_faux_positifs.svg": svg_bars("Taux de faux positifs sur les requêtes servies", "%", groups,
                                            series("false_positive_rate_pct"),
                                            ymax=max(30, *(v or 0 for s in series("false_positive_rate_pct").values() for v in s)) * 1.2,
                                            note="Décisions identiques à 100 % sous attaque : les écarts viennent des textes servis"),
        "bench_toxiques_signales.svg": svg_bars("Commentaires toxiques correctement signalés par minute",
                                                "commentaires / min", groups, series("toxic_flagged_per_min"),
                                                note="Capacité de modération utile : ce que l'attaque fait réellement perdre"),
        "bench_chronologie_flood_1ip.svg": svg_timeline(
            "Flood 1 IP : chaque requête légitime au cours du temps",
            {k: runs[k]["scenarios"]["flood_1ip"]["legit_records"] for k in runs},
            "Attaque de 100 requêtes simultanées pendant toute la mesure"),
    }
    for name, svg in figs.items():
        (FIG_OUT / name).write_text(svg, encoding="utf-8")

    rows = [
        ("Requêtes légitimes servies (200)", "availability_pct", " %"),
        ("Commentaires légitimes analysés par seconde", "served_per_s", ""),
        ("Latence p50 des requêtes servies", "latency_served_p50_ms", " ms"),
        ("Latence p95 des requêtes servies", "latency_served_p95_ms", " ms"),
        ("Latence p95, refus compris", "latency_all_p95_ms", " ms"),
        ("Précision (requêtes servies)", "precision_pct", " %"),
        ("Taux de faux positifs (requêtes servies)", "false_positive_rate_pct", " %"),
        ("Rappel (requêtes servies)", "recall_pct", " %"),
        ("Toxiques effectivement signalés (sur tous les toxiques envoyés)", "toxic_effectively_flagged_pct", " %"),
        ("Toxiques correctement signalés par minute", "toxic_flagged_per_min", ""),
        ("Décisions identiques à celles sans attaque (mêmes textes)", "same_prediction_as_repos_pct", " %"),
    ]
    lines = [f"Généré par `uv run python attack/bench_defense.py report` à partir de "
             f"`bench-avant.json` (commit {runs['avant']['commit']}) et `bench-apres.json` "
             f"(commit {runs['apres']['commit']}).", ""]
    for s in scs:
        a, b = runs["avant"]["scenarios"][s], runs["apres"]["scenarios"][s]
        lines += [f"### {SC_NAMES[s]}", "", "| Indicateur (clients légitimes) | Avant | Après |", "|---|---|---|"]
        for label, key, unit in rows:
            lines.append(f"| {label} | {fmt(a['legit'][key], unit)} | {fmt(b['legit'][key], unit)} |")
        lines.append(f"| Requêtes légitimes envoyées en {MEASURE_S:.0f} s | {a['legit']['requests']} | {b['legit']['requests']} |")
        lines.append(f"| Codes HTTP légitimes | {a['legit']['codes']} | {b['legit']['codes']} |")
        if SCENARIOS[s]["attack_ips"]:
            lines.append(f"| Attaquant : requêtes envoyées | {a['attacker']['requests']} | {b['attacker']['requests']} |")
            lines.append(f"| Attaquant : part atteignant le modèle (200) | {fmt(a['attacker']['reached_model_pct'], ' %')} | {fmt(b['attacker']['reached_model_pct'], ' %')} |")
            lines.append(f"| Attaquant : codes HTTP | {a['attacker']['codes']} | {b['attacker']['codes']} |")
        lines.append("")
    (DATA_OUT / "tableau_avant_apres.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Graphiques : {FIG_OUT.relative_to(ROOT)}/  Tableau : {(DATA_OUT / 'tableau_avant_apres.md').relative_to(ROOT)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="lance une version de l'API et tous les scénarios")
    r.add_argument("--ref", default=None, help="commit git de l'API à tester (défaut : dépôt actuel)")
    r.add_argument("--label", required=True, choices=["avant", "apres"])
    r.add_argument("--port", type=int, default=8790)
    sub.add_parser("report", help="tableau Markdown et graphiques SVG à partir des deux runs")
    args = p.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
