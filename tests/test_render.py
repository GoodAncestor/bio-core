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
    # a speculative finding with perfect stats must still rank below a bare robust one
    strong_spec = magnitude(_f(Tier.SPECULATIVE, p=1e-50, n=1e6, gold_stars=4))
    weak_robust = magnitude(_f(Tier.ROBUST))
    assert weak_robust >= strong_spec


def _mixed(*sources):
    """One finding per source, so the modality mix is whatever the sources imply."""
    return [Finding(f"m{i}", s, "d", Tier.MODERATE, [Category.CLINICAL], detail={})
            for i, s in enumerate(sources)]


def test_source_filter_appears_only_when_a_report_actually_mixes_modalities():
    """The filter is a control for choosing between two things. A methylome-only
    report offering "Methylome + genome" invites the reader to look for a half
    that was never there — and the /demo/combined route cannot pin this rule down,
    because whether its methylome half survives display-splitting depends on which
    reference mirrors the host happens to have."""
    from biocore.report.render import render_html
    mixed = render_html(_mixed("ewas_catalog", "clinvar"), [])
    assert "id='modfilter'" in mixed

    single = render_html(_mixed("ewas_catalog", "ewas_atlas"), [])
    assert "id='modfilter'" not in single
