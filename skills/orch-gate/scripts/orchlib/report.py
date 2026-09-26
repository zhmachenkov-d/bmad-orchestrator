"""Self-contained epic report (HTML, optionally PDF via headless Chromium) built from a status snapshot."""

from __future__ import annotations

import html
import os
import shutil
import subprocess
from pathlib import Path

from .stories import StorySet, id_to_key

STATE_COLORS = {"done": "#2e7d32", "review": "#6a1b9a", "in-progress": "#1565c0", "ready": "#00838f",
                "blocked": "#c62828", "unknown": "#757575"}
BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "msedge")
PDF_TIMEOUT = 120


def _layers(rows: list[dict], stories: StorySet) -> dict[str, int]:
    keys = {r["key"] for r in rows}
    depth: dict[str, int] = {}
    for s in stories.values():  # plan order: dependencies come first
        if s.key in keys:
            depth[s.key] = 1 + max((depth[k] for d in s.depends_on if (k := id_to_key(d)) in depth), default=-1)
    return depth


def _dag_svg(rows: list[dict], stories: StorySet, critical: list[str]) -> str:
    depth = _layers(rows, stories)
    cols: dict[int, list[str]] = {}
    for r in rows:
        cols.setdefault(depth[r["key"]], []).append(r["key"])
    w, h, gx, gy = 150, 44, 60, 18
    pos = {k: (20 + c * (w + gx), 20 + i * (h + gy)) for c, ks in cols.items() for i, k in enumerate(ks)}
    width = 40 + (max(cols, default=0) + 1) * (w + gx) - gx
    height = 40 + max((len(v) for v in cols.values()), default=1) * (h + gy) - gy
    crit_edges = set(zip(critical, critical[1:]))
    by_key = {r["key"]: r for r in rows}
    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="story DAG">',
             '<defs><marker id="a" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             '<path d="M0,0L10,5L0,10z" fill="currentColor"/></marker></defs>']
    for r in rows:
        for dep in stories[r["key"]].depends_on:
            k = id_to_key(dep)
            if k not in pos:
                continue
            (x1, y1), (x2, y2) = pos[k], pos[r["key"]]
            crit = (k, r["key"]) in crit_edges
            parts.append(f'<path d="M{x1 + w},{y1 + h / 2} C{x1 + w + gx / 2},{y1 + h / 2} {x2 - gx / 2},{y2 + h / 2} {x2},{y2 + h / 2}" '
                         f'class="edge{" crit" if crit else ""}" marker-end="url(#a)"/>')
    for k, (x, y) in pos.items():
        r = by_key[k]
        color = STATE_COLORS.get(r["state"], "#757575")
        title = html.escape(f"{r['id']} {r['title']} — {r['state']}" + (f" — {r['claimant']}" if r["claimant"] else ""))
        parts.append(f'<g><title>{title}</title><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{color}"'
                     f'{" class=\"critnode\"" if k in critical else ""}/>'
                     f'<text x="{x + 8}" y="{y + 18}" class="n">{html.escape(r["id"])} · {html.escape(r["state"])}</text>'
                     f'<text x="{x + 8}" y="{y + 34}" class="s">{html.escape((r["subproject"] or "?")[:20])}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def _idle(row: dict) -> str:
    return "" if row["idle_hours"] is None or row["state"] == "done" else f"{row['idle_hours']:.0f}h"


def render(epic: int, status: dict, stories: StorySet, project: str) -> str:
    e = next((x for x in status["epics"] if x["epic"] == epic), None)
    if e is None:
        raise ValueError(f"epic {epic} not in status")
    rows = [r for r in status["stories"] if r["epic"] == epic]
    keys = {r["key"] for r in rows}
    anomalies = [a for a in status["anomalies"] if a.get("story") in keys or a.get("story") is None]
    esc = html.escape
    legend = "".join(f'<span class="chip" style="background:{c}">{s}</span>' for s, c in STATE_COLORS.items())
    subs = "".join(
        f'<tr><td>{esc(n)}</td><td><div class="bar"><div style="width:{100 * d["done"] / max(d["stories"], 1):.0f}%"></div></div></td>'
        f'<td>{d["done"]}/{d["stories"]}</td></tr>' for n, d in e["subprojects"].items())
    anom = "".join(f'<tr><td>{esc(a["code"])}</td><td>{esc(a.get("story") or "—")}</td><td>{esc(a["message"])}</td></tr>'
                   for a in anomalies) or '<tr><td colspan="3">none</td></tr>'
    table = "".join(
        f'<tr><td>{esc(r["id"])}</td><td>{esc(r["title"])}</td><td>{esc(r["subproject"] or "?")}</td>'
        f'<td><span class="chip" style="background:{STATE_COLORS.get(r["state"])}">{esc(r["state"])}</span></td>'
        f'<td>{esc(r["claimant"] or "")}</td><td>{_idle(r)}</td>'
        f'<td>{esc(", ".join(r["blocked_by"]))}</td><td>{r["downstream"]}</td></tr>' for r in rows)
    counts = " · ".join(f"{k} {v}" for k, v in e["counts"].items() if v)
    crit = " → ".join(e["critical_path"]) or "none (all done)"
    unread = "".join(f"<li>{esc(u['message'])}</li>" for u in status["unread_repos"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Epic {epic} report</title>
<style>
:root{{--bg:#fff;--fg:#1b1b1b;--muted:#666;--line:#ddd;--edge:#999}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--muted:#aaa;--line:#333;--edge:#777}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} .muted{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
.chip{{color:#fff;border-radius:10px;padding:1px 8px;font-size:12px;margin-right:4px;white-space:nowrap}}
.bar{{background:var(--line);height:8px;border-radius:4px;min-width:120px}} .bar div{{background:#2e7d32;height:8px;border-radius:4px}}
.dag{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;padding:8px}}
svg{{color:var(--edge)}} .edge{{fill:none;stroke:var(--edge);stroke-width:1.2}} .edge.crit{{stroke:#ef6c00;stroke-width:3}}
.critnode{{stroke:#ef6c00;stroke-width:3}} text.n{{fill:#fff;font-size:12px;font-weight:600}} text.s{{fill:#fff;font-size:11px;opacity:.85}}
@media print{{body{{max-width:none}} .dag{{overflow:visible}} h2{{break-after:avoid}} tr{{break-inside:avoid}}}}
</style></head><body>
<h1>{esc(project)} — epic {epic}</h1>
<p class="muted">State: <b>{esc(e["state"])}</b> · {e["stories"]} stories · {esc(counts)} · generated {esc(status["now"])}</p>
<p>{legend}</p>
<h2>Story DAG</h2><div class="dag">{_dag_svg(rows, stories, e["critical_path"])}</div>
<p class="muted">Critical path (orange): {esc(crit)}</p>
<h2>Anomalies</h2><table><tr><th>Code</th><th>Story</th><th>Detail</th></tr>{anom}</table>
{f"<h2>Unread repos</h2><ul>{unread}</ul>" if unread else ""}
<h2>Progress by subproject</h2><table>{subs}</table>
<h2>Stories</h2><table><tr><th>Id</th><th>Title</th><th>Subproject</th><th>State</th><th>Claimant</th><th>Idle</th><th>Blocked by</th><th>Downstream</th></tr>{table}</table>
</body></html>
"""


def find_browser() -> str | None:
    if (env := os.environ.get("ORCH_CHROME")) and shutil.which(env):
        return shutil.which(env)
    return next((p for b in BROWSERS if (p := shutil.which(b))), None)


def to_pdf(html_path: Path) -> tuple[Path | None, str | None]:
    """Print the HTML with headless Chromium; (pdf path, None) or (None, why not)."""
    browser = find_browser()
    if not browser:
        return None, "no Chromium/Chrome found (set ORCH_CHROME to its binary); open the HTML and print it to PDF"
    pdf = html_path.with_suffix(".pdf")
    argv = [browser, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
            f"--print-to-pdf={pdf}", html_path.resolve().as_uri()]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=PDF_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as exc:
        return None, f"{browser} failed: {exc}"
    if proc.returncode or not pdf.exists():
        return None, f"{browser} exited {proc.returncode}: {(proc.stderr or '').strip()[-300:]}"
    return pdf, None
