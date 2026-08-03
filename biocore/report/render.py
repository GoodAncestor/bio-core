# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
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
import html, re, datetime
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


def _mag_band(mag: float) -> str:
    """CSS band for a 0-10 magnitude, so the badge colour ramps with the score
    (a flat colour for every value would defeat a 0-10 scale). 5 steps."""
    if mag >= 8: return "m5"
    if mag >= 6: return "m4"
    if mag >= 4: return "m3"
    if mag >= 2: return "m2"
    return "m1"


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

    # Position within band from the strongest available evidence signal (0..1).
    #
    # p-value and sample size use a saturating curve x/(x+k) rather than a hard
    # cap. A linear cap flattened the top of the scale: with `-log10(p)/10`,
    # EVERY finding at p <= 1e-10 scored exactly 10.0, and GWAS Catalog hits
    # routinely sit far below that — so the one number meant to rank findings
    # stopped discriminating precisely among the strongest ones. The curve is
    # monotone over the whole plausible range (p=1e-5 to p=1e-300) and never
    # quite reaches 1.0, so a stronger study always outranks a weaker one.
    signals = []
    p = num("p", "pvalue", "p_value")
    if p is not None and p <= 0:
        # GWAS Catalog stores 0 when the reported p-value underflows float, i.e.
        # the strongest associations there are. Discarding it as out-of-range
        # dropped those to the band floor — the single most significant finding
        # in a report sorted below one with no statistics at all.
        signals.append(1.0)
    elif p is not None and 0 < p < 1:
        # k=8 anchors genome-wide significance (5e-8) near the middle:
        # p=1e-8 -> .50, 1e-20 -> .71, 1e-50 -> .86, 1e-200 -> .96
        x = -math.log10(p)
        signals.append(x / (x + 8.0))
    n = num("n", "sample_size")
    if n is not None and n > 0:
        # n=1e3 -> .50, 1e4 -> .57, 1e5 -> .63, 5e5 -> .66 (UK-Biobank scale)
        y = math.log10(n + 1)
        signals.append(y / (y + 3.0))
    stars = num("gold_stars")
    if stars is not None:
        # ClinVar review status is a genuinely bounded 0-4 scale, so it maps
        # directly — there is no tail here to saturate.
        signals.append(min(1.0, stars / 4.0))
    # clamp: a malformed detail value (a negative star count, say) must never
    # push a finding outside the band its tier guarantees
    frac = min(1.0, max(0.0, max(signals))) if signals else 0.0
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
            p = float(val)
            # Sources store 0 when the reported p-value underflows float — these
            # are the strongest associations, and _boost() already scores them
            # that way. "0.0e+00" reads as no significance and contradicts the
            # magnitude shown beside it.
            return "<1e-300" if p <= 0 else f"{p:.1e}"
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
            # "-", "NA" and "?" are the placeholders sources use for an
            # intergenic or unassigned probe; each renders as a symbol that
            # links nowhere and implies a gene the study never named.
            if g and g not in ("?", "-", "NA", "na", "N/A", "."):
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


# --- direction of effect ----------------------------------------------------
# Promethease's equivalent field is "repute": a good / bad / not-set judgement a
# wiki contributor assigns. We deliberately do NOT do that. A direction is shown
# only where the SOURCE ITSELF asserts one — ClinVar's own clinical significance
# — and is left unset everywhere else. A trait like lactase persistence is not
# "good", and a GWAS effect direction is not a verdict unless you have already
# decided whether more of the trait is desirable; inventing that valence is
# exactly the overclaiming this report exists to avoid.
#
# Labels state what the source asserts rather than how to feel about it:
# "Disease-associated", not "bad".
_DIR_LABEL = {
    "adverse":    ("Disease-associated", "ClinVar classifies this variant as causing or contributing to disease"),
    "protective": ("Protective", "ClinVar classifies this variant as protective"),
    "benign":     ("Not disease-causing", "ClinVar classifies this variant as benign"),
    "actionable": ("Affects medication", "This variant has documented drug-response implications"),
}


