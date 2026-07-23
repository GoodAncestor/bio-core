"""Report renderer — the report model -> HTML (and PDF via a headless renderer).

Design (docs/DESIGN.md §4.4, §5):
  - One report model feeds both the interactive view and the static file.
  - Findings are grouped by category, then sorted by evidence tier so the
    reader sees robust findings first and speculative ones clearly marked.
  - The disclaimer is embedded ONCE, from docs/DISCLAIMER.md — never restated.
  - A reproducibility footer lists tool + database versions at generation time.

HTML is produced with the stdlib only so the core has no hard dependency; PDF
rendering (weasyprint) is an optional extra invoked by to_pdf().
"""
from __future__ import annotations
import html, datetime
from pathlib import Path
from ..providers.base import Finding, Tier, Category, ProviderStatus

_TIER_ORDER = {Tier.ROBUST: 0, Tier.MODERATE: 1, Tier.SPECULATIVE: 2, Tier.UNKNOWN: 3}
_TIER_LABEL = {Tier.ROBUST: "Robust", Tier.MODERATE: "Moderate",
               Tier.SPECULATIVE: "Speculative", Tier.UNKNOWN: "Unknown"}
_CAT_LABEL = {Category.CLINICAL: "Clinical relevance",
              Category.AGING: "Aging &amp; wellness",
              Category.TRAIT: "Traits &amp; ancestry"}


def _disclaimer_html(disclaimer_path: str) -> str:
    p = Path(disclaimer_path)
    text = p.read_text() if p.exists() else "See DISCLAIMER.md."
    # embed the single source verbatim (docs/DESIGN.md §5)
    return "<pre class='disclaimer'>" + html.escape(text) + "</pre>"


def _finding_row(f: Finding) -> str:
    tier_cls = f.tier.value
    detail = ", ".join(f"{k}={v}" for k, v in f.detail.items() if v not in (None, ""))
    link = f" <a href='{html.escape(f.link)}'>source</a>" if f.link else ""
    pmids = (" · PMID " + ", ".join(f.pmids)) if f.pmids else ""
    return (f"<tr class='tier-{tier_cls}'>"
            f"<td><span class='badge {tier_cls}'>{_TIER_LABEL[f.tier]}</span></td>"
            f"<td>{html.escape(f.marker)}</td>"
            f"<td>{html.escape(f.description)}{link}{pmids}"
            f"<div class='detail'>{html.escape(detail)}</div></td>"
            f"<td>{html.escape(f.source)}</td></tr>")


def render_html(findings: list[Finding],
                provider_status: list[ProviderStatus],
                disclaimer_path: str = "docs/DISCLAIMER.md",
                tool_version: str = "0.0.1") -> str:
    # group by category, sort each group by tier
    by_cat: dict[Category, list[Finding]] = {}
    for f in findings:
        for cat in f.categories:
            by_cat.setdefault(cat, []).append(f)
    sections = []
    for cat in (Category.CLINICAL, Category.AGING, Category.TRAIT):
        rows = sorted(by_cat.get(cat, []), key=lambda f: _TIER_ORDER[f.tier])
        if not rows:
            continue
        body = "".join(_finding_row(f) for f in rows)
        sections.append(
            f"<h2>{_CAT_LABEL[cat]}</h2>"
            f"<table><thead><tr><th>Evidence</th><th>Marker</th>"
            f"<th>Finding</th><th>Source</th></tr></thead><tbody>{body}</tbody></table>")

    # provider status + reproducibility footer
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    status_rows = "".join(
        f"<li>{html.escape(s.name)}: {s.health.value}"
        + (f" — {html.escape(s.note)}" if s.note else "")
        + (f" (v{html.escape(str(s.version))})" if s.version else "") + "</li>"
        for s in provider_status)

    style = """
    body{font-family:system-ui,sans-serif;max-width:60em;margin:2em auto;color:#222}
    table{border-collapse:collapse;width:100%;margin:1em 0}
    th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
    .badge{padding:2px 8px;border-radius:10px;font-size:0.8em;font-weight:600}
    .badge.robust{background:#1a7f37;color:#fff}
    .badge.moderate{background:#bf8700;color:#fff}
    .badge.speculative{background:#8a8a8a;color:#fff}
    .badge.unknown{background:#eee;color:#666}
    .detail{color:#666;font-size:0.85em}
    .disclaimer{background:#f6f8fa;padding:1em;white-space:pre-wrap;font-family:inherit;border-radius:6px}
    footer{color:#666;font-size:0.85em;border-top:1px solid #ddd;margin-top:2em;padding-top:1em}
    """
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>MethylAsk report</title><style>{style}</style></head><body>
<h1>MethylAsk report</h1>
<p>{len(findings)} findings across {len(by_cat)} categories.</p>
{''.join(sections)}
<h2>About these results</h2>{_disclaimer_html(disclaimer_path)}
<footer><strong>Data sources at generation time</strong><ul>{status_rows}</ul>
Generated {now} · MethylAsk v{tool_version}</footer>
</body></html>"""


def to_pdf(html_str: str, out_path: str) -> None:
    """Render HTML -> PDF. Requires the 'report' extra (weasyprint)."""
    try:
        from weasyprint import HTML  # optional dependency
    except ImportError as e:
        raise RuntimeError("PDF output needs the 'report' extra: pip install methylask[report]") from e
    HTML(string=html_str).write_pdf(out_path)
