"""The By outcome view: cards per consequence with the baseline element each can carry."""
import types
from biocore.providers.base import Finding, Tier, Category, Interpretation
from biocore.report.render import render_html, _position_bar, _contribution_strip


def _row(marker, label):
    return Finding(marker=marker, source="ewas_catalog", description="x", tier=Tier.ROBUST,
                   categories=[Category.TRAIT], detail={"topic": "metabolic", "short_label": label, "n": 1200},
                   interpretation=Interpretation(found=f"{label} — methylation here rises with it.",
                                                 can_mean="Group patterns.", how_sure=""))


def _score(pct=70, word="higher"):
    return types.SimpleNamespace(trait="type 2 diabetes", efo="EFO_0001360", n_variants=9, n_with_af=7,
                                 score=1.2, mean=0.8, sd=0.6, z=0.67, percentile=pct, direction_word=word,
                                 top=[("rs7903146", "TCF7L2", 0.31), ("rs1801282", "PPARG", -0.12)],
                                 caveat="Population reference is the whole gnomAD set; effect sizes and frequencies vary by ancestry.")


def _outcome(kind="trait", key="t2d", label="Type 2 diabetes", findings=(), score=None, contributions=(), actions=()):
    return types.SimpleNamespace(kind=kind, key=key, label=label, findings=list(findings), score=score,
                                 contributions=list(contributions), reference_groups=[], actions=list(actions))


def _html(outcomes, actions=None, findings=None):
    return render_html(findings or [], [], disclaimer_path="/x", outcomes=outcomes, actions=actions)


def test_trait_with_score_renders_the_position_bar_and_caveat():
    h = _html([_outcome(findings=[_row("cg1", "Type 2 diabetes")], score=_score())])
    assert "<section id='outcome' data-view='outcome'>" in h and "data-outcome='t2d'" in h
    assert "higher than about 70% of people in the reference set" in h
    assert "class='paxis'" in h and "style='left:70%'" in h
    assert "vary by ancestry" in h and "rs7903146" in h and "TCF7L2" in h
    assert "class='finding compact'" in h


def test_about_average_wording_and_no_bar_without_percentile():
    assert "about average for the reference set" in _position_bar(_score(50, "about average"))
    assert _position_bar(types.SimpleNamespace(percentile=None)) == ""


def test_age_outcome_renders_signed_contribution_bars():
    o = _outcome(kind="age", key="age", label="Epigenetic age",
                 contributions=[("Hannum2013_Blood", "cg1", 3.1), ("Levine2018_PhenoAge", "cg2", -1.4)])
    h = _html([o])
    assert "Epigenetic age" in h and "+3.1 yrs" in h and "-1.4 yrs" in h
    assert "mvbar up" in h and "mvbar down" in h and "relative to a zero reading" in h
    assert _contribution_strip([]) == ""
    five = _contribution_strip([("cg9", 1.0, 0.5, 0.5, 2.2), ("cg8", -1.0, 0.5, -0.5, -0.9)])
    assert "+2.2 yrs" in five and "-0.9 yrs" in five


def test_direction_only_outcome_says_so_and_actions_render_with_sources():
    o = _outcome(findings=[_row("cg1", "Body mass index")])
    a = types.SimpleNamespace(text="Confirm with a clinical test before acting.", why="ClinVar likely pathogenic in BRCA2",
                              source_label="ClinGen actionability", url="https://x/ac", outcome_key="hboc")
    h = _html([o], actions=[a])
    assert "the direction is known, your position is not" in h
    assert "<b>cg1</b> — methylation here rises with it." in h   # the site leads inside an outcome card
    assert "<section id='actions' data-view='first'>" in h and "What people do with results like these" in h
    assert "ClinGen actionability" in h and "https://x/ac" in h
    assert "href='#view=outcome'" in h
