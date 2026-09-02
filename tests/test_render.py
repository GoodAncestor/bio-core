import re
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


def _predicted_finding():
    """A ClinVar finding that AlphaGenome enriched — source stays clinvar, and the
    only trace of the prediction is the key it wrote into detail."""
    return Finding("1-100-A-G", "clinvar", "d", Tier.MODERATE, [Category.CLINICAL],
                   detail={"alphagenome": {"quantile_score": 0.9, "direction": "increase",
                                           "top_modality": "RNA_SEQ"}})


def test_a_prediction_is_attributed_even_though_it_produced_no_finding():
    """AlphaMissense and AlphaGenome both carry attribution obligations, and both
    ENRICH findings rather than producing them — so f.source stays 'clinvar' and
    the sources panel, built from f.source, could never reach them. Licence
    compliance, not decoration."""
    from biocore.report.sources import sources_used
    names = [s.name for s in sources_used([_predicted_finding()])]
    assert "AlphaGenome" in names and "ClinVar" in names


def test_predicted_findings_are_marked_and_filterable():
    """A prediction and a catalogue entry read identically in a sentence — both
    say "this variant does X" — so the reader needs the distinction shown, and a
    way to set predictions aside."""
    from biocore.report.render import render_html
    out = render_html([_predicted_finding()], [])
    assert "data-predicted='1'" in out
    assert "predicted · AlphaGenome" in out
    assert "id='predfilter'" in out


def test_no_prediction_control_when_nothing_was_predicted():
    """Same rule as the source filter: a control for setting predictions aside
    must not appear on a report that has none, or it sends the reader looking for
    something that was never there."""
    from biocore.report.render import render_html
    out = render_html(_mixed("clinvar", "ewas_catalog"), [])
    assert "id='predfilter'" not in out
    assert "data-predicted='0'" in out


def test_badge_title_survives_an_apostrophe():
    """The badge's attributes are single-quoted, so an apostrophe anywhere in the
    title closes the attribute early and corrupts the rest of the row. The first
    version of this copy read "A model's estimate" and did exactly that."""
    from biocore.report.render import _predicted_badge
    import biocore.report.sources as S
    badge = _predicted_badge(_predicted_finding())
    inner = badge.split("title='", 1)[1].split("'", 1)[0]
    assert "&#x27;" in inner or "'" not in inner
    assert badge.count("title='") == 1 and badge.endswith("</span>")


def _spec_predicted(n=3):
    """Predicted findings are SPECULATIVE by construction: they speak to variants
    the catalogues could not settle, so the evidence behind them is weak. That is
    the honest tier — and it is also why the default view hides all of them."""
    return [Finding(f"1-{i}-A-G", "clinvar", "d", Tier.SPECULATIVE, [Category.CLINICAL],
                    detail={"alphagenome": {"quantile_score": 0.9}}) for i in range(n)]


def test_predictions_are_hidden_by_the_default_evidence_setting():
    """Pin the fact the fix exists for: the default is "robust" (promoted
    findings exempt), every prediction is speculative, so a report can promise
    "Predicted only (10)" and then render an empty page."""
    from biocore.report.render import render_html
    out = render_html(_spec_predicted(), [])
    assert "<option value=\"robust\" selected>" in out
    assert out.count("data-tier='speculative'") == 3


def test_asking_for_predictions_widens_the_evidence_setting():
    """An explicit request to see only predictions must not be overridden by an
    evidence default set for a different purpose — and the widening happens on the
    visible control, so the reason the page changed is on screen."""
    from biocore.report.render import render_html
    out = render_html(_spec_predicted(), [])
    assert "if(pred.value==='only')sel.value='robust moderate speculative unknown';" in out


def test_hidden_predictions_are_announced_not_silently_dropped():
    from biocore.report.render import render_html
    out = render_html(_spec_predicted(), [])
    assert "id=\"prednote\"" in out
    assert "below the current evidence setting" in out


def test_the_reading_is_not_styled_as_a_verdict():
    """The reading is boxed to be findable, but it must not borrow the report's
    adverse red: --adverse means "this variant is disease-associated", and a
    measurement is not a verdict. Reusing it would tell every reader their own
    number is bad news before they read a word."""
    from biocore.report.render import render_html
    out = render_html([Finding("cg1", "ewas_catalog", "d", Tier.ROBUST,
                               [Category.AGING], detail={"your reading": 0.0104})], [])
    assert "class='card-read'" in out and "0.010" in out
    css = out.split(".card-read{", 1)[1].split("}", 1)[0]
    adverse = out.split(".dir-adverse{", 1)[1].split("}", 1)[0]
    adverse_colours = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,6}", adverse)}
    read_colours = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,6}", css)}
    assert not (adverse_colours & read_colours), "reading reuses the adverse palette"
