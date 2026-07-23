"""Tests for the bedMethyl reader + weighted-methylation formula.

Pins the modkit 18-column contract and the Σn_mod/Σ(n_mod+n_canonical) cov>=5
estimator recovered from the eelgrass pipeline (frame 59ba13be).
"""
import os
from biocore.io.bedmethyl import read_sites, summarize_by_context
from biocore.methylation.model import Context, weighted_methylation

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample_bedmethyl_5k.bed")


def test_reads_all_three_contexts():
    ctxs = {s.context for s in read_sites(FIX)}
    assert Context.CG in ctxs and Context.CHG in ctxs and Context.CHH in ctxs


def test_column_contract():
    s = next(read_sites(FIX))
    assert s.coverage == s.n_mod + s.n_canonical
    assert 0.0 <= s.fraction <= 1.0
    assert s.chrom  # non-empty


def test_weighted_methylation_matches_manual():
    sites = [s for s in read_sites(FIX) if s.context == Context.CG]
    wm = weighted_methylation(sites, min_coverage=5)
    sm = sum(s.n_mod for s in sites if s.coverage >= 5)
    scan = sum(s.n_canonical for s in sites if s.coverage >= 5)
    assert abs(wm - sm / (sm + scan)) < 1e-12


def test_context_filter():
    only_cg = list(read_sites(FIX, contexts={"CG"}))
    assert all(s.context == Context.CG for s in only_cg)


def test_summarize_by_context():
    summ = summarize_by_context(FIX, min_coverage=5)
    assert "CG" in summ and "CHH" in summ
    for ctx, d in summ.items():
        assert 0.0 <= d["weighted_methylation"] <= 1.0
        assert d["n_sites_covered"] <= d["n_sites"]
