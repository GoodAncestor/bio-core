"""The sample's own reading belongs on the marker card, once.

A card gathers every finding about one probe, so the reading is a property of
the card, not of each finding. Repeating it per line restates one number up to
30 times inside a single card — the same noise the count field already avoids.
"""
from biocore.providers.base import Finding, Tier, Category
from biocore.report.render import _marker_card


def _f(desc, reading=None):
    d = {"topic": "aging"}
    if reading is not None:
        d["your reading"] = reading
    return Finding(marker="cg00017842", source="ewas_catalog", description=desc,
                   tier=Tier.ROBUST, categories=[Category.AGING], detail=d)


def test_reading_appears_once_on_a_card_with_many_findings():
    fs = [_f(f"trait {i} — associated with lower methylation", 0.714) for i in range(30)]
    html = _marker_card("cg00017842", fs, None)
    assert html.count("0.714") == 1


def test_reading_is_shown_in_the_card_header_not_the_findings_list():
    fs = [_f("age — associated with lower methylation", 0.714)]
    html = _marker_card("cg00017842", fs, None)
    head = html.split("<ul class='findings'>")[0]
    assert "0.714" in head


def test_card_without_a_reading_is_unchanged():
    fs = [_f("age — associated with lower methylation")]
    html = _marker_card("cg00017842", fs, None)
    assert "your reading" not in html.lower()


def test_finding_count_still_shown_alongside_the_reading():
    fs = [_f("a", 0.5), _f("b", 0.5)]
    html = _marker_card("cg00017842", fs, None)
    assert "2 findings" in html and "0.500" in html
