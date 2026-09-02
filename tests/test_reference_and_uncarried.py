"""Tumour comparisons are reference biology; uncarried alleles hide by default."""
from biocore.providers.base import Finding, Tier, Category
from biocore.report.render import render_html


def _f(source, cat=Category.TRAIT, marker=None, **d):
    return Finding(marker=marker or "m" + source, source=source, description="x", tier=Tier.ROBUST,
                   categories=[cat], detail={"topic": "other", **d})


def test_gdc_renders_under_reference_biology_not_clinical():
    h = render_html([_f("gdc", Category.REFERENCE, project="TCGA-BRCA")], [], disclaimer_path="/x")
    assert "Reference biology" in h and "id='reference'" in h and "id='clinical'" not in h


def test_reference_sorts_after_traits():
    h = render_html([_f("gdc", Category.REFERENCE), _f("gwas_catalog")], [], disclaimer_path="/x")
    assert h.index("id='trait'") < h.index("id='reference'")


def test_uncarried_gwas_hidden_by_default_with_a_count():
    h = render_html([_f("gwas_catalog", marker="rs1", risk_allele_carried=False),
                     _f("gwas_catalog", marker="rs2", risk_allele_carried=True),
                     _f("gwas_catalog", marker="rs3", risk_allele_carried=False)], [], disclaimer_path="/x")
    assert h.count("data-carried='0'") == 2 and h.count("data-carried='1'") == 1
    assert "id='uncarried'" in h and "Show 2 associations for alleles you do not carry" in h
    assert "data-carried')!=='0'" in h


def test_no_control_when_nothing_is_uncarried():
    h = render_html([_f("gwas_catalog", risk_allele_carried=True)], [], disclaimer_path="/x")
    assert "id='uncarried'" not in h
