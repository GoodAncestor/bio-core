"""The trait word in the sentence is the link into the glossary.

A "what this means" link buried at the end of the metadata row is the least
visible thing on the line. The reader's eye is on the trait name, so that is
what should be clickable.
"""
import re
from biocore.providers.base import Finding, Tier, Category
from biocore.report.render import _finding_line


def _f(desc, subject=None, key="age"):
    d = {"topic": "aging", "trait": "age"}
    if key: d["copy_key"] = key
    if subject: d["subject"] = subject
    return Finding(marker="cg1", source="ewas_catalog", description=desc,
                   tier=Tier.ROBUST, categories=[Category.AGING], detail=d)


def test_trait_word_in_the_sentence_is_linked():
    h = _finding_line(_f("age — associated with lower methylation at this site", "age"))
    desc = h.split("class='desc'")[1].split("</p>")[0]
    assert "#trait-age" in desc
    assert ">age</a>" in desc


def test_the_rest_of_the_sentence_is_not_swallowed_by_the_link():
    h = _finding_line(_f("age — associated with lower methylation at this site", "age"))
    desc = h.split("class='desc'")[1].split("</p>")[0]
    assert "associated with lower methylation" in re.sub(r"<[^>]+>", "", desc)
    assert desc.count("<a ") == 1


def test_protein_subject_with_spaces_is_linked_whole():
    h = _finding_line(_f("blood level of protein Alpha-2-macroglobulin — associated "
                         "with higher methylation at this site",
                         "blood level of protein Alpha-2-macroglobulin", "_protein_level"))
    desc = h.split("class='desc'")[1].split("</p>")[0]
    assert ">blood level of protein Alpha-2-macroglobulin</a>" in desc


def test_no_duplicate_link_in_the_metadata_row():
    h = _finding_line(_f("age — associated with lower methylation", "age"))
    assert h.count("#trait-age") == 1


def test_falls_back_to_the_meta_link_when_the_subject_is_not_in_the_sentence():
    h = _finding_line(_f("something else entirely", "age"))
    assert "glosslink" in h and "#trait-age" in h


def test_finding_without_copy_has_no_link_at_all():
    h = _finding_line(_f("age — associated with lower methylation", "age", key=None))
    assert "#trait-" not in h