def direction(f: Finding) -> str:
    """Classify a finding's direction from ClinVar's clinical significance.

    Returns one of _DIR_LABEL's keys, or "" when the source states no direction.

    Precedence matters and is the whole subtlety here: ClinVar significance is a
    free-text, semicolon-joined field, so "Conflicting classifications of
    pathogenicity; other; risk factor" contains both "pathogenicity" and "risk
    factor" while asserting neither. Uncertainty is therefore checked FIRST and
    wins outright — a contested variant gets no direction at all rather than the
    scarier of its readings.
    """
    sig = str((f.detail or {}).get("clinical_significance", "")).strip().lower()
    if not sig:
        return ""
    # 1. Genuine uncertainty wins outright — a contested variant is never
    #    resolved into the scarier of its readings. Note "conflicting
    #    classifications of pathogenicity" contains "pathogenic", which is
    #    exactly why this is tested before the pathogenic check below.
    if any(k in sig for k in ("conflicting", "uncertain", "not provided",
                              "no classification")):
        return ""
    # 2. Disease association wins over every remaining modifier. ClinVar joins
    #    terms with ";", and "Affects" / "association" are MODIFIERS layered on
    #    a classification, not classifications themselves — so "Pathogenic;
    #    Affects" (a real value in the shipped panel: SLC26A4, Pendred syndrome)
    #    must stay adverse. Treating those two as uncertainty markers silently
    #    stripped the flag off a pathogenic variant AND hid it from the
    #    "Disease-associated" filter, which is the worst failure this function
    #    can have. "risk allele" is ClinVar's newer vocabulary alongside the
    #    older "risk factor"; both are assertions of elevated risk.
    if "pathogenic" in sig or "risk factor" in sig or "risk allele" in sig:
        return "adverse"
    # 3. Drug response before benign: "Benign; drug response" is not merely
    #    reassuring — the pharmacogenomic implication is the actionable part and
    #    must not be dropped in favour of the benign reading.
    if "drug response" in sig:
        return "actionable"
    if "protective" in sig:
        return "protective"
    if "benign" in sig:
        return "benign"
    # "Affects", "association" and "other" on their own asserts no direction.
    return ""


def _direction_badge(f: Finding) -> str:
    d = direction(f)
    if not d:
        return ""
    label, why = _DIR_LABEL[d]
    return (f"<span class='dir dir-{d}' title='{html.escape(why)}'>"
            f"{html.escape(label)}</span>")


def _modality(f: Finding) -> str:
    m = (f.detail or {}).get("modality")
    if m:
        return m
    s = (f.source or "").lower()
    if s in _GENOME_SOURCES:
        return "genome"
    return "methylome"  # default: methylation-derived (the common case today)


def glossary_anchor(copy_key: str) -> str:
    """Stable, URL-safe anchor id for a glossary entry. One place decides the
    id so the link and the entry cannot drift apart."""
    return "trait-" + re.sub(r"[^a-z0-9]+", "-", str(copy_key).lower()).strip("-")


def _glossary_link(f: Finding) -> str:
    """Link into the glossary when this finding's trait has curated copy.

    The explanation is per-TRAIT while findings are per-marker, so one trait can
    appear dozens of times on a page. The text lives once; each finding points at it.
    """
    key = (f.detail or {}).get("copy_key")
    if not key:
        return ""
    return (f"<a class='glosslink' href='#{html.escape(glossary_anchor(key))}'>"
            f"what this means</a>")


def _finding_line(f: Finding) -> str:
    """One finding: a magnitude gauge in the left rail, then the plain-language
    sentence as the hero, with tier, modality, entity linkouts and citation
    demoted to a metadata line beneath it.

    The sentence leads deliberately. An earlier layout opened every row with
    three coloured pills before any words, so the reader met the scoring before
    the finding — the scores are navigation aids and belong in a rail you can
    skim past, not in front of the thing you came to read.
    """
    tier_cls = f.tier.value
    modality = _modality(f)
    bubble = (f"<span class='mod mod-{modality}' title='{_MODALITY_LABEL[modality]} finding'>"
              f"{_MODALITY_LABEL[modality]}</span>")
    # friendly, attributed source name from the registry (falls back to the raw
    # source string for the person's own callset / unknown sources)
    from .sources import resolve as _resolve_source
    _s = _resolve_source(f.source or "")
    if _s:
        label = f"{_s.org} {_s.name}" if _s.org and _s.org not in _s.name else _s.name
        link = f.link or _s.url
        src = f"<a class='src' href='{html.escape(link)}' title='{html.escape(_s.license)}'>{html.escape(label)}</a>"
    elif f.link:
        src = f"<a class='src' href='{html.escape(f.link)}'>{html.escape(f.source)}</a>"
    else:
        src = f"<span class='src'>{html.escape(f.source)}</span>"
    tier_badge = (f"<span class='tier {tier_cls}'><i class='tdot'></i>"
                  f"{_TIER_LABEL[f.tier]}</span>")
    # direction leads the metadata line when present, because "is this variant
    # associated with disease" is the first thing a reader wants and it is set
    # on a minority of findings — rare enough that it stays meaningful
    meta_bits = [b for b in (_direction_badge(f), tier_badge, bubble,
                             _entity_links(f), _pubmed_links(f.pmids), src,
                             _glossary_link(f)) if b]
    topic = html.escape(str(f.detail.get("topic", "other")))
    mag = magnitude(f)
    return (f"<li class='finding' data-tier='{tier_cls}' data-topic='{topic}' "
            f"data-modality='{modality}' data-mag='{mag}' "
            f"data-direction='{direction(f)}'>"
            f"<div class='rail {_mag_band(mag)}' "
            f"title='Interest magnitude {mag:g} of 10 (evidence tier + study strength)'>"
            f"<span class='mag-n'>{mag:g}</span>"
            f"<span class='mag-bar'><i style='width:{mag * 10:.0f}%'></i></span></div>"
            f"<div class='body'><p class='desc'>{html.escape(f.description)}</p>"
            f"<div class='meta'>{' <span class=sep>·</span> '.join(meta_bits)}</div>"
            f"{_study_details(f)}</div></li>")


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
        # p == 0 means the reported value underflowed, i.e. maximally significant.
        # Treating it as log10(1)=0 sorted the strongest associations last.
        logp = math.log10(p) if p and p > 0 else (-400.0 if p == 0 else 0.0)
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
    # only worth saying when there is more than one — "1 FINDING" on every card
    # is noise repeated down the whole page
    count = f"{n} findings" if n != 1 else ""
    # The sample's own value is a property of the MARKER, so it belongs on the
    # card once. Every finding under a card carries it, and a busy card holds
    # dozens — restating one number down the whole list is the same noise the
    # count field above already avoids.
    reading = next((f.detail.get("your reading") for f in fs
                    if f.detail and f.detail.get("your reading") is not None), None)
    if reading is not None:
        count = " · ".join(x for x in (f"your reading {float(reading):.3f}", count) if x)
    tiers = " ".join(sorted({f.tier.value for f in fs}))
    topics = " ".join(sorted({str(f.detail.get("topic", "other")) for f in fs}))
    top_mag = max(magnitude(f) for f in fs)   # card ranks by its strongest finding
    return (f"<div class='card' data-tiers='{tiers}' data-topics='{topics}' "
            f"data-mag='{top_mag}' data-marker='{html.escape(marker.lower())}'>"
            f"<div class='card-h'><span class='marker'>{head}</span>"
            f"<span class='card-meta'>{count}</span></div>"
            f"<ul class='findings'>{lines}</ul></div>")


