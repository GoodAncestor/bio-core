"""An interpreted finding renders as four parts; promoted findings open the report."""
from biocore.providers.base import (Finding, Tier, Category, Interpretation, ChainLink,
                                    ProviderStatus, Health)
from biocore.report.render import render_html


def _f(promoted=True, dive=None, reviewed=()):
    ip = Interpretation(
        found="BRCA2 makes a protein that helps cells repair broken DNA. ClinVar classifies this change as likely pathogenic for Hereditary breast and ovarian cancer syndrome.",
        can_mean="One altered copy was read. One altered copy is enough to raise the chance of the condition.",
        how_sure="ClinVar rates the classification 2 of 4 review stars.",
        next_step="Confirm the result with a clinical laboratory test before you act on it.",
        condition="Hereditary breast and ovarian cancer syndrome",
        condition_ids=["MedGen:C0677776"], zygosity="het", citations=[],
        copy_version="1", reviewed_by=list(reviewed))
    return Finding(marker="13-32316419-CAG-C", source="clinvar_mirror", description="old sentence",
                   tier=Tier.ROBUST, categories=[Category.CLINICAL],
                   detail={"gene": "BRCA2", "topic": "cancer", "gold_stars": 2,
                           "clinical_significance": "Likely pathogenic", "zygosity": "het"},
                   interpretation=ip,
                   evidence_chain=[ChainLink(kind="variant", label="13-32316419-CAG-C", url="https://x/v"),
                                   ChainLink(kind="gene", label="BRCA2", url="https://x/g"),
                                   ChainLink(kind="condition", label="HBOC", url="https://x/c")],
                   promoted=promoted, promoted_reason="Several labs agree this change is pathogenic",
                   deeper_dive=dive,
                   deeper_dive_meta={"backend": "openai_compat", "model": "GLM-5.3"} if dive else {})


def _html(fs, read_first=None):
    return render_html(fs, [ProviderStatus(name="clinvar", health=Health.OK)],
                       disclaimer_path="/nonexistent", read_first=read_first)


def test_card_has_four_parts_and_no_magnitude_number_on_its_face():
    h = _html([_f()])
    for lab in ("What was found", "What it can mean", "How sure", "Sensible next step"):
        assert lab in h
    assert "one altered copy" in h and "2 of 4 stars" in h
    card = h.split("class='finding meaning")[1].split("</li>")[0]
    assert "class='rail" not in card and "mag-n" not in card
    assert "data-mag=" in card
    assert "old sentence" not in card


def test_read_first_section_leads_and_says_why():
    h = _html([_f()], read_first=[_f()])
    assert h.index("id='read-first'") < h.index("id='clinical'")
    assert "Read this first" in h and "Several labs agree" in h
    assert "class='card first'" in h
    assert "href='#read-first'" in h


def test_chain_and_dive_are_native_details_and_labelled():
    h = _html([_f(dive="A longer explanation.")])
    assert "<details class='chain'>" in h and "https://x/c" in h
    assert "<details class='dive'>" in h and "AI-drafted" in h and "GLM-5.3" in h
    assert "wording not yet reviewed by a person" in h
    h2 = _html([_f(reviewed=["fabiola"])])
    assert "<details class='dive'>" not in h2 and "not yet reviewed" not in h2


def test_terms_section_only_when_a_card_uses_a_term():
    h = _html([_f()])
    assert "id='terms'" in h and "Review stars" in h and "href='#terms'" in h
    plain = Finding(marker="rs1", source="gwas_catalog", description="x", tier=Tier.ROBUST,
                    categories=[Category.TRAIT], detail={"topic": "other"})
    assert "id='terms'" not in _html([plain])


def test_tier_tooltip_names_what_it_measured():
    h = _html([_f(), Finding(marker="rs1", source="gwas_catalog", description="x", tier=Tier.ROBUST,
                             categories=[Category.TRAIT], detail={"topic": "other"})])
    assert "title='Robust: review stars'" in h and "title='Robust: p-value'" in h
