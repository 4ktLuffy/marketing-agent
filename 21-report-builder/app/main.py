"""Report builder: the /kpis output of analytics-ingest -> a one-page HTML + markdown report."""
import html
import re

import markdown
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="report-builder")

# (key, label, kind, higher_is_better). None = neutral: more spend is neither good nor bad.
TILES = [
    ("sessions", "Sessions", "int", True),
    ("clicks", "Clicks", "int", True),
    ("impressions", "Impressions", "int", True),
    ("conversions", "Conversions", "num", True),
    ("ctr", "CTR", "pct", True),
    ("cvr", "Conversion rate", "pct", True),
    ("spend", "Spend", "money", None),
    ("cpa", "Cost per conversion", "money", False),
]
TABLE_COLS = [("sessions", "int"), ("clicks", "int"), ("conversions", "num"),
              ("ctr", "pct"), ("cvr", "pct"), ("spend", "money"), ("cpa", "money")]


class Kpis(BaseModel):
    period: dict | None = None
    totals: dict = Field(default_factory=dict)
    by_channel: list[dict] = Field(default_factory=list)
    previous: dict | None = None
    delta_pct: dict | None = None


class RenderRequest(BaseModel):
    title: str
    period: str | dict
    kpis: Kpis
    highlights_markdown: str | None = None
    currency: str = ""  # optional prefix for money values, e.g. "$" or "EUR "


# ---------- formatting


def fmt(value, kind: str, currency: str = "") -> str:
    if value is None:
        return "–"
    if kind == "int":
        return f"{int(round(value)):,}"
    if kind == "num":
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    if kind == "pct":
        return f"{value * 100:.2f}%"
    return f"{currency}{value:,.2f}"


def period_text(period) -> str:
    if isinstance(period, dict):
        return f"{period.get('from', '?')} to {period.get('to', '?')}"
    return str(period)


def delta_info(key: str, delta, higher_is_better):
    """Return (arrow, text, tone) where tone is good|bad|flat|neutral, or None if no delta."""
    if delta is None:
        return None
    arrow = "▲" if delta > 0 else "▼" if delta < 0 else "■"
    text = f"{arrow} {delta:+.1f}%"
    if delta == 0 or higher_is_better is None:
        return arrow, text, "flat" if delta == 0 else "neutral"
    good = (delta > 0) == higher_is_better
    return arrow, text, "good" if good else "bad"


def chart_metric(channels: list[dict]) -> str:
    return "sessions" if any(c.get("sessions") for c in channels) else "clicks"


def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def highlights_html(text: str) -> str:
    md = markdown.Markdown(extensions=["sane_lists"])
    md.preprocessors.deregister("html_block")  # raw HTML is escaped, not rendered
    md.inlinePatterns.deregister("html")
    out = md.convert(text)
    return re.sub(r'href="(?!https?:|mailto:)[^"]*"', 'href="#"', out)


# ---------- HTML


