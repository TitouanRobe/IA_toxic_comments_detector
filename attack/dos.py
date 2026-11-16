"""TP2 - Attaque de déni de service applicatif (DoS L7) contre l'API Digital Social Score.

Exercice pédagogique AUTORISÉ : chaque groupe attaque le déploiement d'un autre groupe
du même cours, pour mesurer la résistance de l'API puis concevoir une défense (étape 3).
Ce script est un générateur de charge BORNÉ (durée limitée, concurrence réglable, auto-stop,
une seule cible). Ce n'est pas une arme : il mesure, il ne cherche pas à détruire.

Principe
--------
On sature l'API avec des requêtes concurrentes pendant une durée fixe et on mesure la
dégradation : latence (p50/p95/p99), taux d'erreur, disponibilité et débit, seconde par seconde.
Une mesure "à vide" (baseline) est prise AVANT la charge pour chiffrer la dégradation.

Endpoint, méthode et corps sont LIBRES
---------------------------------------
Chaque groupe de la classe n'expose pas forcément les mêmes routes que ce projet
(/model en POST avec {"text","model"}). --endpoint/--method/--body permettent de viser
n'importe quelle API : --endpoint /predict --method GET, ou --endpoint /score --method POST
--body '{"comment": "..."}', etc. Par défaut (sans --body), POST/PUT/PATCH envoient
{"text": ..., "model": ...} (le format de CE projet) — à remplacer par --body si le groupe
cible attend autre chose.

Important - effet de la clé d'API
---------------------------------
Si l'endpoint visé exige une authentification (souvent via un en-tête X-API-Key, comme dans
ce projet), SANS clé valide l'API répond généralement 401/403 immédiatement et ne fait aucun
travail coûteux : on ne stresse alors que la couche web/auth (bon marché), pas le modèle. Pour
réellement le charger, il faut une clé de test fournie par le groupe cible. Le script fonctionne
dans les deux cas et indique clairement quelle couche a été sollicitée (répartition des codes HTTP).

Usage (depuis la racine du projet)
----------------------------------
  # Contre CE projet, sans clé : on mesure la résistance du front (401 sur /model, ou /health)
  uv run python attack/dos.py http://CIBLE:8000 --endpoint /health --duration 30 --concurrency 50

  # Contre CE projet, avec clé de test : on charge réellement le modèle
  uv run python attack/dos.py http://CIBLE:8000 --api-key dss_xxx --duration 30 --concurrency 100

  # Contre un autre groupe, endpoint/méthode/format différents
  uv run python attack/dos.py http://CIBLE:5000 --endpoint /api/predict --method POST \\
      --body '{"comment": "some text"}' --duration 30 --concurrency 50

  uv run python attack/dos.py --help
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

# Console Windows en cp1252 par défaut : force l'UTF-8 pour les accents du récapitulatif.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Bug connu asyncio sur Windows : l'event loop par défaut ("Proactor") lève une exception interne
# inoffensive mais bruyante (_ProactorBasePipeTransport._call_connection_lost) quand beaucoup de
# connexions se ferment d'un coup - exactement notre cas avec une forte concurrence. L'event loop
# "Selector" n'a pas ce défaut pour du simple HTTP (elle ne gère pas les sous-processus, inutilisés ici).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

# Garde-fous : un TP n'est pas une attaque réelle. Bornes dures non contournables en argument.
MAX_DURATION_S = 500        # 5 min max par tir
MAX_CONCURRENCY = 50000       # au-delà, c'est la machine de l'attaquant qui sature, pas la cible
DEFAULT_TEXT = "Thanks for your help with the article, much appreciated."


def percentile(values: list[float], p: float) -> float | None:
    """Percentile (0-100) d'une liste de latences, en millisecondes. Liste vide -> None
    (pas de réponse réussie : la latence n'est pas mesurable, ce n'est pas "0 ms")."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


