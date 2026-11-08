# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""One validator for the copy tables, so the tests, a save path and a publish
gate all apply the same rules.

    check_table(name, table) -> list[Problem]
    check_field(name, key, field, value) -> list[Problem]

Every table has the same shape:

    {"version": "1", "reviewed_by": [...], "reviewed_on": "YYYY-MM-DD" | null,
     "note": "...", "rows": {"<key>": {"<field>": "<value>"}}}

Severity: an "error" means the text must not ship. A "warning" never blocks.

Before this module the rules lived as two regexes in two test files
(GeneAsk tests/test_copy_tables.py and bio-core tests/test_terms.py). BANNED is
their union. One deliberate change: the GeneAsk regex had `not a [A-Z]` under
re.I, where the flag turns [A-Z] into any letter, so it banned every "not a"
followed by a word. Here the case flag is switched off for that one branch, so
it bans "not a" followed by a capital letter, as written. The shipped terms
"group" and "prediction" contain "not a measurement" and "not a laboratory
classification", which the case-insensitive form would reject.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field as _field
from datetime import date
from urllib.parse import urlparse

VERSION = "1"

# Banned constructions. Union of the GeneAsk and bio-core test regexes, plus the
# two model-draft-only entries from DNA-Report dnareport/explain.py `_BANNED`
# (`buckle up`, `dive in`) so this list is a superset of all three.
BANNED = re.compile(
    r"honest|it's not |(?-i:not a [A-Z])|the point is|worth (noting|being explicit)"
    r"|in other words|crucially|importantly,|notably,|by design|you have|you will"
    r"|buckle up|dive in",
    re.I,
)

