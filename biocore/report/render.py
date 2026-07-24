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

# Each tier occupies a magnitude band; the evidence stats only move a finding
# WITHIN its band, so a magnitude never contradicts its tier (a robust finding
# always outranks a speculative one). This is the Promethease-style 0-10 rank
# users expect, derived from what we already carry — not a new data source.
_MAG_BAND = {Tier.ROBUST: (7.0, 10.0), Tier.MODERATE: (4.0, 7.0),
             Tier.SPECULATIVE: (1.0, 4.0), Tier.UNKNOWN: (0.0, 1.0)}


def magnitude(f: Finding) -> float:
    """A 0-10 interest score. Tier picks the band; p-value, sample size and
    ClinVar review stars position the finding inside it. Rounded to one decimal."""
    import math
    lo, hi = _MAG_BAND.get(f.tier, (0.0, 1.0))
    d = f.detail or {}

    def num(*keys, default=None):
        for k in keys:
            v = d.get(k)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return default

    # position within band from the strongest available evidence signal (0..1)
    signals = []
    p = num("p", "pvalue", "p_value")
    if p is not None and p > 0:
        # p=1e-3 -> ~0.3, p=1e-10 -> ~1.0 (cap at 10 orders of magnitude)
        signals.append(min(1.0, -math.log10(p) / 10.0))
    n = num("n", "sample_size")
    if n is not None and n > 0:
        signals.append(min(1.0, math.log10(n + 1) / 5.0))   # n=100k -> 1.0
    stars = num("gold_stars")
    if stars is not None:
        signals.append(min(1.0, stars / 4.0))               # ClinVar 4-star -> 1.0
    frac = max(signals) if signals else 0.0
    return round(lo + (hi - lo) * frac, 1)
_CAT_LABEL = {Category.CLINICAL: "Clinical relevance",
              Category.AGING: "Aging &amp; wellness",
              Category.TRAIT: "Traits &amp; ancestry"}


