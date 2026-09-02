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
from .terms import term_link, terms_html

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
              Category.TRAIT: "Traits &amp; ancestry",
              Category.REFERENCE: "Reference biology"}


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


def _modality_breakdown(findings) -> str:
    """Findings split by source modality, with why the two counts differ.

    A combined report can carry several hundred methylome findings against a
    handful of genome ones, and a single "Findings" total hides that completely.
    The reader sees two lonely genome cards after a wall of methylome ones and
    concludes the genome half failed — which is the wrong conclusion, and the
    report gave them nothing to reach a better one.

    The counts are not comparable quantities, so the split says so rather than
    printing two numbers side by side and letting the larger one look like the
    healthy result.
    """
    counts = {}
    for f in findings:
        m = _modality(f)
        counts[m] = counts.get(m, 0) + 1
    if len(counts) < 2:
        return ""
    parts = " · ".join(
        f"<strong>{html.escape(_MODALITY_LABEL[m])}</strong> {counts[m]}"
        for m in ("methylome", "genome") if m in counts)
    return (f"<p class='scan-mods'>Findings by source — {parts}. "
            f"These are not comparable counts: methylome findings are trait "
            f"associations reported from population studies, while genome findings "
            f"are only variants a curated clinical database considers significant. "
            f"A small number of genome findings is the ordinary result, not a "
            f"failed scan.</p>")


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


def _linked_description(f: Finding) -> tuple[str, bool]:
    """(escaped description html, linked?).

    Links the trait phrase where it appears in the sentence, because that is
    where the reader is already looking — a "what this means" link at the tail of
    the metadata row is the least visible element on the line.

    Matched against detail["subject"], the exact phrase the provider built the
    sentence from, rather than by searching the prose for a trait name: prose
    matching would mis-anchor on a trait whose name recurs later in the sentence.
    """
    desc = f.description or ""
    key = (f.detail or {}).get("copy_key")
    subject = (f.detail or {}).get("subject")
    if not key or not subject or not desc.startswith(subject):
        return html.escape(desc), False
    rest = desc[len(subject):]
    return (f"<a class='glossword' href='#{html.escape(glossary_anchor(key))}' "
            f"title='What this means'>{html.escape(subject)}</a>"
            f"{html.escape(rest)}"), True


def _predicted_by(f: Finding) -> list:
    """The prediction models that annotated this finding, if any.

    A prediction and a catalogue entry read the same way in a sentence — both
    arrive as "this variant does X" — but they are different kinds of claim. One
    is a record of something measured or curated; the other is a model's output
    for a variant nobody has studied. That distinction is why the enrichment
    exists at all (it covers the variants the catalogues cannot resolve), so the
    reader has to be able to see it."""
    from .sources import enrichments_used
    return [s for s in enrichments_used(f) if s.predicted]


def _predicted_badge(f: Finding) -> str:
    models = _predicted_by(f)
    if not models:
        return ""
    names = ", ".join(s.name for s in models)
    # html.escape(quote=True) covers the apostrophe too, which matters: these
    # attributes are single-quoted, and a name or sentence containing one would
    # otherwise close the attribute early and corrupt the markup.
    title = html.escape(f"Predicted from DNA sequence by {names}.", quote=True)
    return (f"<span class='pred' title='{title}'>"
            f"predicted · {html.escape(names, quote=True)}</span>")