async def one_request(client: httpx.AsyncClient, method: str, path: str,
                      payload: dict | None, headers: dict) -> tuple[float, int]:
    """Envoie une requête (méthode libre : GET/POST/PUT/PATCH/DELETE...), renvoie
    (latence_ms, code_http). code 0 = erreur réseau/timeout."""
    start = time.perf_counter()
    try:
        r = await client.request(method, path, json=payload, headers=headers)
        return (time.perf_counter() - start) * 1000, r.status_code
    except (httpx.HTTPError, OSError):
        return (time.perf_counter() - start) * 1000, 0


async def baseline(client: httpx.AsyncClient, method: str, path: str,
                   payload: dict | None, headers: dict, n: int = 5) -> dict:
    """Latence de référence, API au repos : n requêtes séquentielles avant la charge."""
    lat, codes = [], []
    for _ in range(n):
        ms, code = await one_request(client, method, path, payload, headers)
        lat.append(ms)
        codes.append(code)
        await asyncio.sleep(0.3)
    ok = [l for l, c in zip(lat, codes) if 200 <= c < 400]
    return {"requests": n, "codes": codes,
            "latency_ms_mean": round(statistics.mean(ok), 1) if ok else None,
            "latency_ms_min": round(min(ok), 1) if ok else None}


async def worker(client: httpx.AsyncClient, method: str, path: str, payload: dict | None,
                 headers: dict, deadline: float, records: list) -> None:
    """Boucle d'un attaquant virtuel : envoie des requêtes en continu jusqu'à l'échéance."""
    t0 = time.perf_counter()
    while time.perf_counter() < deadline:
        sent_at = time.perf_counter() - t0
        ms, code = await one_request(client, method, path, payload, headers)
        records.append((sent_at, ms, code))


async def ramp_up(target_workers: int, ramp_s: float, spawn):
    """Fait monter la concurrence progressivement sur ramp_s secondes (0 = tout d'un coup)."""
    if ramp_s <= 0:
        for _ in range(target_workers):
            spawn()
        return
    delay = ramp_s / target_workers
    for _ in range(target_workers):
        spawn()
        await asyncio.sleep(delay)


def build_payload(args) -> dict | None:
    """Corps JSON de la requête. --body prime (JSON brut, pour un endpoint au format propre à
    chaque groupe) ; sinon, pour une méthode avec corps, retombe sur {text, model} (format de
    ce projet) ; sinon aucun corps (GET/HEAD/DELETE)."""
    if args.body is not None:
        try:
            return json.loads(args.body)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--body n'est pas du JSON valide : {e}") from e
    if args.method in ("POST", "PUT", "PATCH"):
        return {"text": args.text, "model": args.model}
    return None


