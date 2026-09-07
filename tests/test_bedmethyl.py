"""Tests for the bedMethyl reader + weighted-methylation formula.

Pins the modkit 18-column contract and the Σn_mod/Σ(n_mod+n_canonical) cov>=5
estimator recovered from the eelgrass analysis pipeline.
"""
import os
import gzip
from biocore.io.bedmethyl import read_sites, summarize_by_context
from biocore.methylation.model import Context, weighted_methylation

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample_bedmethyl_5k.bed.gz")


def test_reads_all_three_contexts():
    ctxs = {s.context for s in read_sites(FIX)}
    assert Context.CG in ctxs and Context.CHG in ctxs and Context.CHH in ctxs


def test_column_contract():
    s = next(read_sites(FIX))
    with gzip.open(FIX, "rt") as fixture:
        documented_valid_coverage = int(next(fixture).split("\t")[9])
    assert s.coverage == documented_valid_coverage
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


def test_column_10_controls_coverage_but_not_methylation_denominator(tmp_path):
    """Other modifications count toward valid coverage, not 5mC fraction.

    Both sites satisfy the documented column-10 cutoff.  The estimator remains
    5mC / (5mC + canonical), hence 4 / (4 + 5), not 4 / column-10 totals.
    """
    bed = tmp_path / "coverage.bed"
    bed.write_text(
        "1\t0\t1\tm,CG,0\t5\t+\t0\t1\t0,0,0\t5\t80\t4\t0\t1\t0\t0\t0\t0\n"
        "1\t1\t2\tm,CG,0\t5\t+\t1\t2\t0,0,0\t5\t0\t0\t5\t0\t0\t0\t0\t0\n"
    )
    sites = list(read_sites(str(bed), min_coverage=5))
    assert [(s.pos, s.coverage) for s in sites] == [(0, 5), (1, 5)]
    assert weighted_methylation(sites, min_coverage=5) == 4 / 9
    assert summarize_by_context(str(bed), min_coverage=5)["CG"] == {
        "n_sites": 2,
        "n_sites_covered": 2,
        "weighted_methylation": 4 / 9,
    }
