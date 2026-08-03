"""A mixed report must say how its findings split by source.

A whole genome yields a handful of clinically significant variants while a
methylome yields hundreds of trait associations. Printed as one total, the genome
half is invisible; printed as two bare numbers, the small one reads as a failure.
Both the split and the reason it is uneven are therefore part of the contract.
"""
from biocore.providers.base import Finding, Tier, Category
from biocore.report.render import _modality_breakdown


def _f(marker, source, modality=None):
    detail = {"topic": "other"}
    if modality:
        detail["modality"] = modality
    return Finding(marker=marker, source=source, description="a finding",
                   tier=Tier.ROBUST, categories=[Category.AGING], detail=detail)


def _methylome(n):
    return [_f(f"cg{i:08d}", "ewas_catalog") for i in range(n)]


def _genome(n):
    return [_f(f"1-{i}-A-T", "clinvar_panel_157", modality="genome") for i in range(n)]


def test_mixed_report_reports_both_counts():
    html = _modality_breakdown(_methylome(717) + _genome(2))
    assert "717" in html and "2" in html
    assert "Methylome" in html and "Genome" in html


def test_mixed_report_explains_why_the_counts_are_uneven():
    # the number alone invites the wrong conclusion, so the reason is required
    html = _modality_breakdown(_methylome(717) + _genome(2))
    assert "not comparable" in html
    assert "not a" in html and "failed scan" in html


def test_single_modality_report_gets_no_breakdown():
    # a methylation-only report has nothing to split, and the explanation would
    # be describing a comparison the reader cannot see
    assert _modality_breakdown(_methylome(12)) == ""
    assert _modality_breakdown(_genome(3)) == ""


def test_empty_findings_is_not_a_breakdown():
    assert _modality_breakdown([]) == ""
