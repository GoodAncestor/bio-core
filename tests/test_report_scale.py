"""Top-N-per-section truncation: a large report must default to a readable
page (only the strongest cards shown), not a wall — while everything stays
in the DOM for search/filters and is reachable with a native, no-JS control."""
import re
from biocore.report.render import render_html, magnitude
from biocore.providers.base import Finding, Tier, Category


def _clinical(marker, tier, mag_hint=0, **detail):
    """A clinical finding whose stats are tuned so markers sort in a known,
    verifiable order (higher mag_hint -> stronger p-value -> ranks earlier)."""
    d = {"topic": "cancer"}
    d.update(detail)
    if mag_hint:
        d.setdefault("p", 10 ** (-3 - mag_hint))  # smaller p = larger magnitude
        d.setdefault("n", 5000)
    return Finding(marker, "clinvar", f"finding about {marker}", tier,
                    [Category.CLINICAL], detail=d)


def test_section_truncates_to_top_n_by_default():
    fs = [_clinical(f"m{i}", Tier.MODERATE, mag_hint=i) for i in range(40)]
    out = render_html(fs, [], top_n=15)
    assert out.count("class='card'") == 40, "every card must still be in the DOM"
    assert "<details class='more'>" in out
    assert "Show 25 more" in out


def test_no_truncation_control_when_section_fits():
    fs = [_clinical(f"m{i}", Tier.MODERATE, mag_hint=i) for i in range(10)]
    out = render_html(fs, [], top_n=15)
    assert "class='card'" in out
    assert "details class='more'" not in out


def test_top_n_is_configurable():
    fs = [_clinical(f"m{i}", Tier.MODERATE, mag_hint=i) for i in range(20)]
    out = render_html(fs, [], top_n=5)
    assert "Show 15 more" in out


def test_truncation_keeps_the_strongest_cards_visible():
    """The N cards rendered before the <details> boundary must be the N
    strongest — not just the first N in insertion order. This guards the
    marker sort's tie-break-by-magnitude fix: same-tier markers used to keep
    arbitrary insertion order, which would silently hide a stronger finding
    behind a weaker one once truncation was introduced."""
    # Same tier for all -> without a magnitude tiebreak the order is
    # insertion order (worst-first here, since m0 is weakest).
    fs = [_clinical(f"m{i}", Tier.MODERATE, mag_hint=i) for i in range(20)]
    out = render_html(fs, [], top_n=5)
    boundary = out.index("<details class='more'>")
    shown_region = out[:boundary]
    hidden_region = out[boundary:]
    # the 5 strongest (highest mag_hint => m19..m15) must be in the shown
    # region; the weakest (m0) must be in the hidden region.
    for marker in ("m19", "m18", "m17", "m16", "m15"):
        assert f"data-marker='{marker}'" in shown_region, marker
    assert "data-marker='m0'" in hidden_region


def test_all_findings_remain_queryable_when_truncated():
    """A card hidden by truncation is still in the DOM with its full set of
    data-* attributes, so the existing filter bar's applyFilter() (which
    walks the whole document, not just the visible slice) still reaches it."""
    fs = [_clinical(f"m{i}", Tier.ROBUST, mag_hint=i, topic="metabolic")
          for i in range(30)]
    out = render_html(fs, [], top_n=10)
    assert out.count("data-topic='metabolic'") == 30
    assert out.count("<li class='finding'") == 30


def test_shown_counter_excludes_cards_hidden_by_truncation():
    """The generated script must not count a finding as 'shown' just because
    it passes the tier/topic/mag filters — it also has to be visually
    reachable (not sitting inside a closed <details class='more'>). This is
    the literal risk called out in the spec: 10 on screen, counter claims a
    much bigger number."""
    fs = [_clinical(f"m{i}", Tier.ROBUST, mag_hint=i) for i in range(30)]
    out = render_html(fs, [], top_n=10)
    script = out[out.index("<script>"):out.index("</script>")]
    assert "det.open" in script
    assert "f.closest('details.more, details.rows-more')" in script
    # the finding-counting loop must gate on reachability before incrementing
    m = re.search(r"if\(ok\)\{[^}]*if\(!det\|\|det\.open\)shown\+\+;", script)
    assert m, "shown++ must be gated on the closed-details check"


def test_more_summary_recomputes_against_live_filters():
    """expanding must respect the active filters: the per-section summary
    label is recalculated from cards that still pass the filter bar, not the
    static count baked in at render time."""
    fs = [_clinical(f"m{i}", Tier.ROBUST, mag_hint=i) for i in range(20)]
    out = render_html(fs, [], top_n=5)
    script = out[out.index("<script>"):out.index("</script>")]
    assert "moreDetails.forEach" in script
    assert "'Show '+matching+' more'" in script


def test_print_stylesheet_forces_truncated_cards_visible():
    fs = [_clinical(f"m{i}", Tier.MODERATE, mag_hint=i) for i in range(20)]
    out = render_html(fs, [], top_n=5)
    style = out[out.index("<style>"):out.index("</style>")]
    assert "@media print" in style
    assert "details.more:not([open])>*:not(summary){display:block !important}" in style
    # summary text is a JS-computed default; the print rule must not depend
    # on JS having run (belt-and-suspenders: also see the beforeprint JS)
    assert "beforeprint" in out


def test_details_is_keyboard_reachable_with_no_js():
    """<details>/<summary> is a native browser disclosure — no tabindex or
    JS required for it to be focusable and operable via Enter/Space."""
    fs = [_clinical(f"m{i}", Tier.MODERATE, mag_hint=i) for i in range(20)]
    out = render_html(fs, [], top_n=5)
    assert "<details class='more'><summary>" in out
    assert "tabindex" not in out.split("<script>")[0]  # no manual focus hacks needed


def test_marker_sort_breaks_ties_by_magnitude():
    """Direct check on the sort itself (independent of truncation): among
    same-tier markers, the one with the strongest supporting stats sorts
    first, matching the 'strongest-first' promise in the module docstring."""
    weak = _clinical("weak", Tier.MODERATE, p=0.5, n=100)
    strong = _clinical("strong", Tier.MODERATE, p=1e-9, n=500000)
    out = render_html([weak, strong], [])
    assert out.index("data-marker='strong'") < out.index("data-marker='weak'")
