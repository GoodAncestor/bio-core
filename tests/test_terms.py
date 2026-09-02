import re
from biocore.report.terms import TERMS, terms_html, term_link, term_anchor

BANNED = re.compile(r"honest|it's not |the point is|in other words|crucially|importantly,|notably,|you will", re.I)


def test_every_term_is_short_and_plain():
    for key, (label, sentence) in TERMS.items():
        assert len(sentence.split()) <= 40, key
        assert not BANNED.search(sentence), key


def test_html_only_lists_requested_terms_and_links_resolve():
    html = terms_html(["het", "stars", "nope", "het"])
    assert "One altered copy" in html and "Review stars" in html and "Odds ratio" not in html
    assert html.count("<dt") == 2
    assert term_anchor("het") in term_link("het", "one altered copy")
    assert terms_html([]) == "" and terms_html(["nope"]) == ""
