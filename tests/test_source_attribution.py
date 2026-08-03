"""Every provider that reaches a report must be attributable.

The Data sources panel is the attribution surface, and several licences make
attribution a condition of use rather than a courtesy.
"""
from biocore.report.sources import SOURCES


def test_uniprot_is_registered_with_its_attribution_licence():
    # protein function text is quoted from UniProt, which is CC BY 4.0 —
    # attribution is a licence condition, not optional
    s = SOURCES["uniprot"]
    assert "CC BY" in s.license
    assert s.url.startswith("https://")
    assert not s.noncommercial


def test_curated_marker_reference_is_registered():
    # findings with source="marker_reference" already render in production;
    # without an entry they appear with no attribution for the papers behind them
    s = SOURCES["marker_reference"]
    assert s.name and s.blurb
