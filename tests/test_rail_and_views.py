"""Three views of one report, a contents rail, and a switch that survives printing."""
import types
from biocore.providers.base import Finding, Tier, Category, Interpretation
from biocore.report.render import render_html


def _f(marker, promoted=False):
    ip = Interpretation(found="f", can_mean="m", how_sure="s", next_step="n") if promoted else None
    return Finding(marker=marker, source="clinvar_mirror" if promoted else "gwas_catalog",
                   description="x", tier=Tier.ROBUST,
                   categories=[Category.CLINICAL if promoted else Category.TRAIT],
                   detail={"topic": "other", "gene": "BRCA2"} if promoted else {"topic": "other"},
                   interpretation=ip, promoted=promoted, promoted_reason="why" if promoted else "")


def test_sections_carry_views_and_the_default_is_read_first_when_promoted():
    p = _f("m1", promoted=True); g = _f("rs1")
    h = render_html([p, g], [], disclaimer_path="/x", read_first=[p], outcomes=[types.SimpleNamespace(kind="condition", key="c", label="C", findings=[p], score=None, contributions=[], reference_groups=[], actions=[])])
    # With outcomes the report opens on them; the promoted cards lead that view,
    # so there is no separate Read first tab or section.
    assert "<section id='read-first'" not in h and "href='#view=first'" not in h
    assert "<section id='outcome' data-view='outcome'>" in h
    assert "data-view='site'><h2>Traits" in h
    assert 'data-default-view="outcome"' in h
    assert "href='#view=outcome'" in h and "href='#view=site'" in h
    assert "<nav class='rail'" in h and "IntersectionObserver" in h
    assert "body[data-view=first] section[data-view=first]" in h


def test_default_is_by_site_without_promoted_and_outcome_tab_absent_when_none():
    h = render_html([_f("rs1")], [], disclaimer_path="/x")
    assert 'data-default-view="site"' in h
    assert "href='#view=outcome'" not in h and "href='#view=first'" not in h
    assert "@media print{body[data-view] section[data-view]{display:block}" in h


def test_shown_count_only_counts_the_active_view():
    h = render_html([_f("rs1")], [], disclaimer_path="/x")
    assert "var inView=!sec||sec.getAttribute('data-view')===activeView;" in h
    assert "if((!det||det.open)&&inView)shown++;" in h


def test_without_outcomes_read_first_still_opens_the_report():
    p = _f("m1", promoted=True)
    h = render_html([p], [], disclaimer_path="/x", read_first=[p])
    assert "<section id='read-first' data-view='first'>" in h and 'data-default-view="first"' in h
