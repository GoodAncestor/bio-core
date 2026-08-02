"""Direction of effect: shown only where ClinVar itself asserts one.

This is our answer to Promethease's "repute", with one deliberate difference:
repute is a contributor's good/bad judgement, whereas this reports only what the
source states and stays silent otherwise.
"""
from biocore.report.render import direction, render_html
from biocore.providers.base import Finding, Tier, Category, ProviderStatus


def _f(sig=None, **extra):
    d = dict(extra)
    if sig is not None:
        d["clinical_significance"] = sig
    return Finding("m", "clinvar", "d", Tier.MODERATE, [Category.CLINICAL], detail=d)


def test_pathogenic_readings_are_adverse():
    for sig in ("Pathogenic", "Likely pathogenic", "PATHOGENIC",
                "Pathogenic/Likely pathogenic", "risk factor"):
        assert direction(_f(sig)) == "adverse", sig


def test_benign_readings_are_not_disease_causing():
    for sig in ("Benign", "Likely benign", "Benign/Likely benign"):
        assert direction(_f(sig)) == "benign", sig


def test_protective_and_drug_response():
    assert direction(_f("protective")) == "protective"
    assert direction(_f("drug response")) == "actionable"


def test_contested_calls_get_no_direction():
    """The precedence bug this guards: ClinVar significance is free text, so
    'Conflicting classifications of pathogenicity; other; risk factor' contains
    both 'pathogenicity' and 'risk factor' while asserting neither. A contested
    variant must come back unset, never as the scarier reading."""
    for sig in ("Conflicting classifications of pathogenicity",
                "Conflicting classifications of pathogenicity; other; risk factor",
                "Uncertain significance",
                "not provided",
                "association",
                "Affects"):
        assert direction(_f(sig)) == "", sig


def test_absent_or_empty_significance_is_unset():
    assert direction(_f()) == ""
    assert direction(_f("")) == ""
    assert direction(Finding("m", "geneask", "d", Tier.ROBUST,
                             [Category.TRAIT], detail={"gene": "MCM6"})) == ""


def test_traits_never_get_a_direction():
    """Lactase persistence is not 'good'. A trait with no ClinVar call must stay
    unlabelled rather than inheriting a valence we invented."""
    trait = Finding("rs4988235", "geneask", "Lactase persistence", Tier.ROBUST,
                    [Category.TRAIT], detail={"topic": "metabolic", "n": 120000})
    assert direction(trait) == ""


def test_report_marks_and_filters_by_direction():
    fs = [_f("Pathogenic", gene="PKD1"), _f("Benign", gene="TP53"),
          Finding("rs1", "geneask", "a trait", Tier.ROBUST, [Category.TRAIT], detail={})]
    out = render_html(fs, [ProviderStatus(name="ClinVar", health=None)]
                      if False else [], tool_version="0")
    assert "dir-adverse" in out and "dir-benign" in out
    assert "Disease-associated" in out and "Not disease-causing" in out
    assert "<select id='dirfilter'>" in out, "filter should render when directions exist"
    assert out.count("data-direction=''") == 1, "the trait must carry an empty direction"


def test_filter_is_absent_when_nothing_is_classified():
    fs = [Finding("rs1", "geneask", "a trait", Tier.ROBUST, [Category.TRAIT], detail={})]
    out = render_html(fs, [], tool_version="0")
    # the script always *references* the control (null-guarded), so assert on the
    # control itself rather than the id appearing anywhere in the document
    assert "<select id='dirfilter'>" not in out


# ---- modifiers are not uncertainty (regression: shipped-panel misclassification)

def test_pathogenic_with_a_modifier_stays_adverse():
    """"Pathogenic; Affects" is a real value in the bundled 157-gene panel
    (SLC26A4, Pendred syndrome). ClinVar joins terms with ';' and "Affects" is a
    MODIFIER layered on a classification, not a classification of its own.
    Treating it as an uncertainty marker stripped the Disease-associated flag off
    a pathogenic variant and — worse — hid it from the significance filter."""
    for sig in ("Pathogenic; Affects", "Pathogenic; other", "Pathogenic; association",
                "Likely pathogenic; risk factor", "Pathogenic/Likely pathogenic; other"):
        assert direction(_f(sig)) == "adverse", sig


def test_clinvar_risk_allele_vocabulary_is_recognised():
    """ClinVar's newer risk-allele terms assert elevated risk just as the older
    'risk factor' does, and were previously invisible to the classifier."""
    for sig in ("Likely risk allele", "Established risk allele", "risk allele"):
        assert direction(_f(sig)) == "adverse", sig


def test_drug_response_survives_a_benign_classification():
    """"Benign; drug response" is not merely reassuring. The pharmacogenomic
    implication is the actionable part; reporting it as 'Not disease-causing'
    drops the only clinically useful thing the record carries."""
    for sig in ("Benign; drug response", "Likely benign; drug response",
                "Benign/Likely benign; drug response"):
        assert direction(_f(sig)) == "actionable", sig


def test_bare_modifiers_still_assert_nothing():
    for sig in ("Affects", "association", "other"):
        assert direction(_f(sig)) == "", sig


def test_conflicting_still_beats_a_pathogenic_substring():
    assert direction(_f("Conflicting classifications of pathogenicity; other; risk factor")) == ""
