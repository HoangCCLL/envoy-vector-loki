"""
Report API — wraps report.py, serves JSON / HTML / Markdown / CSV.

GET /report?period=1w&top=10&upstream=<name>&format=json|html|md|csv
GET /health
"""
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from report import build_report, render_csv, render_markdown

LOKI_URL = os.environ.get("LOKI_ENDPOINT", "http://loki:3100")

app = FastAPI(title="Envoy Report API", docs_url="/docs")


# ── helpers ───────────────────────────────────────────────────────────────────

def _html(report: list[dict], period: str) -> str:
    from datetime import datetime
    from report import _period_seconds

    now   = datetime.now()
    t1    = datetime.fromtimestamp(now.timestamp() - _period_seconds(period)).strftime("%Y-%m-%d %H:%M")
    t2    = now.strftime("%Y-%m-%d %H:%M")
    title = f"API Report — {t1} → {t2}"

    def fmt_cells(pairs, limit, skip_empty=False):
        items = [(k, v) for k, v in pairs if not (skip_empty and (not k or k == "-"))][:limit]
        return "<br>".join(f"{k} ({v:,})" for k, v in items) or "-"

    rows = []
    for block in report:
        nodes_str   = ", ".join(f"{n} ({c:,})" for n, c in block["nodes"])
        callers_str = ", ".join(f"{s} ({c:,})" for s, c in block["callers"][:3] if s and s != "-")
        rows.append(f"""
        <section>
          <h2>{block['upstream']} <span class="total">{block['total']:,} calls</span></h2>
          <p><b>Nodes:</b> {nodes_str or '-'} &nbsp;|&nbsp; <b>Top 3 callers:</b> {callers_str or '-'}</p>
          <table>
            <thead><tr><th>#</th><th>Calls</th><th>Path</th><th>Source service</th><th>Status codes</th></tr></thead>
            <tbody>
        """ + "".join(
            f"<tr><td>{r['rank']}</td><td>{r['total']:,}</td><td><code>{r['path']}</code></td>"
            f"<td>{fmt_cells(r['callers'], 99, skip_empty=True)}</td>"
            f"<td>{fmt_cells(r['statuses'], 99)}</td></tr>"
            for r in block["paths"]
        ) + "</tbody></table></section>")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    body  {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
    h1    {{ font-size: 1.4rem; }}
    h2    {{ font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: .3rem; }}
    .total{{ font-size: .9rem; font-weight: normal; color: #555; margin-left: .5rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: .9rem; margin-top: .5rem; }}
    th    {{ background: #f5f5f5; text-align: left; padding: .4rem .6rem; border: 1px solid #ddd; }}
    td    {{ padding: .35rem .6rem; border: 1px solid #eee; vertical-align: top; }}
    tr:nth-child(even) td {{ background: #fafafa; }}
    code  {{ font-size: .85em; }}
    p     {{ margin: .3rem 0; font-size: .9rem; color: #444; }}
    footer{{ margin-top: 2rem; font-size: .8rem; color: #999; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {''.join(rows) if rows else '<p>No data found.</p>'}
  <footer>Envoy → Loki report</footer>
</body>
</html>"""


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/report")
def report(
    period:   str                                         = Query("1w",   description="1h 1d 1w 4w"),
    top:      int                                         = Query(9999,   ge=1),
    upstream: str | None                                  = Query(None,   description="filter to one upstream"),
    format:   Literal["json", "html", "md", "csv"]       = Query("json", description="response format"),
):
    try:
        data = build_report(LOKI_URL, period, top, upstream)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not data:
        raise HTTPException(status_code=404, detail="No data found for the given parameters.")

    if format == "json":
        return data

    if format == "html":
        return HTMLResponse(_html(data, period))

    if format == "md":
        return PlainTextResponse(render_markdown(data, period), media_type="text/markdown")

    # csv — file download
    content = render_csv(data)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=report-{period}.csv"},
    )
