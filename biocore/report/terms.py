# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Terms a report uses, explained once at the end and linked from each card.

The glossary in the product repo explains TRAITS (per EWAS copy key). This one
explains the report's own vocabulary: zygosity, classifications, review stars,
effect sizes. One sentence or two per term, in the house voice.
"""
from __future__ import annotations
import html as _html

TERMS = {
    "group": ("Group patterns", "A research association says that, across a group of people, a trait and a reading moved together on average. It is not a measurement of you, and it does not predict anything about you."),
    "gene": ("Gene", "A gene is a stretch of DNA that carries the recipe for one protein. "
                     "A change in the recipe can change the protein."),
    "het": ("One altered copy", "You carry two copies of most genes, one from each parent. "
                                "One altered copy means the other copy is unchanged."),
    "hom": ("Two altered copies", "Both copies of the gene carry the change."),
    "dominant": ("Dominant condition", "One altered copy is enough to raise the chance of the condition."),
    "recessive": ("Recessive condition", "Two altered copies are needed for the condition. One altered "
                                         "copy makes a carrier, who does not usually have the condition."),
    "plp": ("Pathogenic / likely pathogenic", "A laboratory classification of the change itself. It says "
                                              "the change can cause the condition. It does not say the "
                                              "condition is present."),
    "vus": ("Uncertain significance", "Laboratories do not yet know what this change does."),
    "stars": ("Review stars", "ClinVar's 0 to 4 scale for how well laboratories agree. 2 stars means "
                              "several laboratories agree with the same criteria. 4 means a practice guideline."),
    "af": ("Allele frequency", "How often a change appears among sampled chromosomes in a reference "
                               "population. Rare does not mean harmful."),
    "or": ("Odds ratio", "How many times higher the odds of a trait were in people with the allele, "
                         "compared with people without it, in one study."),
    "beta": ("Effect size (beta)", "How much a measured trait moved, on average, per copy of the allele, "
                                   "in one study."),
    "p": ("p-value", "How unlikely the study result would be if there were no real association. "
                     "Smaller is stronger. It says nothing about the size of the effect."),
    "prediction": ("Prediction score", "A computer model's estimate, not a laboratory classification. "
                                       "Models are wrong often enough that a prediction alone is not a result."),
    "array": ("Genotyping array", "A consumer test that reads a fixed set of positions. It misreads rare "
                                  "changes often, so a rare pathogenic call from an array needs a clinical test."),
    "methylation": ("Methylation", "A chemical mark on DNA that changes with age, tissue, exposure and "
                                   "disease. A reading is a value from 0 to 1 for one site."),
}


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
