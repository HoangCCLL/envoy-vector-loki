#!/usr/bin/env python3
"""
Loki access log report — per-upstream breakdown: top paths, callers, nodes.

Usage:
    python report.py
    python report.py --upstream binance-spot
    python report.py --period 4w --top 20 --output report.md
    python report.py --period 1d --output report.csv --loki http://loki-host:3100
"""
import argparse
import csv
import io
import sys
from collections import defaultdict
from datetime import datetime

import httpx


# ── Loki ─────────────────────────────────────────────────────────────────────

_PERIOD_TO_SECONDS = {
    "h": 3600, "d": 86400, "w": 604800,
}


def _period_seconds(period: str) -> int:
    """Convert e.g. '1h', '4w', '1d' → seconds."""
    for suffix, secs in _PERIOD_TO_SECONDS.items():
        if period.endswith(suffix):
            return int(period[:-1]) * secs
    raise ValueError(f"Unknown period format: {period!r}")


def loki_query(loki_url: str, query: str, period: str = "1w") -> list[dict]:
    """
    Run a LogQL metric query and return the result list.

    Uses /query_range so that extracted labels (e.g. `path` from | json) work
    in `sum by` — the instant /query endpoint rejects those in Loki 2.9.x.
    step = period so we get a single aggregated data point.
    """
    import time as _time
    now   = int(_time.time())
    start = now - _period_seconds(period)
    resp  = httpx.get(
        f"{loki_url}/loki/api/v1/query_range",
        params={"query": query, "start": start, "end": now, "step": f"{period}"},
        timeout=60,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Loki {resp.status_code} for query:\n  {query}\nResponse: {resp.text}"
        )
    # query_range returns "matrix" — each series has multiple (timestamp, value) pairs.
    # We want the last (highest) value per series, which is the cumulative count.
    results = []
    for series in resp.json()["data"]["result"]:
        if not series["values"]:
            continue
        # Take the last value (most recent / highest cumulative count)
        _, val = series["values"][-1]
        results.append({"metric": series["metric"], "value": [None, val]})
    return results


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_report_data(loki_url: str, period: str, upstream_filter: str | None) -> dict:
    """
    Run 5 LogQL queries and return raw aggregated dicts.
    All queries are label-only where possible (fast); json parse only when path is needed.
    """
    stream = f'{{job="envoy", upstream="{upstream_filter}"}}' if upstream_filter \
             else '{job="envoy"}'

    # 1. Total calls per upstream (labels only — fast)
    upstream_totals: dict[str, int] = {}
    for r in loki_query(loki_url, f"sum by (upstream) (count_over_time({stream}[{period}]))", period):
        upstream_totals[r["metric"].get("upstream", "-")] = int(r["value"][1])

    # 2. Calls per (upstream, instance/node) (labels only — fast)
    #    Result: {upstream -> {instance -> count}}
    nodes_by_upstream: dict[str, dict[str, int]] = defaultdict(dict)
    for r in loki_query(loki_url, f"sum by (upstream, instance) (count_over_time({stream}[{period}]))", period):
        up   = r["metric"].get("upstream", "-")
        node = r["metric"].get("instance", "-")
        nodes_by_upstream[up][node] = int(r["value"][1])

    # 3. Calls per (upstream, path) — needs json for path field
    #    Result: {upstream -> {path -> count}}
    paths_by_upstream: dict[str, dict[str, int]] = defaultdict(dict)
    for r in loki_query(loki_url, f"sum by (upstream, path) (count_over_time({stream} | json [{period}]))", period):
        up   = r["metric"].get("upstream", "-")
        path = r["metric"].get("path", "-")
        paths_by_upstream[up][path] = int(r["value"][1])

    # 4. Calls per (upstream, path, source_service) — needs json
    #    Result: {upstream -> {path -> {source_service -> count}}}
    callers: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for r in loki_query(loki_url, f"sum by (upstream, path, source_service) (count_over_time({stream} | json [{period}]))", period):
        up   = r["metric"].get("upstream", "-")
        path = r["metric"].get("path", "-")
        svc  = r["metric"].get("source_service", "-")
        callers[up][path][svc] = int(r["value"][1])

    # 5. Calls per (upstream, path, response_code) — response_code is label, path needs json
    #    Result: {upstream -> {path -> {response_code -> count}}}
    statuses: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for r in loki_query(loki_url, f"sum by (upstream, path, response_code) (count_over_time({stream} | json [{period}]))", period):
        up   = r["metric"].get("upstream", "-")
        path = r["metric"].get("path", "-")
        code = r["metric"].get("response_code", "-")
        statuses[up][path][code] = int(r["value"][1])

    return {
        "upstream_totals":  upstream_totals,
        "nodes_by_upstream": nodes_by_upstream,
        "paths_by_upstream": paths_by_upstream,
        "callers":           callers,
        "statuses":          statuses,
    }


# ── Report building ───────────────────────────────────────────────────────────