def _finding_line(f: Finding, *, hoist_mean: bool = False) -> str:
    """One finding: a magnitude gauge in the left rail, then the plain-language
    sentence as the hero, with tier, modality, entity linkouts and citation
    demoted to a metadata line beneath it.

    The sentence leads deliberately. An earlier layout opened every row with
    three coloured pills before any words, so the reader met the scoring before
    the finding — the scores are navigation aids and belong in a rail you can
    skim past, not in front of the thing you came to read.
    """
    if f.interpretation is not None:
        return _meaning_line(f, hoist_mean=hoist_mean)
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
    tier_badge = _tier_badge(f)
    # direction leads the metadata line when present, because "is this variant
    # associated with disease" is the first thing a reader wants and it is set
    # on a minority of findings — rare enough that it stays meaningful
    desc_html, inline_linked = _linked_description(f)
    # only offer the tail link when the sentence itself could not carry it,
    # so a reader never sees two links to the same glossary entry
    meta_bits = [b for b in (_direction_badge(f), tier_badge, bubble,
                             _predicted_badge(f),
                             _entity_links(f), _pubmed_links(f.pmids), src,
                             "" if inline_linked else _glossary_link(f)) if b]
    topic = html.escape(str(f.detail.get("topic", "other")))
    mag = magnitude(f)
    return (f"<li class='finding' data-tier='{tier_cls}' data-topic='{topic}' "
            f"data-modality='{modality}' data-mag='{mag}' "
            f"data-predicted='{'1' if _predicted_by(f) else '0'}' "
            f"data-direction='{direction(f)}' "
            f"data-carried='{'0' if f.detail.get('risk_allele_carried') is False else '1'}'>"
            f"<div class='rail {_mag_band(mag)}' "
            f"title='Interest magnitude {mag:g} of 10 (evidence tier + study strength)'>"
            f"<span class='mag-n'>{mag:g}</span>"
            f"<span class='mag-bar'><i style='width:{mag * 10:.0f}%'></i></span></div>"
            f"<div class='body'><p class='desc'>{desc_html}</p>"
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


# What each source's tier actually measured. "Robust" means review stars for
# ClinVar and a p-value for a GWAS row; one word for both misleads, so the
# badge's tooltip names the scale.
_TIER_MEASURE = {"clinvar": "review stars", "clinvar_mirror": "review stars",
                 "clinvar_panel_157": "review stars", "gwas_catalog": "p-value",
                 "ewas_catalog": "sample size and p-value",
                 "gdc": "effect size and sample counts", "cpic": "CPIC evidence level"}


def _tier_badge(f: Finding) -> str:
    what = _TIER_MEASURE.get(f.source or "", "the source's own evidence scale")
    return (f"<span class='tier {f.tier.value}' title='{_TIER_LABEL[f.tier]}: {what}'>"
            f"<i class='tdot'></i>{_TIER_LABEL[f.tier]}</span>")


_ZYG_CHIP = {"het": ("one altered copy", "het"), "hom": ("two altered copies", "hom"),
             "hemi": ("one copy (X or Y)", "het"), "unknown": ("copies not determined", None)}


_CLINICAL_FULL = {"clinvar", "clinvar_mirror", "clinvar_panel_157", "cpic"}
_ROWS_SHOWN = 5   # compact rows on a card before "N more"


def _is_full(f: Finding) -> bool:
    """The four-part face is for findings a person may act on: promoted ones and
    clinical ones with a next step (ClinVar, CPIC). Every research row is one line."""
    return bool(f.promoted) or (f.source or "") in _CLINICAL_FULL


def _data_attrs(f: Finding) -> str:
    d = f.detail or {}
    mag = magnitude(f)
    carried = d.get("risk_allele_carried")
    tissue_ok = d.get("tissue_supported")
    return (f"data-tier='{f.tier.value}' data-topic='{html.escape(str(d.get('topic', 'other')))}' "
            f"data-modality='{_modality(f)}' data-mag='{mag}' "
            f"data-predicted='{'1' if _predicted_by(f) else '0'}' "
            f"data-direction='{direction(f)}' data-promoted='{'1' if f.promoted else '0'}' "
            f"data-carried='{'0' if carried is False else '1'}' "
            f"data-tissue='{'0' if tissue_ok is False else '1'}' "
            f"data-source='{html.escape(str(f.source or ''))}'")


def _source_link(f: Finding) -> str:
    from .sources import resolve as _resolve_source
    _s = _resolve_source(f.source or "")
    if _s:
        label = f"{_s.org} {_s.name}" if _s.org and _s.org not in _s.name else _s.name
        return (f"<a class='src' href='{html.escape(f.link or _s.url)}' "
                f"title='{html.escape(_s.license)}'>{html.escape(label)}</a>")
    if f.link:
        return f"<a class='src' href='{html.escape(f.link)}'>{html.escape(f.source)}</a>"
    return f"<span class='src'>{html.escape(f.source)}</span>"


def _meta_line(f: Finding) -> str:
    modality = _modality(f)
    bits = [_tier_badge(f),
            f"<span class='mod mod-{modality}' title='{_MODALITY_LABEL[modality]} finding'>"
            f"{_MODALITY_LABEL[modality]}</span>",
            _entity_links(f), _pubmed_links(f.pmids), _source_link(f), _glossary_link(f)]
    return "<div class='meta'>" + " <span class=sep>·</span> ".join(b for b in bits if b) + "</div>"


def _chain_html(f: Finding) -> str:
    if not f.evidence_chain:
        return ""
    links = " <span class='arrow'>→</span> ".join(
        (f"<a href='{html.escape(c.url)}'>{html.escape(c.label)}</a>" if c.url
         else html.escape(c.label))
        + f" <span class='ckind'>{html.escape(c.kind)}</span>" for c in f.evidence_chain)
    return (f"<details class='chain'><summary>Evidence chain</summary>"
            f"<p class='chainrow'>{links}</p></details>")


def _fmt_people(n) -> str:
    try:
        return f"{int(float(n)):,}"
    except (TypeError, ValueError):
        return ""


def _compact_chips(f: Finding) -> str:
    """Evidence as chips: strength, people, tissue, and the two warnings that
    change how a person reads the row. Counts and p-values never appear as prose."""
    d = f.detail or {}
    chips = [f"<span class='chip tier-{f.tier.value}'>{_TIER_LABEL[f.tier]}</span>"]
    n_st = d.get("n_studies")
    people = _fmt_people(d.get("n_participants") if d.get("n_participants") is not None else d.get("n"))
    try:
        n_st = int(n_st) if n_st is not None else None
    except (TypeError, ValueError):
        n_st = None
    if n_st and n_st > 1 and people:
        chips.append(f"<span class='chip'>{n_st} studies · {people} people</span>")
    elif people:
        chips.append(f"<span class='chip'>{people} people</span>")
    tissues = [str(t) for t in (d.get("tissues") or ([d["tissue"]] if d.get("tissue") else [])) if t]
    if tissues:
        chips.append(f"<span class='chip'>{html.escape(', '.join(tissues[:2]).lower())}</span>")
    if d.get("direction") == "mixed":
        chips.append("<span class='chip warn'>studies disagree</span>")
    if d.get("tissue_supported") is False:
        chips.append("<span class='chip warn'>not your tissue</span>")
    if d.get("risk_allele_carried") is True:
        chips.append("<span class='chip'>you carry it</span>")
    if _predicted_by(f):
        chips.append(f"<span class='chip'>{term_link('prediction', 'prediction')}</span>")
    return "<div class='chips'>" + "".join(chips) + "</div>"


def _compact_line(f: Finding) -> str:
    """One research finding: a sentence, chips, and a closed drawer with the
    study details, the chain and the sources."""
    ip = f.interpretation
    d = f.detail or {}
    label = str(d.get("short_label") or "")
    found = ip.found or f.description
    if label and found.startswith(label + " — "):
        sent = f"<b>{html.escape(label)}</b> — {html.escape(found[len(label) + 3:])}"
    else:
        sent = html.escape(found)
    drawer = (f"<details class='fdetail'><summary>Details</summary>"
              f"{_meta_line(f)}{_chain_html(f)}{_study_details(f)}</details>")
    return (f"<li class='finding compact' {_data_attrs(f)}>"
            f"<p class='sent'>{sent}</p>{_compact_chips(f)}{drawer}</li>")


def _full_line(f: Finding) -> str:
    """A finding a person may act on: chips for the facts a worried reader looks
    for first, the reason it was promoted, four parts, then the drawers."""
    ip = f.interpretation
    d = f.detail or {}
    chips = []
    if f.promoted:
        chips.append("<span class='chip first'>Read this first</span>")
    if ip.zygosity in _ZYG_CHIP:
        text, key = _ZYG_CHIP[ip.zygosity]
        chips.append(f"<span class='chip'>{term_link(key, text) if key else html.escape(text)}</span>")
    sig = str(d.get("clinical_significance") or "").lower()
    if sig:
        inner = term_link("plp", sig) if "pathogenic" in sig and "conflicting" not in sig else html.escape(sig)
        chips.append(f"<span class='chip'>{inner}</span>")
    if d.get("gold_stars") is not None:
        chips.append(f"<span class='chip'>{term_link('stars', f'{d['gold_stars']} of 4 stars')}</span>")
    if str(d.get("platform") or "").upper() == "ARRAY":
        chips.append(f"<span class='chip warn'>{term_link('array', 'array call')}</span>")
    if d.get("diplotype"):
        chips.append(f"<span class='chip'>{html.escape(str(d['diplotype']))}</span>")
    if _predicted_by(f):
        chips.append(f"<span class='chip'>{term_link('prediction', 'prediction')}</span>")
    parts = "".join(
        f"<div><span class='plab'>{lab}</span><p>{html.escape(txt)}</p></div>"
        for lab, txt in (("What was found", ip.found), ("What it can mean", ip.can_mean),
                         ("How sure", ip.how_sure), ("Next step", ip.next_step)) if txt)
    dive = ""
    if f.deeper_dive:
        m = f.deeper_dive_meta or {}
        who = html.escape(str(m.get("model") or m.get("backend") or "model"))
        dive = (f"<details class='dive'><summary>Deeper dive <span class='ai'>AI-drafted from "
                f"the sources above · {who}</span></summary>"
                f"<p>{html.escape(f.deeper_dive)}</p></details>")
    why = f"<p class='why'>{html.escape(f.promoted_reason)}</p>" if f.promoted and f.promoted_reason else ""
    return (f"<li class='finding meaning' {_data_attrs(f)}>"
            f"<div class='body'><div class='mhead'>{''.join(chips)}</div>{why}"
            f"<div class='four'>{parts}</div>{_chain_html(f)}{dive}"
            f"{_meta_line(f)}{_study_details(f)}</div></li>")


def _meaning_line(f: Finding, *, hoist_mean: bool = False) -> str:
    """An interpreted finding, in one of two faces (see _is_full)."""
    return _full_line(f) if _is_full(f) else _compact_line(f)


def _outcomes_html(outcomes: list, marker_url) -> str:
    """Placeholder until the outcome cards land (Task F3): one line per outcome."""
    if not outcomes:
        return "<p class='h2sub'>Nothing to group yet.</p>"
    items = "".join(f"<li>{html.escape(str(getattr(o, 'label', o)))}</li>" for o in outcomes)
    return f"<ul>{items}</ul>"


def _tcga_line(gdc: list[Finding]) -> str:
    """Every tumour comparison on a card, as one sentence and a drawer. 1,106 of
    1,645 rows on the combined demo were these; a person needs the count and
    the two strongest, and can open the rest."""
    rows = []
    for f in gdc:
        d = f.detail or {}
        try:
            delta = float(d.get("delta_beta"))
        except (TypeError, ValueError):
            delta = None
        rows.append((abs(delta or 0.0), delta, str(d.get("project") or "TCGA"),
                     d.get("n_tumor"), d.get("n_normal"), f.link))
    rows.sort(key=lambda r: -r[0])
    n = len(rows)
    names = [r[2].replace("TCGA-", "") for r in rows[:2]]
    most = (f", most in {names[0]} and {names[1]}" if len(names) == 2
            else (f", in {names[0]}" if names else ""))
    trs = "".join(
        f"<tr><td>{html.escape(r[2])}</td><td>{'' if r[1] is None else f'{r[1]:+.2f}'}</td>"
        f"<td>{r[3] if r[3] is not None else ''}</td><td>{r[4] if r[4] is not None else ''}</td>"
        f"<td>{('<a href=' + chr(39) + html.escape(r[5]) + chr(39) + '>record</a>') if r[5] else ''}</td></tr>"
        for r in rows)
    return (f"<div class='tcga' data-n='{n}'><b>Differs in tumour tissue</b> in {n} TCGA cancer "
            f"type{'s' if n != 1 else ''}{most}. Reference biology, not a test."
            f" <details class='fdetail'><summary>Show the {n}</summary>"
            f"<table class='statgrid'><tr><th>project</th><th>Δβ</th><th>tumour n</th>"
            f"<th>normal n</th><th></th></tr>{trs}</table></details></div>")


def _marker_card(marker: str, fs: list[Finding], marker_url) -> str:
    """One card per marker. Findings a person may act on come first in full;
    research rows follow as one line each, five shown then "N more"; tumour
    comparisons fold into one line at the end. The reading stays the headline."""
    fs = sorted(fs, key=_strength_key)
    url = marker_url(marker) if marker_url else None
    label = _marker_label(marker)
    head = (f"<a href='{html.escape(url)}'>{html.escape(label)}</a>"
            if url else html.escape(label))
    gdc = [f for f in fs if (f.source or "") == "gdc"]
    rest = [f for f in fs if (f.source or "") != "gdc"]
    full = [f for f in rest if f.interpretation is not None and _is_full(f)]
    compact = [f for f in rest if f.interpretation is not None and not _is_full(f)]
    plain = [f for f in rest if f.interpretation is None]
    lines = "".join(_finding_line(f) for f in full)
    shown = compact[:_ROWS_SHOWN]
    hidden = compact[_ROWS_SHOWN:]
    lines += "".join(_finding_line(f) for f in shown)
    if hidden:
        lines += (f"<details class='rows-more'><summary>{len(hidden)} more</summary>"
                  f"<ul class='findings'>{''.join(_finding_line(f) for f in hidden)}</ul></details>")
    lines += "".join(_finding_line(f) for f in plain)
    lead = ""
    if compact:
        lead = ("<p class='lead'>Group patterns at this site. Not a measurement of you and not "
                f"a prediction. {term_link('group', 'How to read these')}</p>")
    tcga = _tcga_line(gdc) if gdc else ""
    n_traits = len(rest)
    bits = []
    if n_traits and (gdc or n_traits != 1):
        bits.append(f"{n_traits} trait{'s' if n_traits != 1 else ''}" if compact and not full
                    else f"{n_traits} finding{'s' if n_traits != 1 else ''}")
    if gdc:
        bits.append(f"{len(gdc)} tumour type{'s' if len(gdc) != 1 else ''}")
    count = " · ".join(bits)
    reading = next((f.detail.get("your reading") for f in fs
                    if f.detail and f.detail.get("your reading") is not None), None)
    read_html = ""
    if reading is not None:
        read_html = (f"<span class='card-read'><span class='rlab'>your reading</span>"
                     f"{float(reading):.3f}</span>")
    tiers = " ".join(sorted({f.tier.value for f in fs}))
    topics = " ".join(sorted({str(f.detail.get("topic", "other")) for f in fs}))
    top_mag = max(magnitude(f) for f in fs)
    first = " first" if any(getattr(f, "promoted", False) for f in fs) else ""
    return (f"<div class='card{first}' data-tiers='{tiers}' data-topics='{topics}' "
            f"data-mag='{top_mag}' data-marker='{html.escape(marker.lower())}'>"
            f"<div class='card-h'><span class='marker'>{head}</span>"
            f"<span class='card-vals'>{read_html}"
            f"<span class='card-meta'>{count}</span></span></div>{lead}"
            f"<ul class='findings'>{lines}</ul>{tcga}</div>")


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
                top_n: int = _DEFAULT_TOP_N,
                read_first: list[Finding] | None = None,
                outcomes: list | None = None,
                actions: list | None = None,
                person: dict | None = None) -> str:
    """Render findings as a human report: grouped by category, then by marker
    (one card per marker), robust findings first. `marker_url(marker)->str|None`
    lets the product link a marker to a public record (bio-core stays domain-
    agnostic — it does not know CpG vs variant databases)."""
    # One card per marker (each marker appears exactly ONCE — no cross-category
    # duplication). A marker is placed under a single PRIMARY category = the
    # category of its best-tier finding, ties broken by precedence
    # (clinical > aging > trait).
    _CAT_PREC = {Category.CLINICAL: 0, Category.AGING: 1, Category.TRAIT: 2,
                 Category.REFERENCE: 3}
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
    for cat in (Category.CLINICAL, Category.AGING, Category.TRAIT, Category.REFERENCE):
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
        sections.append(f"<section id='{anchor}' data-view='site'><h2>{label}</h2>{cards}</section>")

    # The section that opens the report. Membership is decided upstream by
    # published lists (dnareport.triage), never by the magnitude score; the
    # renderer only places what it is handed and shows the reason on each card.
    read_first_html = ""
    if read_first:
        cards = "".join(_marker_card(f.marker, [f], marker_url) for f in read_first)
        read_first_html = ("<section id='read-first' data-view='first'><h2>Read this first</h2>"
                           "<p class='h2sub'>Chosen by published lists, not by a score. "
                           "Each one says why it is here.</p>"
                           f"{cards}</section>")
        toc.insert(0, "<li><a href='#read-first'>Read this first "
                      f"<span class='toc-n'>{len(read_first)}</span></a></li>")
    # Terms are explained once, for the keys some card actually linked to.
    used_terms = [k for k in ("group", "het", "hom", "plp", "stars", "array", "prediction",
                              "dominant", "recessive", "af", "or", "beta", "p", "methylation")
                  if f"#term-{k}'" in read_first_html + "".join(sections)]
    terms_section = terms_html(used_terms)

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
                     f"{_modality_breakdown(findings)}"
                     f"{consulted_html}"
                     f"<p class='scan-privacy'>&#128274; Your uploaded file is processed and then "
                     f"deleted — it is not retained after this report is generated.</p></section>")

    # Three views of one report. "Read first" opens on what matters; "By outcome"
    # groups by consequence; "By site" is the card-per-marker list. The switch
    # sets data-view on <body>; sections carry data-view membership, sections
    # without it (snapshot, terms, sources, about) show in every view.
    default_view = "first" if read_first else "site"
    outcome_html = ""
    n_outcomes = len(outcomes or [])
    if outcomes is not None:
        outcome_html = (f"<section id='outcome' data-view='outcome'><h2>By outcome</h2>"
                        f"{_outcomes_html(outcomes, marker_url)}</section>")
    views = [("first", "Read first", len(read_first or [])),
             ("outcome", "By outcome", n_outcomes),
             ("site", "By site", len(by_marker))]
    switch = "".join(
        f"<a class='view' data-view='{v}' href='#view={v}'>{lab} "
        f"<span class='toc-n'>{n}</span></a>" for v, lab, n in views
        if not (v == "first" and not read_first) and not (v == "outcome" and outcomes is None))
    view_switch = f"<nav class='views' aria-label='Views'>{switch}</nav>"
    rail_items = []
    if read_first:
        rail_items.append(f"<li data-view='first'><a href='#read-first'>Read this first "
                          f"<span class='toc-n'>{len(read_first)}</span></a></li>")
    if outcomes is not None:
        rail_items.append(f"<li data-view='outcome'><a href='#outcome'>By outcome "
                          f"<span class='toc-n'>{n_outcomes}</span></a></li>")
    rail_items += [f"<li data-view='site'>{t[4:]}" for t in toc]   # the category items, view-tagged
    if terms_section:
        rail_items.append("<li><a href='#terms'>Terms</a></li>")
    if used:
        rail_items.append("<li><a href='#sources'>Data sources</a></li>")
    rail_items.append("<li><a href='#about'>How to read this</a></li>")
    rail_html = (f"<nav class='rail' aria-label='Contents'>{view_switch}"
                 f"<strong>Contents</strong><ul>{''.join(rail_items)}</ul></nav>")
    toc_html = (f"<nav class='toc'>{view_switch}<strong>Contents</strong><ul>{''.join(rail_items)}</ul></nav>"
                if toc else view_switch)

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
    /* the split reads before the source list: it is the difference between "the
       genome scan found little" and "the genome scan failed" */
    .scan-mods{font-size:13px;color:var(--mut);line-height:1.55;margin:13px 0 0;
      max-width:78ch}
    .scan-mods strong{color:var(--ink);font-weight:600}
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
    /* Saving is the browser's own print-to-PDF, which is why the report has a
       print stylesheet at all. The button exists because nothing on the page
       said so — the capability was already here and undiscoverable. */
    .savebtn{font:inherit;font-size:12px;color:var(--mut);background:var(--card);
      border:1px solid var(--hair);border-radius:4px;padding:4px 10px;cursor:pointer}
    .savebtn:hover{color:var(--ink);border-color:var(--mut)}
    .savebtn:focus{outline:2px solid var(--accent);outline-offset:1px}
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
    /* the trait phrase links into the glossary — marked with a dotted rule so it
       reads as "there is more about this" rather than as a navigation link */
    .glossword{color:inherit;text-decoration:none;
      border-bottom:1px dotted var(--hair);cursor:help}
    .glossword:hover{color:var(--accent);border-bottom-color:var(--accent)}
    .glosslink{color:var(--faint);font-size:12px}
    .card-meta{color:var(--faint);font-size:11.5px;white-space:nowrap;
      letter-spacing:.08em;text-transform:uppercase}
    /* the reading leads the card: full-contrast ink at a size that survives a
       page of cards, with the label kept quiet so the NUMBER is what carries */
    .card-vals{display:flex;align-items:baseline;gap:14px;min-width:0}
    /* The reading is boxed because it is the one number on the card that came
       from the reader's own sample, and unboxed it read as one more piece of
       metadata in a row of them.
       The frame is deliberately NOT the report's red: --adverse is reserved for
       "this variant is disease-associated", and a measurement is not a verdict.
       Borrowing that colour would tell every reader their reading is bad news
       before they have read a word. This is a warm amber that draws the eye at
       the same strength without carrying the meaning. */
    .card-read{font-family:var(--mono);font-size:18px;color:var(--ink);
      letter-spacing:-.01em;white-space:nowrap;font-weight:600;
      border:1.5px solid #c2683c;border-radius:7px;padding:3px 10px 4px;
      background:#fdf0e7;display:inline-flex;align-items:baseline}
    .card-read .rlab{font-size:10px;letter-spacing:.09em;text-transform:uppercase;
      color:#a8522c;margin-right:7px;font-weight:700}
    @media(prefers-color-scheme:dark){
      .card-read{border-color:#d98a5c;background:#2b1d14}
      .card-read .rlab{color:#e2a479}}
    @media print{
      /* the fill is what carries it on screen; on paper the border does the work
         and a filled box wastes ink on every card */
      .card-read{background:none}}
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
    /* Outlined, not filled: a prediction is a qualifier on the finding, and
       should be legible without competing with the tier badge that carries the
       evidence weight. */
    .pred-note{margin:2px 0 0;font-size:13px;color:var(--mut)}
    .pred{font-size:10.5px;font-weight:600;letter-spacing:.04em;color:#8a6d3b;
      border:1px solid currentColor;border-radius:9px;padding:0 6px;white-space:nowrap}
    .mod-methylome{color:#3d7ea6}
    .mod-genome{color:#a8574f}
    @media(prefers-color-scheme:dark){
      .mod-methylome{color:#7cc0ff} .mod-genome{color:#e09a90}
      .pred{color:#d7b56d}}
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
    /* --- views + rail ---------------------------------------------------- */
    body[data-view] section[data-view]{display:none}
    body[data-view=first] section[data-view=first],
    body[data-view=outcome] section[data-view=outcome],
    body[data-view=site] section[data-view=site]{display:block}
    body[data-view] nav li[data-view]{display:none}
    body[data-view=first] nav li[data-view=first],
    body[data-view=outcome] nav li[data-view=outcome],
    body[data-view=site] nav li[data-view=site]{display:list-item}
    .views{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
    .views a.view{font:600 12.5px/1 var(--sans);color:var(--mut);background:var(--card);
      border:1px solid var(--line);border-radius:3px;padding:8px 12px;text-decoration:none}
    .views a.view.on{color:var(--card);background:var(--accent);border-color:var(--accent)}
    .views a.view.on .toc-n{color:var(--card);opacity:.8}
    .rail{display:none}
    @media(min-width:1180px){
      .rail{display:block;position:fixed;top:28px;left:calc(50% - 28em - 236px);width:210px;
        font-size:13px;max-height:calc(100vh - 56px);overflow:auto}
      .rail strong{font:600 11px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;
        color:var(--faint);display:block;margin:6px 0 8px}
      .rail ul{list-style:none;margin:0;padding:0}
      .rail li{margin:0}
      .rail li a{display:block;padding:5px 8px;border-left:2px solid transparent;color:var(--mut);
        text-decoration:none}
      .rail li a:hover{color:var(--accent)}
      .rail li a.here{border-left-color:var(--accent);color:var(--ink)}
      .rail .views{flex-direction:column}
      .rail .views a.view{padding:7px 10px}
      .toc{display:none}
    }
    @media print{body[data-view] section[data-view]{display:block}.rail,.views{display:none}}
    .stats-hidden details.stats{display:none}
    .empty-note{margin:26px 0;padding:16px 18px;background:var(--card);
      border:1px solid var(--line);border-left:3px solid var(--speculative);
      border-radius:3px;font-size:14.5px;color:var(--mut)}
    @media(max-width:560px){
      .finding{grid-template-columns:38px minmax(0,1fr);gap:12px}
      .count-note{margin-left:0}
      /* the id and the reading stop competing for one line rather than the
         reading being the thing that truncates */
      .card-h{flex-wrap:wrap;gap:3px 12px}
      .card-read{font-size:15px}
    }
    /* --- interpreted findings -------------------------------------------
       Two faces. A research row is one serif sentence with quiet chips and a
       closed drawer. A finding a person may act on keeps four labelled parts.
       Both sit under a card lead that says once what the rows are. */
    .lead{font:400 13px/1.45 var(--sans);color:var(--mut);margin:9px 0 2px;max-width:none}
    .lead .glossword{color:var(--accent);border-bottom-color:var(--accent)}
    .finding.compact{display:block;padding:9px 0 8px}
    .finding.compact .sent{font:400 14.5px/1.42 var(--serif);margin:0;color:var(--ink);max-width:72ch}
    .finding.compact .sent b{font-weight:600}
    .chips{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}
    .chip{font:600 10px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;
      color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:4px 7px;white-space:nowrap}
    .chip a{color:inherit;border:0}
    .chip.tier-robust{color:var(--robust);border-color:var(--robust)}
    .chip.tier-moderate{color:var(--moderate);border-color:var(--moderate)}
    .chip.tier-speculative{color:var(--speculative);border-color:var(--speculative)}
    .chip.first{background:var(--accent);color:var(--card);border-color:var(--accent)}
    .chip.warn{color:#8a4b2a;border-color:#8a4b2a}
    details.fdetail{margin-top:5px;font-size:12.5px}
    details.fdetail>summary{color:var(--faint);cursor:pointer;font:500 11px/1 var(--mono);
      letter-spacing:.08em;text-transform:uppercase;list-style:none}
    details.fdetail>summary::-webkit-details-marker{display:none}
    details.fdetail>summary::before{content:'▸ ';color:var(--accent)}
    details.fdetail[open]>summary::before{content:'▾ '}
    details.fdetail .meta{margin-top:6px}
    details.rows-more{margin:4px 0 2px}
    details.rows-more>summary{cursor:pointer;list-style:none;display:inline-block;
      font:600 12px/1 var(--sans);color:var(--accent);background:var(--accent-soft);
      border:1px solid var(--hair);border-radius:3px;padding:7px 12px;user-select:none}
    details.rows-more>summary::-webkit-details-marker{display:none}
    details.rows-more>ul.findings{margin-top:2px}
    .tcga{padding:9px 0 6px;border-top:1px solid var(--line);font:400 13px/1.5 var(--sans);color:var(--mut)}
    .tcga b{color:var(--ink);font-weight:600}
    .tcga details.fdetail{display:inline-block;margin:0}
    .tcga table.statgrid{display:table;margin-top:6px}
    .tcga th{font:500 10px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
      text-align:left;padding:3px 9px;border-bottom:1px solid var(--line)}
    .finding.meaning{grid-template-columns:minmax(0,1fr);padding:13px 0}
    .mhead{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:7px}
    .why{font:400 12.5px/1.4 var(--sans);color:var(--mut);margin:0 0 8px;padding-left:10px;
      border-left:2px solid var(--accent)}
    .four{display:grid;gap:8px 16px;grid-template-columns:1fr}
    @media(min-width:640px){.four{grid-template-columns:1fr 1fr}}
    .plab{display:block;font:500 10px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;
      color:var(--faint);margin-bottom:3px}
    .four p{font:400 14px/1.45 var(--serif);margin:0;color:var(--ink)}
    details.chain,details.dive{margin-top:8px;font-size:12.5px}
    details.chain summary,details.dive summary{cursor:pointer;color:var(--accent);
      font-weight:600;font-size:12px}
    .chainrow{margin:6px 0 0;line-height:2}
    .ckind{font:400 10px/1 var(--mono);color:var(--faint);letter-spacing:.08em;text-transform:uppercase}
    .arrow{color:var(--hair)}
    .ai{font-weight:400;color:var(--faint);font-style:italic}
    details.dive p{font:400 14px/1.5 var(--serif);margin:6px 0 0;max-width:70ch}
    .unreviewed{font:400 12.5px/1.4 var(--sans);color:var(--faint);margin:10px 0 0;
      padding-left:12px;border-left:2px solid var(--hair)}
    .card.first{border-left:3px solid var(--accent)}
    #read-first .card{margin:12px 0}
    dl.terms dt{font:600 14px/1.3 var(--sans);margin:14px 0 2px}
    dl.terms dd{margin:0;color:var(--mut);font-size:14px;max-width:70ch}
    @media(prefers-color-scheme:dark){.chip.warn{color:#d6905f;border-color:#d6905f}}
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

    # A prediction filter, shown only when some finding actually carries one —
    # the same rule as the modality control above. Offering "predicted only" on a
    # report with no predictions in it sends the reader looking for something
    # that was never there.
    n_predicted = sum(1 for f in findings if _predicted_by(f))
    predicted_ctrl = ""
    if n_predicted:
        predicted_ctrl = (
            "<label>Predictions <select id='predfilter'>"
            "<option value=''>Include predictions</option>"
            f"<option value='only'>Predicted only ({n_predicted})</option>"
            "<option value='none'>Measured and curated only</option>"
            "</select></label>")

    # Associations for alleles the person does not carry are true statements
    # about a study and nothing about the reader. Hidden by default, counted, and
    # one checkbox away — never silently dropped.
    n_uncarried = sum(1 for f in findings if (f.detail or {}).get("risk_allele_carried") is False)
    uncarried_ctrl = ""
    if n_uncarried:
        uncarried_ctrl = (f"<label class='switch'><input type='checkbox' id='uncarried'> "
                          f"Show {n_uncarried} association{'s' if n_uncarried != 1 else ''} "
                          f"for alleles you do not carry</label>")
    # Associations studied only in tissues that are not the sample's say less
    # about the reader; same treatment: hidden, counted, one checkbox away.
    n_mismatch = sum(1 for f in findings if (f.detail or {}).get("tissue_supported") is False)
    if n_mismatch:
        uncarried_ctrl += (f"<label class='switch'><input type='checkbox' id='mismatch'> "
                           f"Show {n_mismatch} association{'s' if n_mismatch != 1 else ''} "
                           f"studied in other tissues</label>")
    # One notice for the whole report, instead of one line under every row.
    unreviewed_note = ""
    if any(f.interpretation is not None and not f.interpretation.reviewed_by for f in findings):
        unreviewed_note = ("<p class='unreviewed'>The plain-language wording on this report "
                           "has not yet been reviewed by a person.</p>")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{style}</style></head><body>
{rail_html}
<h1 data-default-view="{default_view}">{html.escape(title)}</h1>
<p class="sub">{len(findings)} findings across {n_markers} markers, ordered by how much
evidence stands behind each one.</p>
{unreviewed_note}
{scan_html}
{mix_html}
<div class="controls">
  <label>Evidence
    <select id="evfilter">
      <option value="robust" selected>Strongest only</option>
      <option value="robust moderate">Strong &amp; moderate</option>
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
  {predicted_ctrl}
  <label>Find
    <input id="markersearch" type="search" placeholder="gene, rsID or trait" size="16">
  </label>
  <label class="switch"><input type="checkbox" id="stattoggle"> Study statistics</label>
  {uncarried_ctrl}
  <button type="button" id="savepdf" class="savebtn" title="Save this report as a PDF">Save as PDF</button>
  <span class="count-note" id="countnote"></span>
</div>
<p class="pred-note" id="prednote" style="display:none"></p>
<p class="empty-note" id="emptynote" style="display:none">No findings match these filters.
Widen the evidence setting or lower the minimum magnitude to see more.</p>
{toc_html}
{read_first_html}
{outcome_html}
{''.join(sections)}
{terms_section}
{sources_panel}
<section id="about"><h2>About these results</h2>{_disclaimer_html(disclaimer_path)}</section>
<footer><strong>Data sources at generation time</strong><ul>{status_rows}</ul>
Generated {now} · v{tool_version}</footer>
<script>
(function(){{
  var sel=document.getElementById('evfilter'),
      topic=document.getElementById('topicfilter'),
      mod=document.getElementById('modfilter'),
      pred=document.getElementById('predfilter'),
      dir=document.getElementById('dirfilter'),
      search=document.getElementById('markersearch'),
      mag=document.getElementById('magfilter'),
      magval=document.getElementById('magval'),
      stat=document.getElementById('stattoggle'),
      uncarried=document.getElementById('uncarried'),
      mismatch=document.getElementById('mismatch'),
      note=document.getElementById('countnote'),
      empty=document.getElementById('emptynote'),
      prednote=document.getElementById('prednote'),
      cards=[].slice.call(document.querySelectorAll('.card')),
      moreDetails=[].slice.call(document.querySelectorAll('details.more, details.rows-more')),
      viewLinks=[].slice.call(document.querySelectorAll('a.view')),
      viewTagged=[].slice.call(document.querySelectorAll('[data-view]'));

  // Search covers everything the card SAYS — gene symbol, condition, trait
  // wording, rsID — not just the marker id, because people arrive looking for
  // "BRCA" or "lactose", not for cg05575921. Indexed once; the text is static.
  cards.forEach(function(c){{ c._hay=(c.textContent||'').toLowerCase(); }});

  function applyFilter(){{
    var allow=new Set(sel.value.split(' '));
    var want=topic.value;                       // '' = all subjects
    var wantMod=mod?mod.value:'';               // '' = all sources
    var wantPred=pred?pred.value:'';            // '' = include predictions
    var wantDir=dir?dir.value:'';               // '' = any significance
    var minMag=parseFloat(mag.value)||0;
    var q=(search.value||'').trim().toLowerCase();
    var shown=0;
    magval.textContent=minMag;
    var activeView=document.body.getAttribute('data-view')||'site';
    document.querySelectorAll('.finding').forEach(function(f){{
      var promoted=f.getAttribute('data-promoted')==='1';
      var sec=f.closest('section[data-view]');
      var inView=!sec||sec.getAttribute('data-view')===activeView;
      var ok=(allow.has(f.getAttribute('data-tier'))||promoted)
           && (!want || f.getAttribute('data-topic')===want)
           && (!wantMod || f.getAttribute('data-modality')===wantMod)
           && (!wantPred || (wantPred==='only')===(f.getAttribute('data-predicted')==='1'))
           && (!wantDir || f.getAttribute('data-direction')===wantDir)
           && parseFloat(f.getAttribute('data-mag')||0)>=minMag
           && ((uncarried&&uncarried.checked) || f.getAttribute('data-carried')!=='0')
           && ((mismatch&&mismatch.checked) || promoted || f.getAttribute('data-tissue')!=='0');
      f.classList.toggle('filtered-out',!ok);
      if(ok){{
        // The opening section repeats cards that also sit in their category;
        // counting both would say "4 findings shown" for two findings.
        if(f.closest('#read-first'))return;
        // A card collapsed inside a closed "show more" is not on screen —
        // it must not inflate the "N findings shown" count, or the counter
        // and the page visibly disagree (the #1 risk in this feature: 10
        // cards on screen while the counter claims hundreds are "shown").
        var det=f.closest('details.more, details.rows-more');
        if((!det||det.open)&&inView)shown++;
      }}
    }});
    // Predictions hidden by the EVIDENCE setting specifically: without this the
    // page silently omits the one thing the reader may have come to look at, and
    // the "Predicted only (N)" label promises a number the view will not deliver.
    if(prednote){{
      var hiddenPred=0;
      document.querySelectorAll(".finding[data-predicted='1']").forEach(function(f){{
        if(f.classList.contains('filtered-out')&&!allow.has(f.getAttribute('data-tier')))hiddenPred++;
      }});
      if(hiddenPred&&wantPred!=='none'){{
        prednote.textContent=hiddenPred+' predicted finding'+(hiddenPred===1?' is':'s are')+
          ' below the current evidence setting. Choose “All, incl. weak” to see '+
          (hiddenPred===1?'it':'them')+'.';
        prednote.style.display='';
      }} else {{ prednote.style.display='none'; }}
    }}
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
      var inner=[].slice.call(det.querySelectorAll(det.classList.contains('rows-more')?'.finding':'.card'));
      var matching=inner.filter(function(c){{return !c.classList.contains('filtered-out');}}).length;
      var summary=det.querySelector('summary');
      if(!summary)return;
      if(det.classList.contains('rows-more')){{
        summary.textContent=det.open?'Fewer':(matching?(matching+' more'):'No further matches');
        return;
      }}
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
  if(pred)pred.addEventListener('change',function(){{
    // A prediction is speculative by construction — it speaks to variants the
    // catalogues could not settle — so every predicted finding sits below the
    // default evidence setting. Asking to see only predictions and then being
    // shown an empty page is the filter contradicting an explicit request, so
    // widen the evidence setting to match it. Done by changing the visible
    // control, not behind it, so the reason the page changed is on screen.
    if(pred.value==='only')sel.value='robust moderate speculative unknown';
    applyFilter();
  }});
  topic.addEventListener('change',applyFilter);
  if(mod)mod.addEventListener('change',applyFilter);
  if(dir)dir.addEventListener('change',applyFilter);
  search.addEventListener('input',applyFilter);
  mag.addEventListener('input',applyFilter);
  stat.addEventListener('change',applyStats);
  if(uncarried)uncarried.addEventListener('change',applyFilter);
  if(mismatch)mismatch.addEventListener('change',applyFilter);
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
  // Saving is the browser's print-to-PDF: it keeps a genome report inside the
  // reader's own machine, which a server-rendered PDF would not.
  var savebtn=document.getElementById('savepdf');
  if(savebtn) savebtn.addEventListener('click',function(){{ window.print(); }});

  // Views. The switch writes data-view on <body>; CSS hides the sections of
  // the other views; the hash carries the view so a link can point at one.
  function viewFromHash(){{
    var m=(location.hash||'').match(/view=(first|outcome|site)/);
    return m?m[1]:null;
  }}
  function setView(v){{
    var known=viewLinks.map(function(a){{return a.getAttribute('data-view');}});
    if(known.indexOf(v)<0)v=known[0]||'site';
    document.body.setAttribute('data-view',v);
    viewLinks.forEach(function(a){{a.classList.toggle('on',a.getAttribute('data-view')===v);}});
    applyFilter();
  }}
  viewLinks.forEach(function(a){{
    a.addEventListener('click',function(e){{e.preventDefault();history.replaceState(null,'','#view='+a.getAttribute('data-view'));setView(a.getAttribute('data-view'));window.scrollTo(0,0);}});
  }});
  window.addEventListener('hashchange',function(){{var v=viewFromHash(); if(v)setView(v);}});
  setView(viewFromHash()||document.querySelector('h1').getAttribute('data-default-view')||'site');
  // Rail: mark the section in view.
  if(window.IntersectionObserver){{
    var railLinks=[].slice.call(document.querySelectorAll('.rail li a[href^="#"]'));
    var obs=new IntersectionObserver(function(entries){{
      entries.forEach(function(en){{
        if(!en.isIntersecting)return;
        railLinks.forEach(function(a){{a.classList.toggle('here',a.getAttribute('href')==='#'+en.target.id);}});
      }});
    }},{{rootMargin:'-20% 0px -70% 0px'}});
    document.querySelectorAll('section[id]').forEach(function(s){{obs.observe(s);}});
  }}
  applyStats();
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
