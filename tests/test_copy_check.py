"""biocore.copy.check: one test per rule, and a clean table returns []."""
import copy

import pytest

from biocore.copy.check import BANNED, check_field, check_table, errors


def _table(rows, **header):
    t = {"version": "1", "reviewed_by": [], "reviewed_on": None, "note": "A table.", "rows": rows}
    t.update(header)
    return t


GENE = {"BRCA2": {"sentence": "BRCA2 makes a protein that repairs broken DNA.", "source": "NCBI Gene",
                  "url": "https://www.ncbi.nlm.nih.gov/gene/?term=BRCA2%5Bsym%5D"}}
STEP = {"plp|any|any": {"how_sure": "ClinVar rates it {stars} of 4 stars, from {platform_words}.",
                        "next_step": "Ask a doctor."},
        "other|any|any": {"how_sure": "No classification.", "next_step": ""}}
COND = {"APC": {"name": "Familial adenomatous polyposis", "inheritance": "dominant",
                "url": "https://www.ncbi.nlm.nih.gov/medgen/?term=x"}}


def _msgs(problems):
    return " | ".join(p.message for p in problems)


def test_clean_tables_return_nothing():
    assert check_table("gene_function", _table(GENE)) == []
    assert check_table("clinical_next_step", _table(STEP)) == []
    assert check_table("condition_phrasing", _table(COND)) == []
    assert check_table("terms", _table({"het": {"label": "One altered copy",
                                                "sentence": "One copy is altered."}})) == []


def test_word_cap_is_per_sentence_at_25():
    s25 = " ".join(["word"] * 24) + " end."
    s26 = " ".join(["word"] * 25) + " end."
    assert check_field("gene_function", "X", "sentence", s25) == []
    assert check_field("gene_function", "X", "sentence", s25 + " " + s25) == []
    p = check_field("gene_function", "X", "sentence", s26)
    assert errors(p) and "26 words" in _msgs(p)


def test_terms_cap_is_40_for_the_whole_text():
    assert check_field("terms", "x", "sentence", " ".join(["w"] * 40)) == []
    assert "41 words" in _msgs(check_field("terms", "x", "sentence", "Short. " + " ".join(["w"] * 40)))


@pytest.mark.parametrize("text", ["Importantly, it's not X.", "To be honest, it is rare.",
                                  "This is not a Diagnosis.", "In other words, rare.",
                                  "Crucially, rare.", "Notably, rare.", "Rare by design.",
                                  "You will need a test.", "It is worth noting that.",
                                  "The point is rarity."])
def test_banned_constructions_error(text):
    assert errors(check_field("gene_function", "X", "sentence", text)), text


def test_not_a_bans_only_a_capital_as_written():
    # The case-insensitive form in the old GeneAsk regex banned "not a measurement",
    # which shipped terms use. The rule as written means a capital letter.
    assert BANNED.search("This is not a Diagnosis")
    assert not BANNED.search("It is not a measurement of you")


@pytest.mark.parametrize("text", ["This gene can diagnose a disease.", "You'll get it.",
                                  "It will definitely happen.", "You are at risk of it."])
def test_forbidden_clinical_phrasing_errors(text):
    assert "forbidden clinical phrasing" in _msgs(check_field("gene_function", "X", "sentence", text))


def test_templates_must_resolve():
    assert check_field("clinical_next_step", "k", "how_sure", "Rated {stars} of 4.") == []
    p = check_field("clinical_next_step", "k", "how_sure", "Rated {star} of 4.")
    assert errors(p) and "{star}" in _msgs(p)
    assert errors(check_field("clinical_next_step", "k", "how_sure", "Rated {stars of 4."))


def test_urls_https_and_allowlisted_host():
    assert errors(check_field("gene_function", "X", "url", "http://www.ncbi.nlm.nih.gov/gene/1"))
    assert errors(check_field("gene_function", "X", "url", "https://example.com/gene/1"))
    assert check_field("gene_function", "X", "url", "https://www.ncbi.nlm.nih.gov/gene/1") == []


def test_markup_errors():
    assert "markup" in _msgs(check_field("gene_function", "X", "sentence", "A <b>bold</b> gene."))
    assert "markup" in _msgs(check_field("gene_function", "X", "sentence", "A &amp; B."))


def test_enum_inheritance():
    assert errors(check_field("condition_phrasing", "APC", "inheritance", "maybe"))
    assert check_field("condition_phrasing", "APC", "inheritance", None) == []
    assert check_field("condition_phrasing", "APC", "inheritance", "x_linked") == []


def test_shape_and_header():
    t = _table(copy.deepcopy(GENE))
    t["rows"]["BRCA2"]["colour"] = "blue"
    del t["rows"]["BRCA2"]["source"]
    msgs = _msgs(check_table("gene_function", t))
    assert "unknown field 'colour'" in msgs and "field missing" in msgs
    assert errors(check_table("gene_function", _table(GENE, reviewed_by="morgan")))
    assert errors(check_table("gene_function", _table(GENE, reviewed_on="13/09/2026")))
    assert errors(check_table("gene_function", _table(GENE, reviewed_by=["morgan@x.org"])))
    assert check_table("gene_function", _table(GENE, reviewed_by=["morgan@x.org"],
                                               reviewed_on="2026-11-08")) == []
    assert errors(check_table("gene_function", {"rows": []}))
    assert errors(check_field("gene_function", "X", "sentence", 3))
    assert errors(check_field("gene_function", "X", "sentence", ""))
    assert check_field("clinical_next_step", "k", "next_step", "") == []
