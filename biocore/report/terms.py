# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Terms a report uses, explained once at the end and linked from each card.

The glossary in the product repo explains TRAITS (per EWAS copy key). This one
explains the report's own vocabulary: zygosity, classifications, review stars,
effect sizes. One sentence or two per term, in the house voice. The text lives
in data/terms.json; biocore.copy.check holds the house-voice rules it must pass.
"""
from __future__ import annotations
import html as _html
import json as _json
from pathlib import Path as _Path

# The wording is data (`data/terms.json`) so a reviewer edits text, not Python.
# Same shape as the other copy tables: a header plus rows of {label, sentence}.
# TERMS keeps its old form, key -> (label, sentence), so callers are unchanged.
TERMS_PATH = _Path(__file__).resolve().parent / "data" / "terms.json"


def load_table() -> dict:
    """The whole terms table: version, reviewed_by, reviewed_on, note, rows."""
    return _json.loads(TERMS_PATH.read_text(encoding="utf-8"))


TERMS = {key: (row["label"], row["sentence"]) for key, row in load_table()["rows"].items()}


def term_anchor(key: str) -> str:
    return f"term-{key}"


def term_link(key: str, text: str) -> str:
    """A dotted-underline link from a card into the terms section."""
    return f"<a class='glossword' href='#{term_anchor(key)}'>{_html.escape(text)}</a>"


def terms_html(keys) -> str:
    """The terms section for the keys actually used, in first-use order. "" when none."""
    keys = [k for k in dict.fromkeys(keys) if k in TERMS]
    if not keys:
        return ""
    rows = "".join(f"<dt id='{term_anchor(k)}'>{_html.escape(TERMS[k][0])}</dt>"
                   f"<dd>{_html.escape(TERMS[k][1])}</dd>" for k in keys)
    return (f"<section id='terms'><h2>Terms used in this report</h2>"
            f"<dl class='terms'>{rows}</dl></section>")