# Forbidden clinical phrasing: diagnosis claims and second-person prediction.
# Copied from DNA-Report dnareport/explain.py `_FORBIDDEN` (bio-core cannot import
# the app). Matched case-insensitively as substrings, as explain.py does.
FORBIDDEN = (
    "you have ",
    "you will ",
    "you'll ",
    "diagnos",
    "you are at risk",
    "you suffer",
    "guarantee",
    "certainly",
    "definitely",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MARKUP = re.compile(r"[<>]|&(#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
HEADER_KEYS = ("version", "reviewed_by", "reviewed_on", "rows")


@dataclass(frozen=True)
class Problem:
    severity: str          # "error" | "warning"
    row: str | None        # row key, or None for the header / table
    field: str | None
    message: str


@dataclass(frozen=True)
class Field:
    """What a field holds and which rules apply to it.

    kind: "sentence" (prose, word-capped), "template" (prose with {placeholders}),
    "label" (short prose), "url", "enum". `per` says whether `max_words` caps each
    sentence ("sentence") or the whole value ("field").
    """
    kind: str
    max_words: int | None = None
    per: str = "sentence"
    editable: bool = True
    nullable: bool = False
    allow_empty: bool = False
    placeholders: frozenset = frozenset()
    hosts: tuple = ()
    choices: tuple = ()


_NCBI = ("www.ncbi.nlm.nih.gov",)
_INHERITANCE = ("dominant", "recessive", "x_linked")
_STEP = Field("template", 25, placeholders=frozenset({"stars", "platform_words"}),
              allow_empty=True)

# Per table: the rules for each row field, and the word cap for the header note.
# Caps differ on purpose: 25 per sentence for the GeneAsk copy tables (the rule in
# tests/test_copy_tables.py), 40 for the whole of a term's sentence (the rule in
# tests/test_terms.py). Tightening terms would fail text already shipped.
RULES: dict[str, dict[str, Field]] = {
    "clinical_next_step": {"how_sure": _STEP, "next_step": _STEP},
    "gene_function": {"sentence": Field("sentence", 25),
                      "source": Field("label", 25, editable=False),
                      "url": Field("url", hosts=_NCBI)},
    "condition_phrasing": {"name": Field("label", 25),
                           "inheritance": Field("enum", choices=_INHERITANCE, nullable=True),
                           "url": Field("url", hosts=_NCBI, nullable=True)},
    # Published ACMG SF v3.2 facts; only `note` is ours to write.
    "acmg_sf_v3_2": {"condition": Field("label", 25, editable=False),
                     "inheritance": Field("enum", choices=_INHERITANCE, editable=False),
                     "note": Field("sentence", 25, allow_empty=True)},
    "terms": {"label": Field("label", 25),
              "sentence": Field("sentence", 40, per="field")},
}
_NOTE = Field("sentence", 25, allow_empty=True)


def _err(row, fld, msg):
    return Problem("error", row, fld, msg)


def voice_problems(text: str, *, max_words: int | None = 25, per: str = "sentence",
                   row: str | None = None, fld: str | None = None) -> list[Problem]:
    """House-voice rules for one piece of prose: banned constructions, forbidden
    clinical phrasing, markup, and the word cap."""
    out: list[Problem] = []
    for m in BANNED.finditer(text):
        out.append(_err(row, fld, f"banned construction: {m.group(0)!r}"))
    low = text.lower()
    for phrase in FORBIDDEN:
        if phrase in low:
            out.append(_err(row, fld, f"forbidden clinical phrasing: {phrase.strip()!r}"))
    if _MARKUP.search(text):
        out.append(_err(row, fld, "markup: no <, > or HTML entities; copy is escaped "
                                  "at render and would show as literal text"))
    if max_words:
        parts = _SENTENCE_SPLIT.split(text) if per == "sentence" else [text]
        for part in parts:
            n = len(part.split())
            if n > max_words:
                where = "sentence" if per == "sentence" else "text"
                out.append(_err(row, fld, f"{where} has {n} words (cap {max_words}): {part}"))
    return out


def _template_problems(rule: Field, value: str, row, fld) -> list[Problem]:
    out: list[Problem] = []
    try:
        names = {n for _, n, _, _ in string.Formatter().parse(value) if n is not None}
    except ValueError as e:
        return [_err(row, fld, f"template does not parse: {e}")]
    unknown = sorted(n for n in names if n not in rule.placeholders)
    if unknown:
        allowed = ", ".join("{" + p + "}" for p in sorted(rule.placeholders))
        out.append(_err(row, fld, f"unknown placeholder {', '.join('{'+n+'}' for n in unknown)}; "
                                  f"allowed: {allowed}"))
    else:
        try:
            value.format(**{p: "x" for p in rule.placeholders})
        except (KeyError, IndexError, ValueError) as e:
            out.append(_err(row, fld, f"template does not fill: {e!r}"))
    return out


def check_field(name: str, key: str | None, field: str, value) -> list[Problem]:
    """Every rule that applies to one field value. The per-keystroke path."""
    rules = RULES[name]
    if field not in rules:
        return [_err(key, field, f"unknown field {field!r} for {name}")]
    rule = rules[field]
    if value is None:
        return [] if rule.nullable else [_err(key, field, "missing value")]
    if not isinstance(value, str):
        return [_err(key, field, f"expected text, got {type(value).__name__}")]
    if rule.kind == "enum":
        if value not in rule.choices:
            return [_err(key, field, f"{value!r} is not one of {', '.join(rule.choices)}")]
        return []
    if rule.kind == "url":
        u = urlparse(value)
        out = []
        if u.scheme != "https":
            out.append(_err(key, field, "URL must use https"))
        if rule.hosts and u.netloc not in rule.hosts:
            out.append(_err(key, field, f"URL host {u.netloc!r} is not allowed "
                                        f"(allowed: {', '.join(rule.hosts)})"))
        return out
    if not value.strip():
        return [] if rule.allow_empty else [_err(key, field, "empty text")]
    out = voice_problems(value, max_words=rule.max_words, per=rule.per, row=key, fld=field)
    if rule.kind == "template":
        out += _template_problems(rule, value, key, field)
    return out


def check_header(name: str, table) -> list[Problem]:
    if not isinstance(table, dict):
        return [_err(None, None, "table is not an object")]
    out = [_err(None, k, f"header key {k!r} missing") for k in HEADER_KEYS if k not in table]
    v = table.get("version")
    if "version" in table and (not isinstance(v, str) or not v.strip()):
        out.append(_err(None, "version", "version must be a non-empty string"))
    who = table.get("reviewed_by")
    if "reviewed_by" in table and (not isinstance(who, list)
                                   or not all(isinstance(w, str) and w.strip() for w in who)):
        out.append(_err(None, "reviewed_by", "reviewed_by must be a list of identities"))
    on = table.get("reviewed_on")
    if on is not None:
        ok = isinstance(on, str) and _ISO_DATE.fullmatch(on)
        if ok:
            try:
                date.fromisoformat(on)
            except ValueError:
                ok = False
        if not ok:
            out.append(_err(None, "reviewed_on", "reviewed_on must be an ISO date (YYYY-MM-DD)"))
    if isinstance(who, list) and who and on is None:
        out.append(_err(None, "reviewed_on", "reviewed_by is set but reviewed_on is not"))
    note = table.get("note")
    if note is not None:
        if not isinstance(note, str):
            out.append(_err(None, "note", "note must be text"))
        else:
            out += voice_problems(note, max_words=_NOTE.max_words, fld="note")
    return out


def check_table(name: str, table) -> list[Problem]:
    """Every problem in a whole table, header first, then rows in order."""
    rules = RULES[name]
    out = check_header(name, table)
    if not isinstance(table, dict) or "rows" not in table:
        return out
    rows = table["rows"]
    if not isinstance(rows, dict):
        return out + [_err(None, "rows", "rows must be an object keyed by row key")]
    for key, row in rows.items():
        if not isinstance(row, dict):
            out.append(_err(key, None, "row is not an object"))
            continue
        for fld in row:
            if fld not in rules:
                out.append(_err(key, fld, f"unknown field {fld!r}"))
        for fld in rules:
            if fld not in row:
                out.append(_err(key, fld, "field missing"))
            else:
                out += check_field(name, key, fld, row[fld])
    return out


def errors(problems) -> list[Problem]:
    return [p for p in problems if p.severity == "error"]


def format_problems(problems) -> str:
    return "\n".join(f"{p.severity}: {p.row or '-'}.{p.field or '-'}: {p.message}" for p in problems)
