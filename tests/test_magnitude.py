"""Magnitude scoring: 0-10 within tier bands, never contradicting tier."""
from biocore.report.render import magnitude
from biocore.providers.base import Finding, Tier, Category


def _f(tier, **detail):
    return Finding("m", "s", "d", tier, [Category.TRAIT], detail=detail)


def test_magnitude_bands():
    assert magnitude(_f(Tier.ROBUST, p=1e-30, n=14000)) >= 9.0
    assert 4.0 <= magnitude(_f(Tier.MODERATE, gold_stars=3)) < 7.0
    assert 1.0 <= magnitude(_f(Tier.SPECULATIVE, p=0.01)) < 4.0
    assert magnitude(_f(Tier.UNKNOWN)) < 1.0


def test_tier_always_dominates_stats():
    strong_spec = magnitude(_f(Tier.SPECULATIVE, p=1e-50, n=1e6, gold_stars=4))
    weak_robust = magnitude(_f(Tier.ROBUST))
    assert weak_robust >= strong_spec


def test_strong_pvalues_do_not_all_collapse_to_ten():
    """The scale must keep discriminating among the strongest findings.

    A hard cap at -log10(p)/10 gave every finding at p <= 1e-10 exactly 10.0,
    which is where GWAS Catalog hits actually live — so the top of the scale
    carried no information at all.
    """
    scores = [magnitude(_f(Tier.ROBUST, p=p))
              for p in (1e-10, 1e-20, 1e-50, 1e-100, 1e-200)]
    assert len(set(scores)) == len(scores), f"saturated: {scores}"
    assert scores == sorted(scores), "stronger p must never score lower"
    assert all(s < 10.0 for s in scores[:-1])
    assert max(scores) <= 10.0


def test_large_samples_keep_separating():
    """Sample size has real diminishing returns, so adjacent large cohorts may
    round to the same tenth — but the score must never stop rising, and a
    decade of extra samples must still be visible somewhere on the scale."""
    scores = [magnitude(_f(Tier.ROBUST, n=n)) for n in (1e3, 1e4, 1e5, 1e6)]
    assert scores == sorted(scores), f"not monotone: {scores}"
    assert scores[-1] > scores[0], f"no separation at all: {scores}"


def test_magnitude_stays_inside_its_tier_band():
    for tier, (lo, hi) in ((Tier.ROBUST, (7, 10)), (Tier.MODERATE, (4, 7)),
                           (Tier.SPECULATIVE, (1, 4)), (Tier.UNKNOWN, (0, 1))):
        for d in ({}, {"p": 1e-300}, {"n": 1e7}, {"gold_stars": 4},
                  {"p": 1e-300, "n": 1e7, "gold_stars": 4}):
            m = magnitude(_f(tier, **d))
            assert lo <= m <= hi, f"{tier} {d} -> {m} outside {lo}-{hi}"


def test_underflowed_pvalue_ranks_as_strongest_not_weakest():
    """GWAS Catalog stores p=0 when the reported value underflows float — those
    are the strongest associations in the file. The old range guard discarded it,
    dropping the single most significant finding to its band floor."""
    assert magnitude(_f(Tier.ROBUST, p=0.0)) == 10.0
    assert magnitude(_f(Tier.ROBUST, p=0.0)) > magnitude(_f(Tier.ROBUST, p=1e-50))
    assert magnitude(_f(Tier.ROBUST, p=0.0)) > magnitude(_f(Tier.ROBUST))


def test_malformed_stats_cannot_escape_the_tier_band():
    """A negative star count previously produced a magnitude below the band the
    finding's tier guarantees."""
    for d in ({"gold_stars": -1}, {"gold_stars": 99}, {"n": -5}, {"p": -1}):
        m = magnitude(_f(Tier.ROBUST, **d))
        assert 7.0 <= m <= 10.0, f"{d} -> {m}"
