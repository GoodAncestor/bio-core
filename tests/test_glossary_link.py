"""A finding whose trait has curated copy links to the glossary entry.

The copy lives once in a glossary rather than on each finding — "age" alone
appears 127 times on one demo page, so inlining restates it 127 times.
"""
from biocore.providers.base import Finding, Tier, Category
from biocore.report.render import _finding_line, glossary_anchor


def _f(detail):
    return Finding(marker="cg1", source="ewas_catalog", description="age — assoc",
                   tier=Tier.ROBUST, categories=[Category.AGING], detail=detail)


def test_finding_with_copy_links_to_its_glossary_anchor():
    html = _finding_line(_f({"topic": "aging", "copy_key": "age"}))
    assert f"#{glossary_anchor('age')}" in html


def test_finding_without_copy_has_no_glossary_link():
    html = _finding_line(_f({"topic": "aging"}))
    assert "glosslink" not in html


def test_anchor_is_stable_and_url_safe():
    a = glossary_anchor("_protein_level")
    assert a == glossary_anchor("_protein_level")
    assert " " not in a and "#" not in a