_DEFAULT_TOP_N = 15
# Cards shown per section before "show more". 756 findings on one page is the
# exact Promethease complaint this report exists to avoid, so the DEFAULT view
# must stop being a wall — not just a filter someone has to discover. 15 is a
# comfortable single screenful of specimen cards: enough to establish "this
# section has real content" without forcing a scroll marathon. We truncate
# whole marker CARDS, not individual findings within a card, because a card
# is the report's unit of "one thing about your genome" — splitting a card's
# findings across shown/hidden would fragment a single marker's story.
#
# One reveal per section, not paged in chunks: findings are already sorted
# strongest-first, so once someone commits to seeing more of a section they
# want the rest, not another wall five clicks deep. A single "show more" is
# also what stays correctly in sync with the live filter bar (see applyFilter
# in the generated script) without tracking a page cursor.


def render_html(findings: list[Finding],
                provider_status: list[ProviderStatus],
                disclaimer_path: str = "docs/DISCLAIMER.md",
                tool_version: str = "0.0.1",
                title: str = "Report",
                marker_url=None,
                scan_stats: dict | None = None,
                top_n: int = _DEFAULT_TOP_N) -> str:
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

    def _marker_rank(kv):
        # Strongest-first: best (lowest) tier order, then — WITHIN that tier —
        # highest magnitude. Sorting on tier alone left same-tier markers in
        # arbitrary (insertion) order, which is invisible when everything
        # renders, but becomes a real bug once we truncate: the "top N" must
        # actually be the N strongest, or the cards a reader never sees could
        # be stronger than ones left showing.
        _marker, fs = kv
        best_tier = min(_TIER_ORDER[x.tier] for x in fs)
        best_mag = max(magnitude(x) for x in fs)
        return (best_tier, -best_mag)

    n_top = max(0, top_n)
    toc, sections = [], []
    for cat in (Category.CLINICAL, Category.AGING, Category.TRAIT):
        group = cat_markers.get(cat, [])
        if not group:
            continue
        anchor = cat.value
        markers = sorted(group, key=_marker_rank)
        card_html = [_marker_card(m, fs, marker_url) for m, fs in markers]
        label = _CAT_LABEL[cat]
        toc.append(f"<li><a href='#{anchor}'>{label} "
                   f"<span class='toc-n'>{len(markers)}</span></a></li>")
        if len(card_html) > n_top:
            shown_html = "".join(card_html[:n_top])
            hidden_html = "".join(card_html[n_top:])
            hidden_n = len(card_html) - n_top
            # <details>/<summary> is a native disclosure widget: keyboard
            # operable and expandable with ZERO javascript, so the extra
            # cards are never permanently unreachable if the script fails to
            # load or is disabled. The script below only ENHANCES it (live
            # count that tracks the filter bar, "show fewer" on reopen).
            cards = (shown_html +
                     "<details class='more'><summary>"
                     f"Show {hidden_n} more</summary>"
                     f"<div class='findings-more'>{hidden_html}</div></details>")
        else:
            cards = "".join(card_html)
        sections.append(f"<section id='{anchor}'><h2>{label}</h2>{cards}</section>")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    status_rows = "".join(
        f"<li>{html.escape(s.name)}: {s.health.value}"
        + (f" — {html.escape(s.note)}" if s.note else "")
        + (f" (v{html.escape(str(s.version))})" if s.version else "") + "</li>"
        for s in provider_status)

    # data-sources attribution panel — friendly names + links + licenses for
    # every external source this report drew on (a license obligation for several).
    from .sources import sources_used
    used = sources_used(findings)
    if used:
        rows = "".join(
            f"<li><a href='{html.escape(s.url)}'><strong>{html.escape(s.name)}</strong></a>"
            f" — {html.escape(s.blurb)} <span class='src-lic'>({html.escape(s.org)}, "
            f"{html.escape(s.license)}{', non-commercial' if s.noncommercial else ''})</span></li>"
            for s in used)
        nc = any(s.noncommercial for s in used)
        nc_note = ("<p class='src-nc'>Some sources are used under non-commercial terms; "
                   "this tool is provided by a non-profit for non-commercial use.</p>" if nc else "")
        sources_panel = (f"<section id='sources'><h2>Data sources</h2>"
                         f"<p>Findings draw on these public resources — shown so you can "
                         f"see where each result comes from and follow it to the source:</p>"
                         f"<ul class='sources'>{rows}</ul>{nc_note}</section>")
    else:
        sources_panel = ""

    # scan-summary panel — makes the work visible: how much was scanned, what was
    # classified vs left uncertain, which local DBs + live APIs were consulted, and
    # (importantly for genomic data) that the uploaded file is deleted after the run.
    scan_html = ""
    if scan_stats:
        ss = scan_stats
        def _human_n(n):
            if n >= 1_000_000: return f"{n/1_000_000:.0f}M"
            if n >= 1_000: return f"{n/1_000:.0f}k"
            return str(n)
        def _human_bytes(b):
            for unit in ("B", "KB", "MB", "GB"):
                if b < 1024: return f"{b:.0f} {unit}"
                b /= 1024
            return f"{b:.0f} TB"
        tiles = []
        if ss.get("markers_scanned"):
            tiles.append(("Markers analysed", _human_n(ss["markers_scanned"])))
        tiles.append(("Findings", str(ss.get("findings_total", 0))))
        if ss.get("classified") is not None:
            tiles.append(("Classified", str(ss["classified"])))
        if ss.get("uncertain"):
            tiles.append(("Uncertain / novel", str(ss["uncertain"])))
        if ss.get("reference_variants_scanned"):
            tiles.append(("Reference records scanned", _human_n(ss["reference_variants_scanned"])))
        if ss.get("input_bytes"):
            tiles.append(("Your file", _human_bytes(ss["input_bytes"])))
        tile_html = "".join(
            f"<div class='stat'><span class='stat-n'>{html.escape(v)}</span>"
            f"<span class='stat-l'>{html.escape(l)}</span></div>" for l, v in tiles)
        dbs = ss.get("local_dbs_queried") or []
        apis = ss.get("live_apis_called") or []
        consulted = []
        if dbs:
            consulted.append("Local databases queried: " + ", ".join(html.escape(d) for d in dbs))
        if apis:
            consulted.append("Live services called: " + ", ".join(html.escape(a) for a in apis))
        consulted_html = ("<p class='scan-consulted'>" + " · ".join(consulted) + "</p>") if consulted else ""
        scan_html = (f"<section class='scan'><div class='stats'>{tile_html}</div>"
                     f"{consulted_html}"
                     f"<p class='scan-privacy'>&#128274; Your uploaded file is processed and then "
                     f"deleted — it is not retained after this report is generated.</p></section>")

    toc_html = (f"<nav class='toc'><strong>Contents</strong><ul>{''.join(toc)}"
                + ("<li><a href='#sources'>Data sources</a></li>" if used else "")
                + "<li><a href='#about'>How to read this</a></li></ul></nav>"
                if toc else "")

    # Specimen-plate house style, shared with the product's front door: a warm
    # paper ground, a serif for anything meant to be READ, a sans for controls
    # and metadata, hairline rules instead of boxes-within-boxes, and exactly one
    # accent. The report and the upload page are one printed object, not two apps.
    #
    # Every value is inline and locally-resolvable: no webfont, no stylesheet, no
    # image is fetched from anywhere. A report is generated from someone's genome
    # and may be opened offline or years later; it must not phone home to render.
    style = """
    :root{
      --paper:#f7f5ef; --card:#fffdf8; --ink:#1b1c18; --mut:#6b6a61; --faint:#939186;
      --line:#ddd9cc; --hair:#c9c4b3; --accent:#2b6a5b; --accent-soft:#e6efe9;
      --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Charter,Georgia,"Times New Roman",serif;
      --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif;
      --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
      --robust:#0c7a54; --moderate:#3d7ea6; --speculative:#b8860b; --unknown:#8a8a8a;
    }
    @media(prefers-color-scheme:dark){:root{
      --paper:#14150f; --card:#1c1e17; --ink:#ecebe2; --mut:#9e9d92; --faint:#7b7a70;
      --line:#32342a; --hair:#3d3f33; --accent:#63c2a2; --accent-soft:#1d2a24;
      --robust:#3fbb8a; --moderate:#6fb6dd; --speculative:#d6a63c; --unknown:#8f8f88;
    }}
    *{box-sizing:border-box}
    body{font-family:var(--sans);max-width:56em;margin:0 auto;
      padding:44px 26px 80px;color:var(--ink);background:var(--paper);line-height:1.65}
    h1{font:400 clamp(30px,5vw,44px)/1.05 var(--serif);letter-spacing:-.02em;margin:0}
    h2{font:400 25px/1.2 var(--serif);margin:44px 0 4px;letter-spacing:-.01em}
    a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
    .sub{color:var(--mut);font:400 18px/1.5 var(--serif);margin:10px 0 0}
    .h2sub{color:var(--faint);font-size:13px;margin:0 0 16px}
    /* section headings sit under a hairline, like a printed plate caption */
    section[id]>h2{padding-bottom:8px;border-bottom:1px solid var(--hair)}

    /* --- scan summary ------------------------------------------------- */
    .scan{margin:26px 0 0}
    .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:1px;
      background:var(--line);border:1px solid var(--line);border-radius:3px;overflow:hidden}
    .stat{background:var(--card);padding:14px 16px}
    .stat-n{display:block;font:400 27px/1.1 var(--serif);color:var(--ink);
      font-variant-numeric:tabular-nums}
    .stat-l{display:block;font-size:11px;color:var(--faint);margin-top:5px;
      letter-spacing:.11em;text-transform:uppercase}
    .scan-consulted{font-size:12.5px;color:var(--mut);margin:12px 0 0}
    .scan-privacy{font-size:13.5px;color:var(--mut);margin:10px 0 0;
      padding-left:14px;border-left:2px solid var(--accent)}

    /* --- evidence mix bar ---------------------------------------------
       Orientation before immersion: how the findings are distributed across
       evidence tiers, so nobody has to scroll 200 cards to learn that almost
       all of them are weak. */
    .mix{margin:24px 0 0}
    .mixbar{display:flex;height:9px;border-radius:5px;overflow:hidden;background:var(--line)}
    .mixbar i{display:block;height:100%}
    .mixkey{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0 0;font-size:12.5px;color:var(--mut)}
    .mixkey span{display:flex;align-items:center;gap:6px}
    .mixkey b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}

    /* --- controls ------------------------------------------------------ */
    .controls{position:sticky;top:0;background:var(--paper);border-bottom:1px solid var(--hair);
      padding:14px 0 12px;margin:34px 0 0;display:flex;gap:20px;flex-wrap:wrap;
      align-items:center;z-index:5}
    .controls label{font-size:12.5px;color:var(--faint);display:flex;align-items:center;gap:7px;
      letter-spacing:.02em}
    .controls select,.controls input[type=search]{font:inherit;font-size:13px;padding:6px 9px;
      border-radius:3px;border:1px solid var(--hair);background:var(--card);color:var(--ink)}
    .controls select:focus,.controls input:focus{outline:2px solid var(--accent);outline-offset:1px}
    .controls input[type=range]{accent-color:var(--accent);width:104px;vertical-align:middle}
    .controls .switch{cursor:pointer}
    .magval{font-family:var(--mono);font-size:12px;color:var(--ink);min-width:2.1em}
    .count-note{font-size:12.5px;color:var(--faint);margin-left:auto}

    /* --- contents ------------------------------------------------------ */
    .toc{margin:26px 0 0;font-size:14px}
    .toc strong{font:600 11px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;
      color:var(--faint);display:block;margin:0 0 10px}
    .toc ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px 10px}
    .toc li{margin:0}
    .toc a{display:inline-block;padding:6px 12px;border:1px solid var(--line);border-radius:3px;
      background:var(--card);transition:border-color .15s ease}
    .toc a:hover{border-color:var(--accent);text-decoration:none}
    .toc-n{color:var(--faint);font-size:12px}

    /* --- marker cards --------------------------------------------------- */
    .card{background:var(--card);border:1px solid var(--line);border-radius:3px;
      padding:0 18px 4px;margin:12px 0;
      box-shadow:0 1px 0 rgba(0,0,0,.02),0 10px 26px -24px rgba(0,0,0,.4)}
    .card-h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
      padding:11px 0 9px;border-bottom:1px solid var(--line)}
    /* the marker id is provenance, not the headline — small, quiet, monospaced */
    .marker{font-family:var(--mono);font-size:12px;color:var(--mut);letter-spacing:.01em}
    .marker a{color:var(--mut)}.marker a:hover{color:var(--accent)}
    .card-meta{color:var(--faint);font-size:11.5px;white-space:nowrap;
      letter-spacing:.08em;text-transform:uppercase}
    ul.findings{list-style:none;margin:0;padding:0}
    .finding{display:grid;grid-template-columns:46px minmax(0,1fr);gap:16px;
      padding:15px 0;border-top:1px solid var(--line)}
    .finding:first-child{border-top:none}

    /* magnitude gauge: the number plus a 0-10 fill bar, in a fixed left rail so
       the eye can run straight down the scores without them interrupting the
       sentences. Ramps grey -> accent with strength. */
    .rail{padding-top:2px}
    .mag-n{display:block;font:400 19px/1 var(--serif);font-variant-numeric:tabular-nums;
      color:var(--mut)}
    .mag-bar{display:block;height:3px;margin-top:6px;background:var(--line);border-radius:2px;
      overflow:hidden}
    .mag-bar i{display:block;height:100%;background:var(--mut);border-radius:2px}
    .m4 .mag-n,.m5 .mag-n{color:var(--accent)}
    .m4 .mag-bar i,.m5 .mag-bar i{background:var(--accent)}
    .m3 .mag-n{color:var(--ink)}.m3 .mag-bar i{background:var(--hair)}

    /* the sentence is the hero: serif, comfortable size, nothing before it */
    .desc{font:400 16.5px/1.5 var(--serif);margin:0;color:var(--ink)}
    .meta{color:var(--mut);font-size:12.5px;margin-top:7px;display:flex;flex-wrap:wrap;
      gap:0 7px;align-items:center}
    .meta .sep{color:var(--hair)}
    /* tier as a dot + word rather than a filled pill — three saturated pills per
       row turned every finding into badge soup */
    .tier{display:inline-flex;align-items:center;gap:5px;font-weight:600;color:var(--ink)}
    .tdot{width:7px;height:7px;border-radius:50%;background:var(--unknown);display:inline-block}
    .tier.robust .tdot{background:var(--robust)} .tier.moderate .tdot{background:var(--moderate)}
    .tier.speculative .tdot{background:var(--speculative)} .tier.unknown .tdot{background:var(--unknown)}
    /* Direction of effect, shown only where ClinVar states one. This IS a filled
       chip — the single loudest thing in a row — because it is set on a minority
       of findings and is the one field a worried reader is actually looking for.
       Muted grounds, not alarm colours: this is a classification, not a warning. */
    .dir{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.04em;
      padding:2px 8px;border-radius:2px;text-transform:uppercase}
    .dir-adverse{background:#f3e3d8;color:#8a4b2a;box-shadow:inset 0 0 0 1px #e3cbb9}
    .dir-protective,.dir-benign{background:#dfeee7;color:#1f6a52;box-shadow:inset 0 0 0 1px #c3ded2}
    .dir-actionable{background:#dde8f2;color:#2b5f85;box-shadow:inset 0 0 0 1px #c2d8e8}
    @media(prefers-color-scheme:dark){
      .dir-adverse{background:#382215;color:#e8a982;box-shadow:inset 0 0 0 1px #533223}
      .dir-protective,.dir-benign{background:#14291f;color:#6cc6a2;box-shadow:inset 0 0 0 1px #244635}
      .dir-actionable{background:#152634;color:#7fb6de;box-shadow:inset 0 0 0 1px #234559}}

    /* which DNA layer the finding came from. Tinted text rather than a filled
       pill: the distinction is worth seeing but not worth shouting, and three
       saturated pills per row is what made the old rows unreadable. */
    .mod{font-size:10.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
    .mod-methylome{color:#3d7ea6}
    .mod-genome{color:#a8574f}
    @media(prefers-color-scheme:dark){
      .mod-methylome{color:#7cc0ff} .mod-genome{color:#e09a90}}
    details.stats{margin-top:8px;font-size:12.5px}
    details.stats summary{color:var(--faint);cursor:pointer;font-size:12px;
      letter-spacing:.04em;text-transform:uppercase}
    details.stats summary:hover{color:var(--accent)}
    table.statgrid{border-collapse:collapse;margin:8px 0 2px;font-family:var(--mono);font-size:12px}
    table.statgrid td{border:1px solid var(--line);padding:3px 9px;color:var(--mut)}

    /* --- sources + disclaimer ------------------------------------------- */
    .sources{list-style:none;margin:12px 0 0;padding:0;font-size:14px}
    .sources li{margin:0;padding:11px 0;border-bottom:1px solid var(--line);color:var(--mut)}
    .sources li:last-child{border-bottom:0}
    .src-lic{color:var(--faint);font-size:12.5px}
    .src-nc{color:var(--faint);font-size:13px;font-style:italic;margin-top:12px}
    .disclaimer{font-size:14.5px;color:var(--mut);margin-top:14px}
    .disclaimer h3{font:600 11px/1 var(--sans);letter-spacing:.14em;text-transform:uppercase;
      margin:22px 0 8px;color:var(--faint)}
    .disclaimer p{margin:8px 0}
    .disclaimer ul{margin:8px 0;padding-left:20px}.disclaimer li{margin:5px 0}
    footer{color:var(--faint);font:11.5px/1.8 var(--mono);border-top:1px solid var(--hair);
      margin-top:46px;padding-top:16px}
    footer ul{margin:6px 0;padding-left:18px}
    footer strong{color:var(--mut);font-weight:400;letter-spacing:.1em;text-transform:uppercase}

    /* --- progressive disclosure (top-N cards per section) --------------
       Default view shows only the strongest N marker cards per section — a
       report with hundreds of findings must not open as a wall (the single
       biggest, most-repeated complaint about the raw-dump tools this product
       replaces). The rest of the cards stay in the DOM inside a native
       <details> so in-page search and the filter bar still reach them; only
       their on-screen visibility is deferred. */
    details.more{margin:18px 0 26px}
    details.more summary{cursor:pointer;list-style:none;display:inline-flex;
      align-items:center;gap:8px;font:600 12.5px/1 var(--sans);letter-spacing:.03em;
      color:var(--accent);background:var(--accent-soft);border:1px solid var(--hair);
      border-radius:3px;padding:9px 15px;user-select:none}
    details.more summary::-webkit-details-marker{display:none}
    details.more summary::marker{content:''}
    details.more summary::after{content:'';width:6px;height:6px;
      border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;
      transform:rotate(45deg);margin-top:-4px;transition:transform .15s ease}
    details.more[open] summary::after{transform:rotate(-135deg);margin-top:2px}
    details.more summary:hover{border-color:var(--accent)}
    details.more summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
    .findings-more{margin-top:12px}
    @media print{
      /* the truncated view is a screen convenience only — a printed or
         PDF-saved report should read as the complete document */
      details.more{border:none;margin:0}
      details.more summary{display:none}
      details.more:not([open])>*:not(summary){display:block !important}
    }

    .filtered-out{display:none}
    .stats-hidden details.stats{display:none}
    .empty-note{margin:26px 0;padding:16px 18px;background:var(--card);
      border:1px solid var(--line);border-left:3px solid var(--speculative);
      border-radius:3px;font-size:14.5px;color:var(--mut)}
    @media(max-width:560px){
      .finding{grid-template-columns:38px minmax(0,1fr);gap:12px}
      .count-note{margin-left:0}
    }
    @media(prefers-reduced-motion:reduce){*{transition:none !important}}
    @media print{
      body{max-width:none;padding:0;background:#fff;color:#000}
      .controls,.toc{display:none}
      .card{break-inside:avoid;box-shadow:none}
    }
    """
    n_markers = len({f.marker for f in findings})

    # Evidence-mix bar: the shape of the whole report in one line, so a reader
    # knows before scrolling whether they are looking at three robust findings or
    # two hundred speculative ones. Counts, not just proportions — a 2% sliver is
    # unreadable as a width but matters as a number.
    mix_html = ""
    if findings:
        counts = {t: sum(1 for f in findings if f.tier is t) for t in _TIER_ORDER}
        total = sum(counts.values()) or 1
        segs, keys = [], []
        for tier in (Tier.ROBUST, Tier.MODERATE, Tier.SPECULATIVE, Tier.UNKNOWN):
            n = counts.get(tier, 0)
            if not n:
                continue
            pct = n * 100.0 / total
            segs.append(f"<i style='width:{pct:.2f}%;background:var(--{tier.value})' "
                        f"title='{_TIER_LABEL[tier]}: {n}'></i>")
            keys.append(f"<span><i class='tdot' style='background:var(--{tier.value})'></i>"
                        f"{_TIER_LABEL[tier]} <b>{n}</b></span>")
        mix_html = (f"<div class='mix'><div class='mixbar' role='img' "
                    f"aria-label='Evidence tier distribution across {total} findings'>"
                    f"{''.join(segs)}</div><div class='mixkey'>{''.join(keys)}</div></div>")

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

    # significance filter — only rendered when the report actually contains
    # findings ClinVar has classified, so a methylation-only or traits-only
    # report doesn't grow a control that would match nothing
    dirs_present = [d for d in ("adverse", "benign", "protective", "actionable")
                    if any(direction(f) == d for f in findings)]
    direction_ctrl = ""
    if dirs_present:
        opts = "".join(f"<option value='{d}'>{_DIR_LABEL[d][0]}</option>"
                       for d in dirs_present)
        direction_ctrl = ("<label>Significance <select id='dirfilter'>"
                          f"<option value=''>Any</option>{opts}</select></label>")

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
<p class="sub">{len(findings)} findings across {n_markers} markers, ordered by how much
evidence stands behind each one.</p>
{scan_html}
{mix_html}
<div class="controls">
  <label>Evidence
    <select id="evfilter">
      <option value="robust">Strongest only</option>
      <option value="robust moderate" selected>Strong &amp; moderate</option>
      <option value="robust moderate speculative unknown">All, incl. weak</option>
    </select>
  </label>
  <label>Min. magnitude
    <input id="magfilter" type="range" min="0" max="10" step="1" value="0">
    <span class="magval" id="magval">0</span>
  </label>
  {direction_ctrl}
  <label>Subject
    <select id="topicfilter"><option value="">All subjects</option>{topic_opts}</select>
  </label>
  {modality_ctrl}
  <label>Find
    <input id="markersearch" type="search" placeholder="gene, rsID or trait" size="16">
  </label>
  <label class="switch"><input type="checkbox" id="stattoggle"> Study statistics</label>
  <span class="count-note" id="countnote"></span>