def _disclaimer_html(disclaimer_path: str) -> str:
    """Render the single-source disclaimer as readable HTML.

    The file is light markdown: `## Heading` lines become subheadings, `- ` lines
    become bullets, blank lines separate paragraphs. This keeps the disclaimer a
    plain editable text file (one source of truth) while giving the reader real
    formatting instead of a preformatted block.
    """
    p = Path(disclaimer_path)
    if not p.exists():
        return "<div class='disclaimer'>See DISCLAIMER.md.</div>"
    out, bullets, para = [], [], []

    def flush_para():
        if para:
            out.append("<p>" + " ".join(html.escape(x) for x in para) + "</p>")
            para.clear()

    def flush_bullets():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{html.escape(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in p.read_text().splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_para(); flush_bullets(); continue
        if line.startswith("## "):
            flush_para(); flush_bullets()
            out.append(f"<h3>{html.escape(line[3:].strip())}</h3>")
        elif line.startswith("- "):
            flush_para(); bullets.append(line[2:].strip())
        else:
            flush_bullets(); para.append(line.strip())
    flush_para(); flush_bullets()
    return "<div class='disclaimer'>" + "".join(out) + "</div>"


def _pubmed_links(pmids: list[str]) -> str:
    if not pmids:
        return ""
    links = ", ".join(
        f"<a href='https://pubmed.ncbi.nlm.nih.gov/{html.escape(p)}/'>{html.escape(p)}</a>"
        for p in pmids)
    return f"<span class='refs'>study: {links}</span>"


# Only a few summary-stat fields mean anything to a human reader; the rest
# (se, chrpos, array type, …) are machine detail available at the source link.
_HUMAN_STAT_LABELS = {
    "p": "p-value", "pvalue": "p-value", "p_value": "p-value",
    "n": "sample size", "sample_size": "sample size",
    "tissue": "tissue", "trait": "trait", "gene": "gene",
    "beta": "effect (beta)", "effect": "effect",
}


def _fmt_stat(key: str, val) -> str:
    if key in ("p", "pvalue", "p_value"):
        try:
            return f"{float(val):.1e}"
        except (TypeError, ValueError):
            return str(val)
    if key in ("beta", "effect"):
        try:
            return f"{float(val):+.3f}"
        except (TypeError, ValueError):
            return str(val)
    return str(val)


def _study_details(f: Finding) -> str:
    """A compact 'study details' expander showing only the human-meaningful
    summary stats (p-value, sample size, tissue, effect). Full numbers live at
    the source link."""
    items = [(k, v) for k, v in f.detail.items()
             if k in _HUMAN_STAT_LABELS and v not in (None, "")]
    if not items:
        return ""
    rows = "".join(
        f"<tr><td>{html.escape(_HUMAN_STAT_LABELS[k])}</td>"
        f"<td>{html.escape(_fmt_stat(k, v))}</td></tr>" for k, v in items)
    return ("<details class='stats'><summary>study details</summary>"
            f"<table class='statgrid'>{rows}</table></details>")


def _entity_links(f: Finding) -> str:
    """Contextualizing linkouts for the entities named in a finding: gene ->
    NCBI Gene, protein -> UniProt. So a reader can click any named thing to a
    database that explains it, rather than seeing a bare symbol or code."""
    bits = []
    gene = f.detail.get("gene")
    if gene:
        # a gene field may be 'SYMBOL' or 'SYMBOL1;SYMBOL2' — link each
        for g in str(gene).replace(",", ";").split(";"):
            g = g.strip()
            if g and g != "?":
                bits.append(f"<a class='ent' href='https://www.ncbi.nlm.nih.gov/gene/?term="
                            f"{html.escape(g)}%5Bsym%5D'>{html.escape(g)}</a>")
    prot = f.detail.get("protein")
    if prot:
        bits.append(f"<a class='ent' href='https://www.uniprot.org/uniprotkb/"
                    f"{html.escape(str(prot))}'>{html.escape(str(prot))}</a>")
    return ("<span class='ents'>" + " · ".join(bits) + "</span>") if bits else ""


# Which layer of your DNA a finding comes from. Providers whose evidence is a
# methylation measurement are "methylome"; variant/genotype providers are
# "genome". `detail['modality']` overrides this if a provider sets it explicitly.
_METHYLOME_SOURCES = {"ewas_catalog", "gdc", "methbank", "ewas_atlas", "clocks"}
_GENOME_SOURCES = {"clinvar", "gwas_catalog", "geneask", "pharmgkb", "opengwas"}
_MODALITY_LABEL = {"methylome": "Methylome", "genome": "Genome"}


def _modality(f: Finding) -> str:
    m = (f.detail or {}).get("modality")
    if m:
        return m
    s = (f.source or "").lower()
    if s in _GENOME_SOURCES:
        return "genome"
    return "methylome"  # default: methylation-derived (the common case today)


def _finding_line(f: Finding) -> str:
    """One finding under its marker: a source-modality bubble, a plain-language
    sentence, a tier badge, entity linkouts, a linked citation, and the raw stats
    behind an expander."""
    tier_cls = f.tier.value
    modality = _modality(f)
    bubble = (f"<span class='mod mod-{modality}' title='{_MODALITY_LABEL[modality]} finding'>"
              f"{_MODALITY_LABEL[modality]}</span> ")
    src = (f"<a class='src' href='{html.escape(f.link)}'>{html.escape(f.source)}</a>"
           if f.link else f"<span class='src'>{html.escape(f.source)}</span>")
    meta_bits = [b for b in (_entity_links(f), _pubmed_links(f.pmids), src) if b]
    topic = html.escape(str(f.detail.get("topic", "other")))
    mag = magnitude(f)
    return (f"<li class='finding' data-tier='{tier_cls}' data-topic='{topic}' "
            f"data-modality='{modality}' data-mag='{mag}'>"
            f"{bubble}<span class='mag' title='Interest magnitude 0-10 "
            f"(tier + evidence strength)'>{mag:g}</span> "
            f"<span class='badge {tier_cls}'>{_TIER_LABEL[f.tier]}</span> "
            f"<span class='desc'>{html.escape(f.description)}</span>"
            f"<div class='meta'>{' · '.join(meta_bits)}</div>"
            f"{_study_details(f)}</li>")


def _strength_key(f: Finding):
    """Rank findings strongest-first: tier first, then the stats as a principled
    tiebreaker — smaller p-value and larger sample size mean stronger support."""
    import math
    d = f.detail
    def num(*keys, default=None):
        for k in keys:
            v = d.get(k)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return default
    p = num("p", "pvalue", "p_value", default=1.0)
    n = num("n", "sample_size", default=0.0)
    # lower tier-order first; then smaller p (use log10 so ties break sensibly);
    # then larger n. Negate n so ascending sort puts big samples first.
    try:
        logp = math.log10(p) if p and p > 0 else 0.0
    except ValueError:
        logp = 0.0
    return (_TIER_ORDER[f.tier], logp, -n)


def _marker_label(marker: str) -> str:
    """Human-readable marker for the card header. A CpG id (cg…) or rsID passes
    through; a long variant id 'chr-pos-ref-alt' (indels dump the whole sequence)
    is abbreviated to 'chr:pos ref→alt' with long alleles truncated, so a 331 bp
    deletion shows 'chr2:47403171 AGGAGG…→A' instead of a wall of DNA. The full id
    is still used for the linkout and search."""
    parts = marker.split("-")
    if len(parts) >= 4 and parts[1].isdigit():
        chrom, pos = parts[0], parts[1]
        ref, alt = parts[2], "-".join(parts[3:])
        def sh(a, keep=6):
            return a if len(a) <= keep + 3 else f"{a[:keep]}…({len(a)}bp)"
        return f"chr{chrom}:{pos} {sh(ref)}→{sh(alt)}"
    return marker


def _marker_card(marker: str, fs: list[Finding], marker_url) -> str:
    """One card per marker, gathering all findings about that marker. The marker
    id links out to a public record when the product supplies a resolver.
    Findings are ordered strongest-first (tier, then p-value, then sample size)."""
    fs = sorted(fs, key=_strength_key)
    url = marker_url(marker) if marker_url else None
    label = _marker_label(marker)
    head = (f"<a href='{html.escape(url)}'>{html.escape(label)}</a>"
            if url else html.escape(label))
    lines = "".join(_finding_line(f) for f in fs)
    n = len(fs)
    count = f"{n} finding" + ("s" if n != 1 else "")
    tiers = " ".join(sorted({f.tier.value for f in fs}))
    topics = " ".join(sorted({str(f.detail.get("topic", "other")) for f in fs}))
    top_mag = max(magnitude(f) for f in fs)   # card ranks by its strongest finding
    return (f"<div class='card' data-tiers='{tiers}' data-topics='{topics}' "
            f"data-mag='{top_mag}' data-marker='{html.escape(marker.lower())}'>"
            f"<div class='card-h'><span class='marker'>{head}</span>"
            f"<span class='card-meta'><span class='card-mag' title='Top interest "
            f"magnitude'>{top_mag:g}</span> · {count}</span></div>"
            f"<ul class='findings'>{lines}</ul></div>")


def render_html(findings: list[Finding],
                provider_status: list[ProviderStatus],
                disclaimer_path: str = "docs/DISCLAIMER.md",
                tool_version: str = "0.0.1",
                title: str = "Report",
                marker_url=None) -> str:
    """Render findings as a human report: grouped by category, then by marker
    (one card per marker), robust findings first. `marker_url(marker)->str|None`
    lets the product link a marker to a public record (bio-core stays domain-
    agnostic — it does not know CpG vs variant databases)."""
    # One card per marker (each marker appears exactly ONCE — no cross-category
    # duplication). A marker is placed under a single PRIMARY category = the
    # category of its best-tier finding, ties broken by precedence
    # (clinical > aging > trait).
    _CAT_PREC = {Category.CLINICAL: 0, Category.AGING: 1, Category.TRAIT: 2}
    by_marker: dict[str, list[Finding]] = {}
    for f in findings:
        by_marker.setdefault(f.marker, []).append(f)

    def primary_category(fs: list[Finding]) -> Category:
        best = min(_TIER_ORDER[f.tier] for f in fs)
        cats = [c for f in fs if _TIER_ORDER[f.tier] == best for c in f.categories]
        cats = cats or [c for f in fs for c in f.categories] or [Category.TRAIT]
        return min(cats, key=lambda c: _CAT_PREC.get(c, 9))

    cat_markers: dict[Category, list[tuple[str, list[Finding]]]] = {}
    for m, fs in by_marker.items():
        cat_markers.setdefault(primary_category(fs), []).append((m, fs))

    toc, sections = [], []
    for cat in (Category.CLINICAL, Category.AGING, Category.TRAIT):
        group = cat_markers.get(cat, [])
        if not group:
            continue
        anchor = cat.value
        markers = sorted(group, key=lambda kv: min(_TIER_ORDER[x.tier] for x in kv[1]))
        cards = "".join(_marker_card(m, fs, marker_url) for m, fs in markers)
        label = _CAT_LABEL[cat]
        toc.append(f"<li><a href='#{anchor}'>{label}</a> "
                   f"<span class='toc-n'>{len(markers)} markers</span></li>")
        sections.append(f"<section id='{anchor}'><h2>{label}</h2>{cards}</section>")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    status_rows = "".join(
        f"<li>{html.escape(s.name)}: {s.health.value}"
        + (f" — {html.escape(s.note)}" if s.note else "")
        + (f" (v{html.escape(str(s.version))})" if s.version else "") + "</li>"
        for s in provider_status)

    toc_html = (f"<nav class='toc'><strong>Jump to</strong><ul>{''.join(toc)}"
                "<li><a href='#about'>About these results</a></li></ul></nav>"
                if toc else "")

    style = """
    :root{--ink:#1a1a1a;--mut:#666;--line:#e4e4e2;--bg:#fbfbfa;--card:#fff;--accent:#2b6a5b;}
    @media(prefers-color-scheme:dark){:root{--ink:#eee;--mut:#a9a9a5;--line:#333;--bg:#141414;--card:#1d1d1c;--accent:#4bbf9f;}}
    body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;max-width:52em;
      margin:0 auto;padding:36px 22px 70px;color:var(--ink);background:var(--bg);line-height:1.6}
    h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:30px 0 12px}
    a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
    .toc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:18px 0 8px;font-size:14px}
    .toc ul{margin:8px 0 0;padding-left:18px}.toc li{margin:3px 0}.toc-n{color:var(--mut);font-size:12.5px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
    .card-h{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:8px}
    .marker{font-family:ui-monospace,Menlo,monospace;font-size:15px;font-weight:600}
    .card-meta{color:var(--mut);font-size:12.5px}
    ul.findings{list-style:none;margin:0;padding:0}
    .finding{padding:8px 0;border-top:1px solid var(--line)}.finding:first-child{border-top:none}
    .desc{font-size:15px}
    /* Evidence-strength ramp — a single indigo->violet hue that darkens with
       strength, deliberately NOT the green link/accent colour so the badges
       read as a strength scale and never blend into links. */
    .badge{padding:1px 9px;border-radius:10px;font-size:11px;font-weight:700;vertical-align:middle;letter-spacing:.01em}
    .badge.robust{background:#3b2f7a;color:#fff}       /* deep indigo  = strongest */
    .badge.moderate{background:#7c6bc4;color:#fff}     /* mid violet   = moderate  */
    .badge.speculative{background:#c9922b;color:#fff}  /* amber        = speculative */
    .badge.unknown{background:#e6e6e3;color:#555}      /* grey         = limited    */
    .mag{display:inline-block;min-width:22px;text-align:center;padding:1px 6px;border-radius:6px;
         font-size:11px;font-weight:700;background:#eef;color:#3b2f7a;vertical-align:middle;
         font-variant-numeric:tabular-nums}
    .card-mag{display:inline-block;min-width:20px;text-align:center;padding:0 6px;border-radius:6px;
         font-weight:700;background:#3b2f7a;color:#fff;font-variant-numeric:tabular-nums}
    @media(prefers-color-scheme:dark){.mag{background:#2a2450;color:#c9c0ff}}
    .meta{color:var(--mut);font-size:12.5px;margin-top:3px}
    .meta .src{margin-left:10px}
    details.stats{margin-top:6px;font-size:12.5px}
    details.stats summary{color:var(--mut);cursor:pointer}
    table.statgrid{border-collapse:collapse;margin:6px 0 2px;font-family:ui-monospace,Menlo,monospace;font-size:12px}
    table.statgrid td{border:1px solid var(--line);padding:2px 8px;color:var(--mut)}
    .disclaimer{background:var(--card);border:1px solid var(--line);padding:4px 20px 14px;font-family:inherit;border-radius:8px;font-size:14px}
    .disclaimer h3{font-size:14px;margin:16px 0 4px;color:var(--ink)}
    .disclaimer p{margin:4px 0}.disclaimer ul{margin:4px 0;padding-left:20px}.disclaimer li{margin:3px 0}
    footer{color:var(--mut);font-size:12.5px;border-top:1px solid var(--line);margin-top:30px;padding-top:14px}
    .controls{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
      padding:12px 0 10px;margin:8px 0 4px;display:flex;gap:18px;flex-wrap:wrap;align-items:center;z-index:5}
    .controls label{font-size:13px;color:var(--mut)}
    .controls select{font:inherit;font-size:13px;padding:5px 8px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
    .controls .switch{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--mut);cursor:pointer}
    .filtered-out{display:none}
    .stats-hidden details.stats{display:none}
    .count-note{font-size:12.5px;color:var(--mut)}
    /* source-modality bubble: which DNA layer the finding came from */
    .mod{display:inline-block;font-size:10px;font-weight:700;padding:1px 7px;border-radius:10px;
      vertical-align:middle;letter-spacing:.02em}
    .mod-methylome{background:#d8ecff;color:#1a5a99;border:1px solid #b9dbff}
    .mod-genome{background:#ffe4d1;color:#9a4a17;border:1px solid #ffd0b0}
    @media(prefers-color-scheme:dark){
      .mod-methylome{background:#123049;color:#7cc0ff;border-color:#1c496e}
      .mod-genome{background:#402312;color:#ffb27d;border-color:#5e3418}}
    """
    n_markers = len({f.marker for f in findings})

    # topics actually present, for the subject filter (nice labels, stable order)
    _TOPIC_LABEL = {"aging": "Aging", "cancer": "Cancer", "metabolic": "Metabolic",
                    "cardiovascular": "Cardiovascular", "immune": "Immune / inflammation",
                    "respiratory": "Respiratory", "neuro": "Neurological",
                    "reproductive": "Reproductive / developmental", "lifestyle": "Lifestyle",
                    "proteomic": "Protein levels", "other": "Other"}
    present = {str(f.detail.get("topic", "other")) for f in findings}
    topic_opts = "".join(
        f"<option value='{t}'>{_TOPIC_LABEL.get(t, t.title())}</option>"
        for t in _TOPIC_LABEL if t in present)

    # a source-modality filter, shown only when the report actually mixes
    # methylome + genome findings (a methylation-only report doesn't need it)
    mods_present = {_modality(f) for f in findings}
    modality_ctrl = ""
    if len(mods_present) > 1:
        opts = "".join(f"<option value='{m}'>{_MODALITY_LABEL[m]}</option>"
                       for m in ("methylome", "genome") if m in mods_present)
        modality_ctrl = ("<label>Source: <select id='modfilter'>"
                         f"<option value=''>Methylome + genome</option>{opts}</select></label>")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{style}</style></head><body>
<h1>{html.escape(title)}</h1>
<p>{len(findings)} findings across {n_markers} markers.</p>
<div class="controls">
  <label>Minimum evidence:
    <select id="evfilter">
      <option value="robust">Strongest only</option>
      <option value="robust moderate" selected>Strong &amp; moderate</option>
      <option value="robust moderate speculative unknown">All, incl. weak</option>
    </select>
  </label>
  <label>Subject:
    <select id="topicfilter"><option value="">All subjects</option>{topic_opts}</select>
  </label>
  {modality_ctrl}
  <label>Find marker:
    <input id="markersearch" type="search" placeholder="e.g. cg05575921" size="14">
  </label>
  <label class="switch"><input type="checkbox" id="stattoggle"> Show study statistics</label>
  <span class="count-note" id="countnote"></span>
</div>
{toc_html}
{''.join(sections)}
<section id="about"><h2>About these results</h2>{_disclaimer_html(disclaimer_path)}</section>
<footer><strong>Data sources at generation time</strong><ul>{status_rows}</ul>
Generated {now} · v{tool_version}</footer>
<script>
(function(){{
  var sel=document.getElementById('evfilter'),
      topic=document.getElementById('topicfilter'),
      mod=document.getElementById('modfilter'),
      search=document.getElementById('markersearch'),
      stat=document.getElementById('stattoggle'),
      note=document.getElementById('countnote');
  function applyFilter(){{
    var allow=new Set(sel.value.split(' '));
    var want=topic.value;                       // '' = all subjects
    var wantMod=mod?mod.value:'';               // '' = all sources
    var q=(search.value||'').trim().toLowerCase();
    var shown=0;
    document.querySelectorAll('.finding').forEach(function(f){{
      var ok=allow.has(f.getAttribute('data-tier'))
           && (!want || f.getAttribute('data-topic')===want)
           && (!wantMod || f.getAttribute('data-modality')===wantMod);
      f.classList.toggle('filtered-out',!ok); if(ok)shown++;
    }});
    document.querySelectorAll('.card').forEach(function(c){{
      var hasVisible=c.querySelector('.finding:not(.filtered-out)');
      var matchQ=!q || (c.getAttribute('data-marker')||'').indexOf(q)>=0;
      c.classList.toggle('filtered-out',!(hasVisible&&matchQ));
    }});
    document.querySelectorAll('section[id]').forEach(function(s){{
      if(s.id==='about')return;
      var any=s.querySelector('.card:not(.filtered-out)');
      s.classList.toggle('filtered-out',!any);
    }});
    note.textContent=shown+' findings shown';
  }}
  function applyStats(){{
    document.body.classList.toggle('stats-hidden',!stat.checked);
  }}
  sel.addEventListener('change',applyFilter);
  topic.addEventListener('change',applyFilter);
  if(mod)mod.addEventListener('change',applyFilter);
  search.addEventListener('input',applyFilter);
  stat.addEventListener('change',applyStats);
  applyFilter(); applyStats();
}})();
</script>
</body></html>"""


def to_pdf(html_str: str, out_path: str) -> None:
    """Render HTML -> PDF. Requires the 'report' extra (weasyprint)."""
    try:
        from weasyprint import HTML  # optional dependency
    except ImportError as e:
        raise RuntimeError("PDF output needs the 'report' extra: pip install methylask[report]") from e
    HTML(string=html_str).write_pdf(out_path)