async def run_attack(args) -> dict:
    method = args.method
    payload = build_payload(args)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}

    limits = httpx.Limits(max_connections=args.concurrency + 10,
                          max_keepalive_connections=args.concurrency + 10)
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(base_url=args.target, limits=limits, timeout=timeout,
                                 verify=not args.insecure) as client:
        print(f"[baseline] mesure au repos ({method} {args.endpoint})...", flush=True)
        base = await baseline(client, method, args.endpoint, payload, headers)
        print(f"[baseline] latence moyenne au repos : {base['latency_ms_mean']} ms "
              f"(codes {base['codes']})\n", flush=True)
        codes = base["codes"]
        if all(c == 0 for c in codes):
            # Code 0 = exception réseau (pas de réponse HTTP du tout) : la cible est injoignable.
            print(f"[ATTENTION] les {base['requests']} requêtes de baseline (SANS charge) ont "
                  "toutes échoué AU NIVEAU RÉSEAU (aucune réponse HTTP) : la cible semble "
                  "injoignable depuis cette machine (VPN non connecté ? mauvais port ? pare-feu/"
                  f"Cloud Armor ? service arrêté ?). Le tir va continuer {args.duration:.0f}s "
                  "comme demandé mais ne produira probablement aucune latence mesurable. Ctrl+C "
                  f"pour annuler et vérifier la connectivité (ex: curl {args.target}{args.endpoint}).\n",
                  flush=True)
        elif all(not (200 <= c < 400) for c in codes):
            # La cible RÉPOND (donc elle est joignable) mais toujours en erreur : c'est la requête
            # elle-même qui est mal formée pour cet endpoint (mauvaise méthode/route/clé/corps).
            hint = {405: "méthode HTTP refusée par cette route -> essayer --method GET ou --method POST",
                    404: "route introuvable -> vérifier --endpoint (ex: lire /openapi.json de la cible)",
                    401: "authentification requise -> fournir --api-key",
                    403: "accès refusé -> clé invalide, ou IP bloquée (pare-feu/Cloud Armor)",
                    422: "corps de requête rejeté -> vérifier --body / le schéma attendu par la cible"}
            msg = hint.get(codes[0], "vérifier --endpoint/--method/--body pour cette cible")
            print(f"[info] la cible RÉPOND (codes {codes}) mais toujours en erreur sur cette "
                  f"requête précise -> {msg}. Le tir va quand même continuer {args.duration:.0f}s, "
                  "mais mesurera cette erreur en boucle plutôt qu'une vraie charge sur le modèle.\n",
                  flush=True)

        records: list = []
        deadline = time.perf_counter() + args.duration
        tasks: list[asyncio.Task] = []

        def spawn():
            tasks.append(asyncio.create_task(
                worker(client, method, args.endpoint, payload, headers, deadline, records)))

        print(f"[charge] {args.concurrency} attaquants virtuels, {args.duration}s, "
              f"montée {args.ramp}s -> {method} {args.target}{args.endpoint}", flush=True)
        start_wall = time.perf_counter()
        await ramp_up(args.concurrency, args.ramp, spawn)
        # Attend la fin de la fenêtre de tir puis que tous les workers terminent leur requête en cours.
        await asyncio.sleep(max(0, deadline - time.perf_counter()))
        await asyncio.gather(*tasks)
        wall = time.perf_counter() - start_wall

    return summarize(records, base, wall, args)


def summarize(records: list, base: dict, wall: float, args) -> dict:
    """Agrège les requêtes en métriques globales + série temporelle (par seconde)."""
    total = len(records)
    lat_ok = [ms for _, ms, c in records if 200 <= c < 400]
    codes: dict[str, int] = {}
    for _, _, c in records:
        key = str(c) if c else "reseau/timeout"
        codes[key] = codes.get(key, 0) + 1
    errors = sum(n for k, n in codes.items() if k == "reseau/timeout" or not k.startswith("2"))

    # Diagnostic : temps écoulé avant un échec (réseau, timeout, 4xx/5xx). Permet de distinguer
    # un refus rapide (port fermé / connexion refusée : quelques ms) d'un vrai timeout (la cible
    # ne répond jamais : proche de --timeout) — utile quand il n'y a aucune réponse réussie.
    fail_lat = [ms for _, ms, c in records if not (200 <= c < 400)]

    # Série temporelle : une ligne par seconde écoulée.
    buckets: dict[int, list] = {}
    for sent_at, ms, c in records:
        buckets.setdefault(int(sent_at), []).append((ms, c))
    timeseries = []
    for sec in sorted(buckets):
        rows = buckets[sec]
        ok = [ms for ms, c in rows if 200 <= c < 400]
        n_err = sum(1 for _, c in rows if not (200 <= c < 400))
        timeseries.append({
            "second": sec,
            "requests": len(rows),
            "rps": len(rows),
            "error_rate_pct": round(100 * n_err / len(rows), 1),
            "availability_pct": round(100 * (len(rows) - n_err) / len(rows), 1),
            "p50_ms": round(p, 1) if (p := percentile(ok, 50)) is not None else None,
            "p95_ms": round(p, 1) if (p := percentile(ok, 95)) is not None else None,
            "p99_ms": round(p, 1) if (p := percentile(ok, 99)) is not None else None,
        })

    return {
        "target": args.target,
        "endpoint": args.endpoint,
        "method": args.method,
        "authenticated": bool(args.api_key),
        "concurrency": args.concurrency,
        "duration_s": args.duration,
        "ramp_s": args.ramp,
        "baseline": base,
        "total_requests": total,
        "wall_time_s": round(wall, 1),
        "throughput_rps": round(total / wall, 1) if wall else 0,
        "error_count": errors,
        "error_rate_pct": round(100 * errors / total, 1) if total else 0,
        "availability_pct": round(100 * (total - errors) / total, 1) if total else 0,
        "http_codes": codes,
        "latency_ms": {
            "mean": round(statistics.mean(lat_ok), 1) if lat_ok else None,
            "p50": round(p, 1) if (p := percentile(lat_ok, 50)) is not None else None,
            "p95": round(p, 1) if (p := percentile(lat_ok, 95)) is not None else None,
            "p99": round(p, 1) if (p := percentile(lat_ok, 99)) is not None else None,
            "max": round(max(lat_ok), 1) if lat_ok else None,
        },
        # Diagnostic (pas une mesure de performance) : temps avant échec, si aucune/peu de
        # réponses ont réussi. Permet de distinguer refus rapide (port fermé) et vrai timeout.
        "failure_latency_ms": {
            "mean": round(statistics.mean(fail_lat), 1) if fail_lat else None,
            "max": round(max(fail_lat), 1) if fail_lat else None,
        },
        "timeseries": timeseries,
    }