</div>
<p class="empty-note" id="emptynote" style="display:none">No findings match these filters.
Widen the evidence setting or lower the minimum magnitude to see more.</p>
{toc_html}
{''.join(sections)}
{sources_panel}
<section id="about"><h2>About these results</h2>{_disclaimer_html(disclaimer_path)}</section>
<footer><strong>Data sources at generation time</strong><ul>{status_rows}</ul>
Generated {now} · v{tool_version}</footer>
<script>
(function(){{
  var sel=document.getElementById('evfilter'),
      topic=document.getElementById('topicfilter'),
      mod=document.getElementById('modfilter'),
      dir=document.getElementById('dirfilter'),
      search=document.getElementById('markersearch'),
      mag=document.getElementById('magfilter'),
      magval=document.getElementById('magval'),
      stat=document.getElementById('stattoggle'),
      note=document.getElementById('countnote'),
      empty=document.getElementById('emptynote'),
      cards=[].slice.call(document.querySelectorAll('.card')),
      moreDetails=[].slice.call(document.querySelectorAll('details.more'));

  // Search covers everything the card SAYS — gene symbol, condition, trait
  // wording, rsID — not just the marker id, because people arrive looking for
  // "BRCA" or "lactose", not for cg05575921. Indexed once; the text is static.
  cards.forEach(function(c){{ c._hay=(c.textContent||'').toLowerCase(); }});

  function applyFilter(){{
    var allow=new Set(sel.value.split(' '));
    var want=topic.value;                       // '' = all subjects
    var wantMod=mod?mod.value:'';               // '' = all sources
    var wantDir=dir?dir.value:'';               // '' = any significance
    var minMag=parseFloat(mag.value)||0;
    var q=(search.value||'').trim().toLowerCase();
    var shown=0;
    magval.textContent=minMag;
    document.querySelectorAll('.finding').forEach(function(f){{
      var ok=allow.has(f.getAttribute('data-tier'))
           && (!want || f.getAttribute('data-topic')===want)
           && (!wantMod || f.getAttribute('data-modality')===wantMod)
           && (!wantDir || f.getAttribute('data-direction')===wantDir)
           && parseFloat(f.getAttribute('data-mag')||0)>=minMag;
      f.classList.toggle('filtered-out',!ok);
      if(ok){{
        // A card collapsed inside a closed "show more" is not on screen —
        // it must not inflate the "N findings shown" count, or the counter
        // and the page visibly disagree (the #1 risk in this feature: 10
        // cards on screen while the counter claims hundreds are "shown").
        var det=f.closest('details.more');
        if(!det||det.open)shown++;
      }}
    }});
    var visibleCards=0;
    cards.forEach(function(c){{
      var hasVisible=c.querySelector('.finding:not(.filtered-out)');
      var matchQ=!q || c._hay.indexOf(q)>=0;
      var vis=!!(hasVisible&&matchQ);
      c.classList.toggle('filtered-out',!vis); if(vis)visibleCards++;
    }});
    document.querySelectorAll('section[id]').forEach(function(s){{
      if(s.id==='about'||s.id==='sources')return;
      var any=s.querySelector('.card:not(.filtered-out)');
      s.classList.toggle('filtered-out',!any);
    }});
    // Keep each section's "show more" control honest: it should count how
    // many of ITS hidden cards still match the active filters, not the raw
    // total computed at render time — expanding it must reveal exactly what
    // it promises, under whatever filters are live right now.
    moreDetails.forEach(function(det){{
      var inner=[].slice.call(det.querySelectorAll('.card'));
      var matching=inner.filter(function(c){{return !c.classList.contains('filtered-out');}}).length;
      var summary=det.querySelector('summary');
      if(!summary)return;
      if(det.open){{
        summary.textContent='Show fewer';
      }} else {{
        summary.textContent=matching?('Show '+matching+' more'):'No further matches below';
      }}
    }});
    note.textContent=shown+(shown===1?' finding':' findings')+' shown';
    empty.style.display=visibleCards?'none':'';
  }}
  function applyStats(){{
    document.body.classList.toggle('stats-hidden',!stat.checked);
  }}
  sel.addEventListener('change',applyFilter);
  topic.addEventListener('change',applyFilter);
  if(mod)mod.addEventListener('change',applyFilter);
  if(dir)dir.addEventListener('change',applyFilter);
  search.addEventListener('input',applyFilter);
  mag.addEventListener('input',applyFilter);
  stat.addEventListener('change',applyStats);
  // Re-sync the shown-count and every section's "show more" label the
  // instant a reader opens or closes one — native <details> already makes
  // this keyboard accessible (Tab + Enter/Space), this just keeps the rest
  // of the page's numbers honest when they do.
  moreDetails.forEach(function(det){{ det.addEventListener('toggle',applyFilter); }});
  // Printing (or "save as PDF") should read as the complete report, not the
  // truncated screen view. The print stylesheet already forces closed
  // <details> content visible as a CSS-only fallback (so this still works if
  // JS never runs); this belt-and-suspenders pass additionally flips the
  // real `open` state before print so browsers whose print engine re-derives
  // layout from element state (not just computed style) still get it right,
  // then restores whatever the reader had open afterward.
  window.addEventListener('beforeprint',function(){{
    moreDetails.forEach(function(d){{ d.dataset.wasOpen=d.open?'1':''; d.open=true; }});
  }});
  window.addEventListener('afterprint',function(){{
    moreDetails.forEach(function(d){{ d.open=!!d.dataset.wasOpen; }});
    applyFilter();
  }});
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