def bar_chart_svg(channels: list[dict], metric: str) -> str:
    rows = [c for c in channels if c.get(metric)]
    rows.sort(key=lambda c: -c[metric])
    if not rows:
        return '<p class="muted">No channel data for this period.</p>'
    label_w, bar_w, value_w, row_h, bar_h = 150, 380, 80, 30, 18
    width, height = label_w + bar_w + value_w, row_h * len(rows) + 8
    top = max(c[metric] for c in rows)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="{metric.capitalize()} by channel" xmlns="http://www.w3.org/2000/svg" '
        'style="max-width:640px;font-family:inherit;font-size:13px;">'
    ]
    for i, c in enumerate(rows):
        y = 4 + i * row_h
        w = max(2.0, bar_w * c[metric] / top)
        name = html.escape(str(c.get("channel", "")))
        value = fmt(c[metric], "int")
        parts.append(
            f'<g><title>{name}: {value} {metric}</title>'
            f'<text x="{label_w - 10}" y="{y + bar_h / 2 + 4.5}" text-anchor="end" class="svg-label">{name}</text>'
            f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" class="svg-bar"/>'
            f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h / 2 + 4.5}" class="svg-value">{value}</text></g>'
        )
    parts.append(f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{height}" class="svg-axis"/></svg>')
    return "".join(parts)


CSS = """
:root{--bg:#f4f4f2;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e3e2de;
--bar:#2a78d6;--good:#006300;--bad:#b42318;--flat:#52514e}
@media (prefers-color-scheme:dark){:root{--bg:#111110;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--line:#383835;--bar:#3987e5;--good:#0ca30c;--bad:#ef6b6b;--flat:#c3c2b7}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:880px;margin:0 auto;padding:32px 16px 48px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
.muted{color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile .label{color:var(--ink2);font-size:13px}
.tile .value{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums;margin:2px 0}
.tile .delta{font-size:13px;font-variant-numeric:tabular-nums}
.good{color:var(--good)}.bad{color:var(--bad)}.flat,.neutral{color:var(--flat)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--ink2);font-weight:600;font-size:13px}
tr:last-child td{border-bottom:0}
.svg-bar{fill:var(--bar)}.svg-label{fill:var(--ink2)}.svg-value{fill:var(--ink)}.svg-axis{stroke:var(--line)}
.highlights p:first-child{margin-top:0}
"""


def render_html(req: RenderRequest) -> str:
    k, cur = req.kpis, req.currency
    deltas = k.delta_pct or {}
    tiles = []
    for key, label, kind, better in TILES:
        if key not in k.totals:
            continue
        d = delta_info(key, deltas.get(key), better)
        delta_html = (
            f'<div class="delta {d[2]}">{html.escape(d[1])} <span class="muted">vs previous</span></div>'
            if d else '<div class="delta muted">no comparison</div>'
        )
        tiles.append(
            f'<div class="tile"><div class="label">{label}</div>'
            f'<div class="value">{html.escape(fmt(k.totals[key], kind, cur))}</div>{delta_html}</div>'
        )

    head = "".join(f"<th>{c.upper() if c in ('ctr', 'cvr', 'cpa') else c.capitalize()}</th>" for c, _ in TABLE_COLS)
    body = "".join(
        "<tr><td>" + html.escape(str(ch.get("channel", ""))) + "</td>"
        + "".join(f"<td>{html.escape(fmt(ch.get(c), kind, cur))}</td>" for c, kind in TABLE_COLS)
        + "</tr>"
        for ch in k.by_channel
    )
    table = (f'<table><thead><tr><th>Channel</th>{head}</tr></thead><tbody>{body}</tbody></table>'
             if k.by_channel else '<p class="muted">No channel data for this period.</p>')

    metric = chart_metric(k.by_channel)
    compare_note = ""
    if k.previous and isinstance(k.previous.get("period"), dict):
        compare_note = f" · compared with {html.escape(period_text(k.previous['period']))}"
    highlights = ""
    if req.highlights_markdown and req.highlights_markdown.strip():
        highlights = f'<h2>Highlights</h2><div class="card highlights">{highlights_html(req.highlights_markdown)}</div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(req.title)}</title><style>{CSS}</style></head>
<body><main>
<h1>{html.escape(req.title)}</h1>
<p class="muted">{html.escape(period_text(req.period))}{compare_note}</p>
<div class="tiles">{''.join(tiles)}</div>
{highlights}
<h2>{metric.capitalize()} by channel</h2>
<div class="card">{bar_chart_svg(k.by_channel, metric)}</div>
<h2>By channel</h2>
<div class="card">{table}</div>
<p class="muted" style="font-size:12px;margin-top:24px">CTR = clicks / impressions ·
Conversion rate = conversions / sessions (or / clicks without sessions) ·
Cost per conversion: lower is better.</p>
</main></body></html>"""


# ---------- markdown


def render_markdown(req: RenderRequest) -> str:
    k, cur = req.kpis, req.currency
    deltas = k.delta_pct or {}
    lines = [f"# {req.title}", "", f"_{period_text(req.period)}_", "",
             "| Metric | Value | vs previous |", "|---|---:|---:|"]
    for key, label, kind, better in TILES:
        if key not in k.totals:
            continue
        d = delta_info(key, deltas.get(key), better)
        lines.append(f"| {label} | {fmt(k.totals[key], kind, cur)} | {d[1] if d else '–'} |")
    if req.highlights_markdown and req.highlights_markdown.strip():
        lines += ["", "## Highlights", "", req.highlights_markdown.strip()]
    lines += ["", "## By channel", ""]
    if k.by_channel:
        lines.append("| Channel | " + " | ".join(c.upper() if c in ("ctr", "cvr", "cpa") else c.capitalize()
                                               for c, _ in TABLE_COLS) + " |")
        lines.append("|---|" + "---:|" * len(TABLE_COLS))
        for ch in k.by_channel:
            cells = [md_cell(ch.get("channel", ""))] + [fmt(ch.get(c), kind, cur) for c, kind in TABLE_COLS]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("No channel data for this period.")
    return "\n".join(lines) + "\n"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/render")
def render(req: RenderRequest):
    return {"html": render_html(req), "markdown": render_markdown(req)}
