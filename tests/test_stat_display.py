"""Display fixes for two values that render misleadingly in study details."""
from biocore.providers.base import Finding, Tier, Category
from biocore.report.render import _fmt_stat, _entity_links


def _f(detail):
    return Finding(marker="cg1", source="ewas_catalog", description="d",
                   tier=Tier.ROBUST, categories=[Category.AGING], detail=detail)


def test_underflowed_p_value_is_not_shown_as_zero():
    # sources store 0 when the p-value underflows float — these are the STRONGEST
    # associations, and _boost already treats them that way. Printing "0.0e+00"
    # reads as "no significance" and contradicts the score beside it.
    out = _fmt_stat("p", 0.0)
    assert "0.0e+00" not in out
    assert "1e-300" in out


def test_ordinary_p_value_still_formats_normally():
    assert _fmt_stat("p", 2.7e-06) == "2.7e-06"


def test_placeholder_gene_symbols_are_not_linked():
    # the mirror stores "-" for an intergenic / unassigned probe; rendering it
    # produces a bare dash that links nowhere
    assert _entity_links(_f({"gene": "-"})) == ""
    assert _entity_links(_f({"gene": "NA"})) == ""


def test_real_gene_symbol_still_links():
    assert "ERGIC3" in _entity_links(_f({"gene": "ERGIC3"}))
