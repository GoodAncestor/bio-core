# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Compare string genotypes across samples: concordance, discordance typing,
KING-robust relatedness.

Genotypes are strings per marker per sample, e.g. "A/G" (diploid), "A" or "A/A"
(homozygous / hemizygous), "./." or "." (missing). The unit of input is a dict
{sample_label: {marker_id: genotype_string}}. This is the representation from
consumer-array exports (23andMe) and simple per-site callers.

Recovered/refactored from the "six genomes" concordance analysis (session cells
89/91/94/120). Concordance and KING both restrict to markers shared and fully
called in the pair; hemizygous single-allele calls collapse to homozygous for a
fair comparison ("A" == "A/A").
"""
from __future__ import annotations
from itertools import combinations
from typing import Mapping


def is_missing(p: str) -> bool:
    return p == "." or p == "./." or p.endswith("/.")


def norm_geno(p: str) -> str:
    """Collapse a hemizygous single-allele call to a homozygous pair
    ("A" -> "A/A") so a haploid call compares fairly to a diploid one."""
    if is_missing(p):
        return p
    if "/" not in p:
        return f"{p}/{p}"
    return p


def geno_class(p: str):
    """Return ('het'|'hom', frozenset(alleles)) or None if missing.
    Hemizygous single allele is treated as homozygous."""
    if is_missing(p):
        return None
    if "/" not in p:
        return ("hom", frozenset([p]))
    a, b = p.split("/")
    if a == "." or b == ".":
        return None
    return ("het" if a != b else "hom", frozenset([a, b]))


def concordance_pair(call_a: Mapping[str, str], call_b: Mapping[str, str],
                     *, normalize_hemizygous: bool = True) -> tuple[int, int]:
    """(n_matching, n_shared_called) over markers present and non-missing in both.
    With normalize_hemizygous, "A" and "A/A" count as a match."""
    shared = [r for r in (call_a.keys() & call_b.keys())
              if not is_missing(call_a[r]) and not is_missing(call_b[r])]
    if normalize_hemizygous:
        match = sum(1 for r in shared if norm_geno(call_a[r]) == norm_geno(call_b[r]))
    else:
        match = sum(1 for r in shared if call_a[r] == call_b[r])
    return match, len(shared)


def concordance_matrix(calls: Mapping[str, Mapping[str, str]],
                       *, normalize_hemizygous: bool = True):
    """Pairwise genotype-concordance fraction for every sample pair.
    Returns (labels, matrix, overlap) where matrix[i][j] is the concordant
    fraction (diagonal = 1.0) and overlap[i][j] the shared-called marker count."""
    labels = list(calls)
    n = len(labels)
    mat = [[float("nan")] * n for _ in range(n)]
    overlap = [[0] * n for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0
        overlap[i][i] = sum(1 for v in calls[labels[i]].values() if not is_missing(v))
    for i, j in combinations(range(n), 2):
        m, s = concordance_pair(calls[labels[i]], calls[labels[j]],
                                normalize_hemizygous=normalize_hemizygous)
        val = (m / s) if s else float("nan")
        mat[i][j] = mat[j][i] = val
        overlap[i][j] = overlap[j][i] = s
    return labels, mat, overlap


def discordance_breakdown(call_a: Mapping[str, str], call_b: Mapping[str, str]):
    """Classify every discordant marker between two samples.
    Returns (category_counts, examples, n_shared_called). Categories:
      opposite_homozygote        — hom vs hom, no shared allele (e.g. A/A vs G/G)
      het_vs_hom_shared_allele    — one het one hom sharing an allele (A/G vs A/A)
      het_vs_hom_no_shared_allele — one het one hom, disjoint alleles
      allele_set_mismatch         — same zygosity, different allele set (strand/annotation)
      other                       — anything else
    """
    def gclass(p):
        if is_missing(p):
            return "missing"
        if "/" not in p:
            return "hemi"
        a, b = p.split("/")
        return "het" if a != b else "hom"

    shared = [r for r in (call_a.keys() & call_b.keys())
              if not is_missing(call_a[r]) and not is_missing(call_b[r])]
    cats, examples = {}, []
    for r in shared:
        pa, pb = call_a[r], call_b[r]
        if pa == pb:
            continue
        ca, cb = gclass(pa), gclass(pb)
        sa, sb = set(pa.split("/")), set(pb.split("/"))
        if ca == "hom" and cb == "hom" and sa != sb and len(sa & sb) == 0:
            cat = "opposite_homozygote"
        elif (ca == "het") != (cb == "het"):
            cat = "het_vs_hom_shared_allele" if (sa & sb) else "het_vs_hom_no_shared_allele"
        elif sa != sb:
            cat = "allele_set_mismatch"
        else:
            cat = "other"
        cats[cat] = cats.get(cat, 0) + 1
        if len(examples) < 40:
            examples.append((r, pa, pb, cat))
    return cats, examples, len(shared)


def king_relatedness(call_a: Mapping[str, str], call_b: Mapping[str, str],
                     *, restrict_markers: set | None = None) -> dict:
    """KING-robust within-family kinship between two samples from genotype calls.

    kinship = (N_HetHet - 2*N_IBS0) / (N_Het_i + N_Het_j), where N_IBS0 is the
    count of opposite-homozygote sites. ~0.5 = identical/self, ~0.25 = parent-
    child or full sib, ~0 = unrelated. Restrict to autosomal markers via
    restrict_markers (pass the set of autosomal marker ids).
    """
    shared = call_a.keys() & call_b.keys()
    if restrict_markers is not None:
        shared = shared & restrict_markers
    N_het_i = N_het_j = N_hethet = N_ibs0 = n = 0
    for r in shared:
        ca, cb = geno_class(call_a[r]), geno_class(call_b[r])
        if ca is None or cb is None:
            continue
        n += 1
        ta, sa = ca
        tb, sb = cb
        if ta == "het":
            N_het_i += 1
        if tb == "het":
            N_het_j += 1
        if ta == "het" and tb == "het":
            N_hethet += 1
        if ta == "hom" and tb == "hom" and len(sa & sb) == 0:
            N_ibs0 += 1
    denom = N_het_i + N_het_j
    kinship = (N_hethet - 2 * N_ibs0) / denom if denom else float("nan")
    return dict(n_sites=n, N_het_i=N_het_i, N_het_j=N_het_j, N_hethet=N_hethet,
                N_ibs0=N_ibs0, ibs0_rate=(N_ibs0 / n if n else float("nan")),
                kinship=kinship)
