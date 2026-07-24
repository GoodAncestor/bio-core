"""Source attribution + scan-summary panel rendering."""
from biocore.report.render import render_html
from biocore.report.sources import resolve, sources_used
from biocore.providers.base import Finding, Tier, Category


def _f(marker, source, **d):
    return Finding(marker, source, "desc", Tier.MODERATE, [Category.CLINICAL], detail=d)


def test_resolve_aliases():
    assert resolve("clinvar_panel_157").name == "ClinVar"
    assert resolve("gwas_catalog").name == "GWAS Catalog"
    assert resolve("alphamissense").noncommercial is True
    assert resolve("unified_callset") is None       # person's own callset, no attribution
    assert resolve("") is None


def test_sources_panel_and_friendly_names():
    fs = [_f("13-1-C-G", "clinvar_panel_157", topic="cancer"),
          _f("rs704", "gwas_catalog", topic="cardiovascular"),
          _f("1-1-G-T", "alphamissense", topic="clinical")]
    html = render_html(fs, [])
    assert "Google DeepMind AlphaMissense" in html    # friendly name in finding
    assert "id='sources'" in html or 'id="sources"' in html
    assert "CC BY" in html and "non-commercial" in html


def test_scan_summary_panel():
    fs = [_f("13-1-C-G", "clinvar", topic="cancer")]
    stats = {"input_bytes": 25000, "markers_scanned": 42, "findings_total": 758,
             "classified": 710, "uncertain": 48, "reference_variants_scanned": 12_200_000,
             "local_dbs_queried": ["ClinVar", "EWAS Catalog"], "live_apis_called": []}
    html = render_html(fs, [], scan_stats=stats)
    assert "class='scan'" in html
    assert "12M" in html                              # 12.2M reference records humanized
    assert "deleted" in html                          # data-deletion note present
    assert "ClinVar" in html


def test_all_known_source_keys_resolve():
    """Every source string emitted anywhere in the family resolves as intended:
    external DBs -> a Source (attribution), a person's own callset -> None."""
    from biocore.report.sources import resolve
    external = ["alphamissense", "clinvar_panel_157", "cpic", "epigenetic_clock",
                "gwas_catalog", "clocks", "ewas_catalog", "gdc", "methbank",
                "ewas_atlas", "clinvar", "pharmgkb", "opengwas"]
    internal = ["array_callset", "unified_callset", "biocore.modbam", "geneask.compare"]
    for s in external:
        assert resolve(s) is not None, f"{s} must resolve to a Source (attribution)"
    for s in internal:
        assert resolve(s) is None, f"{s} is the person's own data — no external attribution"
