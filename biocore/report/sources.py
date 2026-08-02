# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Data-source registry — one source of truth for attribution.

Every finding carries a `source` string (e.g. 'gwas_catalog'). This maps each to
a human name, homepage, and license, and drives BOTH:
  - per-finding attribution (the reader sees "NHGRI-EBI GWAS Catalog", linked),
  - the report's "Data sources" panel (formal attribution + licenses).

Several sources REQUIRE attribution as a license condition (GWAS Catalog CC BY,
CPIC CC BY-SA, gnomAD ODbL, AlphaMissense CC BY-NC-SA, AlphaGenome non-commercial
API terms), so this registry is a compliance surface, not just polish.

`source_key` matching is prefix-tolerant: 'clinvar_panel_157' and 'clinvar_full'
both resolve to the ClinVar entry via the longest key prefix that matches.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    name: str            # human-facing name
    org: str             # who produces it
    url: str             # homepage / attribution link
    license: str         # license short name
    noncommercial: bool = False
    blurb: str = ""      # one-line description for the panel


SOURCES: dict[str, Source] = {
    "clinvar": Source(
        "clinvar", "ClinVar", "NCBI",
        "https://www.ncbi.nlm.nih.gov/clinvar/", "Public domain",
        blurb="Clinical significance of human variants, curated from submitters."),
    "gwas_catalog": Source(
        "gwas_catalog", "GWAS Catalog", "NHGRI-EBI",
        "https://www.ebi.ac.uk/gwas/", "CC BY 4.0",
        blurb="Curated genome-wide association study SNP–trait associations."),
    "gnomad": Source(
        "gnomad", "gnomAD", "Broad Institute",
        "https://gnomad.broadinstitute.org/", "ODbL / free",
        blurb="Population allele frequencies from >800k exomes/genomes."),
    "cpic": Source(
        "cpic", "CPIC", "Clinical Pharmacogenetics Implementation Consortium",
        "https://cpicpgx.org/", "CC BY-SA 4.0",
        blurb="Gene–drug pharmacogenomic prescribing guidelines."),
    "alphamissense": Source(
        "alphamissense", "AlphaMissense", "Google DeepMind",
        "https://github.com/google-deepmind/alphamissense", "CC BY-NC-SA 4.0",
        noncommercial=True,
        blurb="AI-predicted pathogenicity for missense variants."),
    "alphagenome": Source(
        "alphagenome", "AlphaGenome", "Google DeepMind",
        "https://deepmind.google/science/alphagenome/", "Non-commercial API terms",
        noncommercial=True,
        blurb="AI prediction of regulatory effects of DNA variants."),
    "ewas_catalog": Source(
        "ewas_catalog", "EWAS Catalog", "MRC-IEU, University of Bristol",
        "https://www.ewascatalog.org/", "Academic / cite",
        blurb="Epigenome-wide association study CpG–trait associations."),
    "gdc": Source(
        "gdc", "GDC / TCGA", "NCI Genomic Data Commons",
        "https://gdc.cancer.gov/", "NIH data use",
        blurb="Tumour vs normal DNA methylation across cancer cohorts."),
    "epigenetic_clock": Source(
        "epigenetic_clock", "Epigenetic clocks", "published clock models",
        "https://en.wikipedia.org/wiki/Epigenetic_clock", "Per-clock citation",
        blurb="Biological-age estimators computed from CpG methylation."),
    "ewas_atlas": Source(
        "ewas_atlas", "EWAS Atlas", "CNCB-NGDC",
        "https://ngdc.cncb.ac.cn/ewas/atlas", "Academic / cite",
        blurb="Curated epigenome-wide association study associations and knowledge."),
    "methbank": Source(
        "methbank", "MethBank", "CNCB-NGDC",
        "https://ngdc.cncb.ac.cn/methbank/", "Academic / cite",
        blurb="Reference DNA methylomes across species and tissues."),
}

# alias/prefix keys emitted by the engines -> registry key
_ALIASES = {
    "clinvar_panel_157": "clinvar", "clinvar_full": "clinvar",
    "clinvar_panel_157genes": "clinvar",
    "epigenetic_clock": "epigenetic_clock", "clocks": "epigenetic_clock",
    "unified_callset": None, "array_callset": None,   # a person's own genotype, not an external source
    "biocore.modbam": None,   # mechanism-derived methylation summary, not an external DB
    "geneask.compare": None,  # the person's own multi-sample comparison
    "pharmgkb": "cpic", "opengwas": "gwas_catalog",
}


def resolve(source: str) -> Source | None:
    """Map a finding's source string to a Source, or None if it's the person's
    own callset (no external attribution) / unknown."""
    if not source:
        return None
    s = source.lower()
    if s in _ALIASES:
        key = _ALIASES[s]
        return SOURCES.get(key) if key else None
    if s in SOURCES:
        return SOURCES[s]
    # longest-prefix match (e.g. 'gwas_catalog_v2' -> 'gwas_catalog')
    for k in sorted(SOURCES, key=len, reverse=True):
        if s.startswith(k):
            return SOURCES[k]
    return None


def sources_used(findings) -> list[Source]:
    """Distinct external Sources referenced by a set of findings, for the panel."""
    seen, out = set(), []
    for f in findings:
        src = resolve(getattr(f, "source", "") or "")
        if src and src.key not in seen:
            seen.add(src.key)
            out.append(src)
    return sorted(out, key=lambda s: s.name.lower())
