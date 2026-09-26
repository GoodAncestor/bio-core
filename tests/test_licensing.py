from biocore.licensing import (commercial_mode, output_mode, output_policy,
    prediction_license, atlas_output_allowed, filter_findings_for_output)
from biocore.providers.base import Finding, Tier, Category, Interpretation
from biocore.report.sources import enrichments_used


def finding(source="clinvar", **detail):
    return Finding("1-100-A-G", source, "ClinVar context — AlphaGenome predicts 0.99", Tier.SPECULATIVE,
                   [Category.CLINICAL], detail=detail,
                   interpretation=Interpretation("Restricted model says0.99", "x", "x"),
                   deeper_dive="Restricted score0.99")


def test_mode_default_and_invalid_fail_closed(monkeypatch):
    monkeypatch.delenv("DNAREPORT_OUTPUT_MODE", raising=False)
    assert output_mode() == "noncommercial" and not commercial_mode()
    monkeypatch.setenv("DNAREPORT_OUTPUT_MODE", "commmercial")
    assert commercial_mode() and not output_policy()["configuration_valid"]
    assert output_policy()["configuration_note"]


def test_source_route_policy_distinguishes_same_score():
    api = {"provenance": "alphagenome_atlas_api", "avi_score": .9, "tracks": [{"scorer": "AVI_SCORE"}]}
    local = {**api, "provenance": "alphagenome_atlas_local_avi"}
    assert not atlas_output_allowed(api) and atlas_output_allowed(local)
    assert not atlas_output_allowed({**local, "tracks": [{"scorer": "AVI_SCORE_FEATURE_IMPORTANCE"}]})
    assert prediction_license("alphagenome_atlas_local_avi")["commercial_allowed"]
    assert not prediction_license("alphagenome_atlas_api")["commercial_allowed"]
    assert enrichments_used(finding(alphagenome_atlas=local))[0].key == "alphagenome_atlas_avi"
    assert enrichments_used(finding(alphagenome_atlas=api))[0].noncommercial


def test_commercial_filter_removes_legacy_scores_and_derived_prose_without_mutation(monkeypatch):
    monkeypatch.setenv("DNAREPORT_OUTPUT_MODE", "commercial")
    original = finding(alphagenome={"quantile_score": .99}, clinical_significance="Uncertain significance")
    cleaned = filter_findings_for_output([original])[0]
    assert cleaned.description == "ClinVar context"
    assert cleaned.interpretation is None and cleaned.deeper_dive is None
    assert "alphagenome" not in cleaned.detail
    assert "alphagenome" in original.detail and original.interpretation is not None
    assert not filter_findings_for_output([finding("alphamissense")])
    assert not filter_findings_for_output([finding("variant_lookup", alphamissense={"pathogenicity": .9})])


def test_local_avi_survives_and_api_only_avi_does_not(monkeypatch):
    monkeypatch.setenv("DNAREPORT_OUTPUT_MODE", "commercial")
    api = {"provenance": "alphagenome_atlas_api", "avi_score": .9}
    local = {**api, "provenance": "alphagenome_atlas_local_avi"}
    kept = filter_findings_for_output([finding("variant_lookup", alphagenome_atlas=local),
        finding("variant_lookup", alphagenome_atlas=api)])
    assert len(kept) == 1 and kept[0].detail["alphagenome_atlas"] == local


def test_direct_render_cannot_bypass_policy_and_local_counts(monkeypatch):
    from biocore.report.render import render_html
    monkeypatch.setenv("DNAREPORT_OUTPUT_MODE", "commercial")
    api = {"provenance": "alphagenome_atlas_api", "avi_score": .998877}
    local = {"provenance": "alphagenome_atlas_local_avi", "avi_score": .75,
             "tracks": [{"scorer": "AVI_SCORE", "raw_score": .75}]}
    html = render_html([finding("variant_lookup", alphagenome_atlas=api),
                        finding("variant_lookup", alphagenome_atlas=local)], [])
    assert "0.998877" not in html and "0.75" in html
    assert "1 variant with predictions" in html
    assert "Permissive" in html and "Terms of use" in html