def svg_line_chart(series: list[tuple[str, list[float | None], str]], xlabels: list,
                   title: str, ylabel: str, path: Path) -> None:
    """Graphe en lignes minimaliste, sans dépendance. series = [(nom, valeurs, couleur)].
    Une valeur None (pas de donnée cette seconde-là, ex. aucune réponse réussie) laisse un vrai
    trou dans la courbe plutôt qu'une fausse chute à 0."""
    def esc(s):  # échappe le texte pour un SVG/XML bien formé (&, <, >).
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    title, ylabel = esc(title), esc(ylabel)
    series = [(esc(name), vals, color) for name, vals, color in series]
    xlabels = [esc(x) for x in xlabels]
    W, H, pad = 760, 380, 60
    plot_w, plot_h = W - 2 * pad, H - 2 * pad
    all_vals = [v for _, vals, _ in series for v in vals if v is not None] or [0, 1]
    ymax = max(all_vals) * 1.1 or 1
    n = max(len(xlabels), 2)

    def x(i): return pad + plot_w * i / (n - 1)
    def y(v): return pad + plot_h * (1 - v / ymax)

    # viewBox (et pas seulement width/height) : sans lui, un CSS responsive qui redimensionne la
    # balise <svg> (ex. "width:100%" dans le dashboard) ne redimensionne PAS les coordonnées
    # dessinées à l'intérieur -> le graphique est tronqué au lieu d'être mis à l'échelle.
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'width="{W}" height="{H}" font-family="sans-serif" font-size="13">',
             f'<rect width="{W}" height="{H}" fill="white"/>',
             f'<text x="{W/2}" y="26" text-anchor="middle" font-size="16" font-weight="bold">{title}</text>']
    # Axes + grille horizontale (5 graduations).
    for g in range(6):
        gy = pad + plot_h * g / 5
        val = ymax * (1 - g / 5)
        parts.append(f'<line x1="{pad}" y1="{gy:.0f}" x2="{W-pad}" y2="{gy:.0f}" '
                     f'stroke="#eee"/><text x="{pad-8}" y="{gy+4:.0f}" text-anchor="end" '
                     f'fill="#666">{val:.0f}</text>')
    parts.append(f'<text x="18" y="{H/2}" text-anchor="middle" fill="#666" '
                 f'transform="rotate(-90 18 {H/2})">{ylabel}</text>')
    parts.append(f'<text x="{W/2}" y="{H-12}" text-anchor="middle" fill="#666">temps (s)</text>')
    # Graduations X (max ~10 labels).
    step = max(1, n // 10)
    for i in range(0, n, step):
        parts.append(f'<text x="{x(i):.0f}" y="{H-pad+18:.0f}" text-anchor="middle" '
                     f'fill="#666">{xlabels[i] if i < len(xlabels) else ""}</text>')
    # Courbes : un <polyline> par segment continu (coupé à chaque None -> vrai trou, pas de chute à 0).
    for name, vals, color in series:
        segment: list[str] = []
        for i, v in enumerate(vals):
            if v is None:
                if len(segment) > 1:
                    parts.append(f'<polyline points="{" ".join(segment)}" fill="none" '
                                 f'stroke="{color}" stroke-width="2"/>')
                segment = []
            else:
                segment.append(f"{x(i):.1f},{y(v):.1f}")
        if len(segment) > 1:
            parts.append(f'<polyline points="{" ".join(segment)}" fill="none" '
                         f'stroke="{color}" stroke-width="2"/>')
    # Légende.
    for j, (name, _, color) in enumerate(series):
        ly = pad + 6 + j * 20
        parts.append(f'<rect x="{W-pad-150}" y="{ly-10}" width="12" height="12" fill="{color}"/>'
                     f'<text x="{W-pad-132}" y="{ly}" fill="#333">{name}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_outputs(result: dict, prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    # JSON complet.
    (prefix.with_suffix(".json")).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    # CSV série temporelle.
    ts = result["timeseries"]
    if ts:
        cols = list(ts[0].keys())
        lines = [",".join(cols)] + [
            ",".join("" if row[c] is None else str(row[c]) for c in cols) for row in ts
        ]
        (prefix.parent / f"{prefix.name}_timeseries.csv").write_text(
            "\n".join(lines), encoding="utf-8")
        # Graphes SVG.
        xs = [str(r["second"]) for r in ts]
        svg_line_chart(
            [("débit (req/s)", [r["rps"] for r in ts], "#16a34a"),
             ("taux d'erreur (%)", [r["error_rate_pct"] for r in ts], "#dc2626")],
            xs, "Débit et taux d'erreur sous charge",
            "req/s  &  % erreurs", prefix.parent / f"{prefix.name}_throughput.svg")
        # Latence : seulement s'il y a eu au moins une réponse HTTP réussie (sinon rien à tracer -
        # un graphique à 0 ms laisserait croire, à tort, à une latence excellente).
        if any(r["p50_ms"] is not None for r in ts):
            svg_line_chart(
                [("p50", [r["p50_ms"] for r in ts], "#2563eb"),
                 ("p95", [r["p95_ms"] for r in ts], "#f59e0b"),
                 ("p99", [r["p99_ms"] for r in ts], "#dc2626")],
                xs, f"Latence sous charge ({result['concurrency']} clients) - {result['endpoint']}",
                "latence (ms)", prefix.parent / f"{prefix.name}_latency.svg")
        else:
            print("[attention] aucune réponse HTTP réussie sur toute la durée du tir : "
                  "latence non mesurable, graphique de latence non généré (voir taux d'erreur / "
                  "codes HTTP). La cible est probablement injoignable (IP/port, pare-feu, VPN).")


def print_summary(r: dict) -> None:
    b = r["baseline"]["latency_ms_mean"]
    load = r["latency_ms"]["p50"]
    factor = f"x{load / b:.1f}" if b and load else "n/a"
    print("\n" + "=" * 62)
    print(f"  RÉSULTAT - DoS L7 contre {r['method']} {r['target']}{r['endpoint']}")
    print("=" * 62)
    print(f"  Authentifié (clé API) : {'oui' if r['authenticated'] else 'non'}")
    print(f"  Charge                : {r['concurrency']} clients x {r['duration_s']}s")
    print(f"  Requêtes totales      : {r['total_requests']}  ({r['throughput_rps']} req/s)")
    print(f"  Codes HTTP            : {r['http_codes']}")
    print(f"  Taux d'erreur         : {r['error_rate_pct']} %   (dispo {r['availability_pct']} %)")
    print(f"  Latence au repos      : {b if b else 'non mesurable (baseline en échec)'} ms")
    if load is None:
        fl = r["failure_latency_ms"]
        print("  Latence sous charge   : NON MESURABLE (0 réponse HTTP réussie sur "
              f"{r['total_requests']} requêtes)")
        print(f"  Temps avant échec     : moyenne {fl['mean']} ms, max {fl['max']} ms "
              "(proche de --timeout -> cible muette/pare-feu ; très court -> connexion refusée)")
    else:
        print(f"  Latence sous charge   : p50 {load} ms  p95 {r['latency_ms']['p95']} ms  "
              f"p99 {r['latency_ms']['p99']} ms  (max {r['latency_ms']['max']} ms)")
        print(f"  Dégradation latence   : {factor}")
    print("=" * 62)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", help="URL/IP de la cible, ex: http://10.42.0.7:8000")
    p.add_argument("--endpoint", default="/model",
                   help="chemin ciblé, LIBRE (chaque groupe peut exposer autre chose) : "
                        "ex. /model, /health, /predict, /api/score, /v1/toxicity... (défaut: /model)")
    p.add_argument("--method", default="POST", choices=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   help="méthode HTTP de la requête (défaut: POST)")
    p.add_argument("--body", default=None,
                   help="corps JSON brut à envoyer, ex: '{\"comment\": \"...\"}' (remplace "
                        "--text/--model ; utile si le groupe cible n'a pas le même format que "
                        "ce projet). Sans --body, POST/PUT/PATCH envoient {\"text\":...,\"model\":...}.")
    p.add_argument("--model", default="bert", help="champ 'model' du corps par défaut (bert|lstm|laya)")
    p.add_argument("--api-key", help="clé X-API-Key de la cible, si elle en exige une (sinon 401 probable)")
    p.add_argument("--text", default=DEFAULT_TEXT, help="champ 'text' du corps par défaut")
    p.add_argument("--duration", type=float, default=30, help=f"durée en s (max {MAX_DURATION_S})")
    p.add_argument("--concurrency", type=int, default=50, help=f"clients simultanés (max {MAX_CONCURRENCY})")
    p.add_argument("--ramp", type=float, default=0, help="montée progressive en s (0 = immédiat)")
    p.add_argument("--timeout", type=float, default=30, help="timeout par requête en s")
    p.add_argument("--insecure", action="store_true", help="ne pas vérifier le certificat TLS")
    p.add_argument("--out", default=None, help="préfixe de sortie (défaut: attack/results/dos-<horodatage>)")
    a = p.parse_args()

    # Garde-fous.
    if a.duration > MAX_DURATION_S:
        raise SystemExit(f"--duration limité à {MAX_DURATION_S}s (test borné, pas une attaque réelle)")
    if a.concurrency > MAX_CONCURRENCY:
        raise SystemExit(f"--concurrency limité à {MAX_CONCURRENCY}")
    if a.api_key and a.method == "GET" and a.body is None:
        print("[info] clé fournie avec une requête GET sans corps : vérifie que l'endpoint "
              "cible l'attend bien en en-tête (X-API-Key) et pas autrement (certains groupes "
              "utilisent un autre nom d'en-tête ou un token dans l'URL).")
    if not a.api_key and a.method != "GET":
        print(f"[info] pas de clé API : si {a.endpoint} exige une authentification, attends-toi "
              "à des 401/403 (on mesure alors la couche auth, pas le modèle).")

    print(f"\n*** Test de charge AUTORISÉ (TP2) - cible unique : {a.target} ***")
    result = asyncio.run(run_attack(a))

    prefix = Path(a.out) if a.out else RESULTS_DIR / f"dos-{time.strftime('%Y%m%d-%H%M%S')}"
    write_outputs(result, prefix)
    print_summary(result)
    outputs = [f"{prefix}.json", f"{prefix.name}_timeseries.csv", f"{prefix.name}_throughput.svg"]
    if prefix.parent.joinpath(f"{prefix.name}_latency.svg").exists():
        outputs.insert(2, f"{prefix.name}_latency.svg")
    print(f"\nSorties : {' , '.join(outputs)}")


if __name__ == "__main__":
    main()
