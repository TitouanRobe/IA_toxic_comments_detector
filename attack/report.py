"""Tableau de bord HTML des résultats d'attaque (attack/results/*.json).

Un seul fichier HTML autonome (pas de dépendance, pas de serveur, graphiques intégrés) :
- Tableau récapitulatif de tous les tirs (dates, cible, charge, latence, erreurs).
- Fiche détaillée par tir (les 2 graphiques SVG produits par dos.py).
- Comparaison avant/après (étape 3 : efficacité de la défense) : deux menus déroulants,
  tableau des métriques avec delta, graphiques des deux tirs côte à côte.

À relancer après chaque nouveau tir (attack/dos.py) pour mettre à jour le tableau de bord.

Usage (depuis la racine du projet) :
  uv run python attack/report.py
  -> attack/results/report.html (double-cliquer pour ouvrir dans le navigateur)
"""

import json
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT = RESULTS / "report.html"


def load_runs() -> list[dict]:
    runs = []
    for jf in sorted(RESULTS.glob("dos-*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        run_id = jf.stem
        lat_svg = RESULTS / f"{run_id}_latency.svg"
        thr_svg = RESULTS / f"{run_id}_throughput.svg"
        data["_id"] = run_id
        data["_latency_svg"] = lat_svg.read_text(encoding="utf-8") if lat_svg.exists() else None
        data["_throughput_svg"] = thr_svg.read_text(encoding="utf-8") if thr_svg.exists() else None
        runs.append(data)
    runs.sort(key=lambda r: r["_id"], reverse=True)
    return runs


def fmt_time(run_id: str) -> str:
    """dos-20261113-142936 -> 2026-11-13 14:29:36 (retombe sur l'id brut si format inattendu)."""
    parts = run_id.split("-")
    if len(parts) == 3 and len(parts[1]) == 8 and len(parts[2]) == 6:
        d, t = parts[1], parts[2]
        return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"
    return run_id


def num(v, digits: int = 1) -> str:
    return f"{v:.{digits}f}" if isinstance(v, (int, float)) else "n/a"


def degradation(run: dict) -> str:
    base = run["baseline"].get("latency_ms_mean")
    p50 = run["latency_ms"].get("p50")
    if not base or not p50:
        return "n/a"
    return f"x{p50 / base:.1f}"


def http_codes_str(run: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(run["http_codes"].items(), key=lambda kv: -kv[1]))


CSS = """
:root {
  --bg: #fafafa; --panel: #ffffff; --text: #1a1a1a; --muted: #666;
  --border: #e3e3e3; --accent: #2563eb; --danger: #dc2626; --good: #16a34a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15171c; --panel: #1d2026; --text: #eaeaea; --muted: #9aa0a8; --border: #2c303a;
  }
}
:root[data-theme="dark"] {
  --bg: #15171c; --panel: #1d2026; --text: #eaeaea; --muted: #9aa0a8; --border: #2c303a;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px 16px 60px;
}
.wrap { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
.sub { color: var(--muted); font-size: 0.9rem; margin-bottom: 28px; }
h2 { font-size: 1.15rem; margin: 36px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.panel {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; margin-bottom: 16px;
}
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; position: sticky; top: 0; background: var(--panel); }
tr:hover td { background: rgba(37, 99, 235, 0.06); }
.table-scroll { overflow-x: auto; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
}
.badge.ok { background: rgba(22,163,74,0.15); color: var(--good); }
.badge.err { background: rgba(220,38,38,0.15); color: var(--danger); }
details { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; background: var(--panel); }
summary {
  cursor: pointer; padding: 12px 16px; font-weight: 600; font-size: 0.9rem;
  display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap;
}
summary .meta { color: var(--muted); font-weight: 400; font-size: 0.8rem; }
.detail-body { padding: 0 16px 16px; }
.charts { display: flex; gap: 16px; flex-wrap: wrap; }
.charts > div { flex: 1; min-width: 280px; }
.charts svg { width: 100%; height: auto; display: block; }
.compare-pickers { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
select {
  padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--panel); color: var(--text); font-size: 0.85rem; min-width: 260px;
}
.arrow { color: var(--muted); font-size: 1.1rem; }
.diff-pos { color: var(--danger); font-weight: 600; }
.diff-neg { color: var(--good); font-weight: 600; }
.diff-zero { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; padding: 20px; text-align: center; }
footer { color: var(--muted); font-size: 0.78rem; margin-top: 40px; text-align: center; }
.svg-store { display: none; }
"""

JS = """
const RUNS = __RUNS_JSON__;
// 4e champ : true = plus haut est pire, false = plus bas est pire, null = pas de jugement
// (le débit/volume reçu par l'attaquant n'a pas de "mieux" absolu : une défense qui le fait
// chuter est un succès, pas une régression — seul le delta est affiché, sans couleur).
const METRICS = [
  ["total_requests", "Requêtes totales (vues par l'attaquant)", 0, null],
  ["throughput_rps", "Débit (req/s, vu par l'attaquant)", 1, null],
  ["error_rate_pct", "Taux d'erreur (%)", 1, true],
  ["availability_pct", "Disponibilité (%)", 1, false],
];
const LAT_METRICS = [["p50", "Latence p50 (ms)"], ["p95", "Latence p95 (ms)"], ["p99", "Latence p99 (ms)"]];

function byId(id) { return RUNS.find(r => r._id === id); }

function fillPickers() {
  const opts = RUNS.map(r => `<option value="${r._id}">${r._label}</option>`).join("");
  document.getElementById("pick-before").innerHTML = opts;
  document.getElementById("pick-after").innerHTML = opts;
  if (RUNS.length > 1) {
    document.getElementById("pick-before").value = RUNS[RUNS.length - 1]._id;
    document.getElementById("pick-after").value = RUNS[0]._id;
  }
}

function deltaCell(before, after, higherIsWorse) {
  if (before === null || after === null || before === undefined || after === undefined) {
    return '<td class="diff-zero">n/a</td>';
  }
  const d = after - before;
  const pct = before !== 0 ? (100 * d / Math.abs(before)) : 0;
  let cls = "diff-zero";
  if (higherIsWorse !== null && Math.abs(d) >= 1e-9) {
    const worse = higherIsWorse ? d > 0 : d < 0;
    cls = worse ? "diff-pos" : "diff-neg";
  }
  const sign = d > 0 ? "+" : "";
  return `<td class="${cls}">${sign}${d.toFixed(1)} (${sign}${pct.toFixed(0)}%)</td>`;
}

function renderCompare() {
  const before = byId(document.getElementById("pick-before").value);
  const after = byId(document.getElementById("pick-after").value);
  const out = document.getElementById("compare-output");
  if (!before || !after) { out.innerHTML = '<p class="empty">Choisir deux tirs.</p>'; return; }

  let rows = "";
  for (const [key, label, digits, worse] of METRICS) {
    rows += `<tr><td>${label}</td><td>${before[key]?.toFixed ? before[key].toFixed(digits) : before[key]}</td>`
          + `<td>${after[key]?.toFixed ? after[key].toFixed(digits) : after[key]}</td>`
          + deltaCell(before[key], after[key], worse) + `</tr>`;
  }
  for (const [key, label] of LAT_METRICS) {
    const b = before.latency_ms[key], a = after.latency_ms[key];
    rows += `<tr><td>${label}</td><td>${b ?? "n/a"}</td><td>${a ?? "n/a"}</td>` + deltaCell(b, a, true) + `</tr>`;
  }

  out.innerHTML = `
    <div class="panel">
      <div class="table-scroll"><table>
        <tr><th>Métrique</th><th>Avant (${before._label})</th><th>Après (${after._label})</th><th>Δ (après - avant)</th></tr>
        ${rows}
      </table></div>
    </div>
    <div class="charts">
      <div><h3 style="font-size:0.85rem;color:var(--muted)">Latence — avant</h3>${document.getElementById("store-" + before._id).querySelector(".lat").innerHTML}</div>
      <div><h3 style="font-size:0.85rem;color:var(--muted)">Latence — après</h3>${document.getElementById("store-" + after._id).querySelector(".lat").innerHTML}</div>
    </div>
    <div class="charts">
      <div><h3 style="font-size:0.85rem;color:var(--muted)">Débit/erreurs — avant</h3>${document.getElementById("store-" + before._id).querySelector(".thr").innerHTML}</div>
      <div><h3 style="font-size:0.85rem;color:var(--muted)">Débit/erreurs — après</h3>${document.getElementById("store-" + after._id).querySelector(".thr").innerHTML}</div>
    </div>`;
}

document.getElementById("pick-before").addEventListener("change", renderCompare);
document.getElementById("pick-after").addEventListener("change", renderCompare);
fillPickers();
renderCompare();
"""


def build_summary_table(runs: list[dict]) -> str:
    head = ("<tr><th>Date</th><th>Cible</th><th>Endpoint</th><th>Auth</th><th>Clients</th><th>Durée (s)</th>"
            "<th>Requêtes</th><th>Débit (req/s)</th><th>Erreurs</th><th>Dispo</th>"
            "<th>p50 (ms)</th><th>p95 (ms)</th><th>p99 (ms)</th><th>Dégradation</th></tr>")
    rows = []
    for r in runs:
        err_badge = "ok" if r["error_rate_pct"] < 5 else "err"
        rows.append(
            "<tr>"
            f"<td>{escape(fmt_time(r['_id']))}</td>"
            f"<td>{escape(str(r['target']))}</td>"
            f"<td>{escape(r['endpoint'])}</td>"
            f"<td>{'oui' if r['authenticated'] else 'non'}</td>"
            f"<td>{r['concurrency']}</td>"
            f"<td>{num(r['duration_s'], 0)}</td>"
            f"<td>{r['total_requests']}</td>"
            f"<td>{num(r['throughput_rps'])}</td>"
            f'<td><span class="badge {err_badge}">{num(r["error_rate_pct"])}%</span></td>'
            f"<td>{num(r['availability_pct'])}%</td>"
            f"<td>{num(r['latency_ms']['p50'])}</td>"
            f"<td>{num(r['latency_ms']['p95'])}</td>"
            f"<td>{num(r['latency_ms']['p99'])}</td>"
            f"<td>{escape(degradation(r))}</td>"
            "</tr>"
        )
    return f'<div class="table-scroll"><table>{head}{"".join(rows)}</table></div>'


def build_run_card(r: dict) -> str:
    charts = []
    if r["_latency_svg"]:
        charts.append(f'<div>{r["_latency_svg"]}</div>')
    if r["_throughput_svg"]:
        charts.append(f'<div>{r["_throughput_svg"]}</div>')
    charts_html = f'<div class="charts">{"".join(charts)}</div>' if charts else ""
    base = r["baseline"].get("latency_ms_mean")
    summary = (
        f'<summary><span>{escape(fmt_time(r["_id"]))}</span>'
        f'<span class="meta">{escape(str(r["target"]))}{escape(r["endpoint"])} · '
        f'{r["concurrency"]} clients × {num(r["duration_s"], 0)}s · '
        f'{"authentifié" if r["authenticated"] else "sans clé"} · '
        f'erreurs {num(r["error_rate_pct"])}% · dégradation {escape(degradation(r))}</span></summary>'
    )
    if r["latency_ms"]["p50"] is None:
        fl = r.get("failure_latency_ms", {})
        latency_line = (
            "<p><strong>Latence sous charge :</strong> "
            '<span class="badge err">non mesurable — 0 réponse HTTP réussie</span><br>'
            f"<strong>Temps avant échec :</strong> moyenne {num(fl.get('mean'))} ms, "
            f"max {num(fl.get('max'))} ms (proche du timeout configuré → cible muette/pare-feu ; "
            "très court → connexion activement refusée)</p>"
        )
    else:
        latency_line = (
            f"<p><strong>Latence sous charge :</strong> p50 {num(r['latency_ms']['p50'])} ms, "
            f"p95 {num(r['latency_ms']['p95'])} ms, p99 {num(r['latency_ms']['p99'])} ms, "
            f"max {num(r['latency_ms']['max']) if r['latency_ms']['max'] else 'n/a'} ms</p>"
        )
    body = (
        '<div class="detail-body">'
        f"<p><strong>Codes HTTP :</strong> {escape(http_codes_str(r))}</p>"
        f"<p><strong>Latence au repos (baseline) :</strong> {num(base) if base else 'n/a (baseline en échec)'} ms</p>"
        f"{latency_line}"
        f"{charts_html}"
        "</div>"
    )
    return f"<details>{summary}{body}</details>"


def build_svg_store(r: dict) -> str:
    lat = r["_latency_svg"] or ""
    thr = r["_throughput_svg"] or ""
    return f'<div class="svg-store" id="store-{escape(r["_id"])}"><div class="lat">{lat}</div><div class="thr">{thr}</div></div>'


def label_for(r: dict) -> str:
    return f"{fmt_time(r['_id'])} · {r['target']}{r['endpoint']} · {r['concurrency']}c"


def main() -> None:
    runs = load_runs()
    if not runs:
        raise SystemExit(f"Aucun résultat dans {RESULTS} : lancez d'abord attack/dos.py")

    runs_json = json.dumps(
        [
            {
                "_id": r["_id"],
                "_label": label_for(r),
                "total_requests": r["total_requests"],
                "throughput_rps": r["throughput_rps"],
                "error_rate_pct": r["error_rate_pct"],
                "availability_pct": r["availability_pct"],
                "latency_ms": r["latency_ms"],
            }
            for r in runs
        ],
        ensure_ascii=False,
    )

    cards = "\n".join(build_run_card(r) for r in runs)
    stores = "\n".join(build_svg_store(r) for r in runs)
    summary_table = build_summary_table(runs)
    js = JS.replace("__RUNS_JSON__", runs_json)

    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Résultats d'attaque — TP2</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>Tableau de bord — Attaque DoS (TP2)</h1>
  <div class="sub">{len(runs)} tir(s) enregistré(s) dans attack/results/. Régénéré par attack/report.py.</div>

  <h2>Récapitulatif de tous les tirs</h2>
  <div class="panel">{summary_table}</div>

  <h2>Comparaison avant / après (étape 3 — efficacité de la défense)</h2>
  <div class="compare-pickers">
    <label>Avant : <select id="pick-before"></select></label>
    <span class="arrow">→</span>
    <label>Après : <select id="pick-after"></select></label>
  </div>
  <div id="compare-output"></div>

  <h2>Détail par tir</h2>
  {cards}

  {stores}

  <footer>Généré par attack/report.py · attack/dos.py (TP2 Digital Social Score)</footer>
</div>
<script>
{js}
</script>
</body>
</html>"""

    OUT.write_text(html, encoding="utf-8")
    print(f"{len(runs)} tir(s) -> {OUT}")


if __name__ == "__main__":
    main()