def _strip_prefix(path: str, upstream: str) -> str:
    """Strip /{upstream}/ prefix from stored path, return the real upstream path."""
    prefix = f"/{upstream}/"
    return path[len(prefix):] if path.startswith(prefix) else path


def build_report(loki_url: str, period: str, top: int, upstream_filter: str | None) -> list[dict]:
    data = fetch_report_data(loki_url, period, upstream_filter)

    upstream_totals  = data["upstream_totals"]
    nodes_by_up      = data["nodes_by_upstream"]
    paths_by_up      = data["paths_by_upstream"]
    callers          = data["callers"]
    statuses         = data["statuses"]

    # Derive top callers per upstream by summing across all paths
    callers_by_up: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for up, path_map in callers.items():
        for path, svc_map in path_map.items():
            for svc, n in svc_map.items():
                callers_by_up[up][svc] += n

    report = []
    for upstream, total in sorted(upstream_totals.items(), key=lambda x: x[1], reverse=True):
        top_paths = sorted(paths_by_up[upstream].items(), key=lambda x: x[1], reverse=True)[:top]

        paths = []
        for rank, (raw_path, count) in enumerate(top_paths, 1):
            real_path = _strip_prefix(raw_path, upstream)
            paths.append({
                "rank":     rank,
                "path":     real_path,
                "total":    count,
                "callers":  sorted(callers[upstream].get(raw_path, {}).items(),  key=lambda x: x[1], reverse=True),
                "statuses": sorted(statuses[upstream].get(raw_path, {}).items(), key=lambda x: x[1], reverse=True),
            })

        report.append({
            "upstream":      upstream,
            "total":         total,
            "nodes":         sorted(nodes_by_up[upstream].items(),     key=lambda x: x[1], reverse=True),
            "top_callers":   sorted(callers_by_up[upstream].items(),   key=lambda x: x[1], reverse=True)[:5],
            "paths":         paths,
        })

    return report


# ── Rendering ─────────────────────────────────────────────────────────────────

def _fmt_pairs(pairs: list[tuple], limit: int = 3, skip_empty: bool = False) -> str:
    filtered = [(k, v) for k, v in pairs if not (skip_empty and not k)]
    return ", ".join(f"{k} ({v:,})" for k, v in filtered[:limit])


def render_markdown(report: list[dict], period: str) -> str:
    lines = [
        f"# API Report — last {period}",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
    ]
    for block in report:
        lines += [
            f"## {block['upstream']} — {block['total']:,} calls",
            f"**Source Services:** {_fmt_pairs(block['top_callers'], skip_empty=True) or '-'}  ",
            f"**Nodes:**   {_fmt_pairs(block['nodes']) or '-'}",
            "",
            "| # | Calls | Path | Source Service | Status codes |",
            "|--:|------:|------|----------------|--------------|",
        ]
        for row in block["paths"]:
            lines.append(
                f"| {row['rank']} | {row['total']:,} | `{row['path']}` "
                f"| {_fmt_pairs(row['callers'], skip_empty=True) or '-'} | {_fmt_pairs(row['statuses'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_csv(report: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["upstream", "upstream_total", "rank", "path", "path_calls",
                "source_service", "calls_from_service", "response_code", "calls_with_code"])
    for block in report:
        for row in block["paths"]:
            callers  = row["callers"]  or [("-", row["total"])]
            statuses = row["statuses"] or [("-", row["total"])]
            max_rows = max(len(callers), len(statuses))
            for i in range(max_rows):
                svc,  ncalls = callers[i]  if i < len(callers)  else ("", "")
                code, ncode  = statuses[i] if i < len(statuses) else ("", "")
                w.writerow([
                    block["upstream"] if i == 0 else "",
                    block["total"]    if i == 0 else "",
                    row["rank"]       if i == 0 else "",
                    row["path"]       if i == 0 else "",
                    row["total"]      if i == 0 else "",
                    svc, ncalls, code, ncode,
                ])
    return buf.getvalue()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Loki access log report — per upstream")
    p.add_argument("--period",   default="1w",                    help="query window: 1h 1d 1w 4w (default: 1w)")
    p.add_argument("--top",      type=int, default=10,            help="top N paths per upstream (default: 10)")
    p.add_argument("--upstream", default=None,                    help="filter to 1 upstream: binance-spot, httpbin, ...")
    p.add_argument("--output",   default=None, metavar="FILE",    help="output file (.md or .csv); default stdout")
    p.add_argument("--loki",     default="http://localhost:3100", help="Loki base URL")
    args = p.parse_args()

    report = build_report(args.loki, args.period, args.top, args.upstream)

    if not report:
        print("No data found.", file=sys.stderr)
        sys.exit(1)

    is_csv = (args.output or "").endswith(".csv")
    content = render_csv(report) if is_csv else render_markdown(report, args.period)

    if args.output:
        with open(args.output, "w") as f:
            f.write(content)
        print(f"Written to {args.output}")
    else:
        print(content)


if __name__ == "__main__":
    main()
