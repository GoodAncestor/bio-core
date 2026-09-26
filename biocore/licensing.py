# SPDX-License-Identifier: Apache-2.0
"""Shared output-use policy. Data provenance, not score names, determines access."""
from __future__ import annotations
from copy import deepcopy
import os
import re

_TERMS = "https://deepmind.google.com/science/alphagenome/terms"
_OUTPUT_TERMS = "https://deepmind.google.com/science/alphagenome/output-terms"
_LICENSES = {
    "alphamissense": dict(id="CC-BY-NC-SA-4.0", label="AlphaMissense", commercial_allowed=False,
        terms_url="https://creativecommons.org/licenses/by-nc-sa/4.0/", scope="Non-commercial research only"),
    "alphagenome": dict(id="AlphaGenome-Output-Terms", label="AlphaGenome API output", commercial_allowed=False,
        terms_url=_OUTPUT_TERMS, scope="Non-commercial use only"),
    "alphagenome_atlas_api": dict(id="AlphaGenome-Output-Terms", label="AlphaGenome Atlas API output", commercial_allowed=False,
        terms_url=_OUTPUT_TERMS, scope="Non-commercial API access; this app excludes API-returned AVI from commercial output"),
    "alphagenome_atlas_local_avi": dict(id="AlphaGenome-Permissive-Artifact", label="Downloaded AVI SNV scores", commercial_allowed=True,
        terms_url=_TERMS, scope="Permissive downloadable artifact for commercial and non-commercial use"),
    "alphagenome_atlas_splicing": dict(id="AlphaGenome-Output-Terms", label="Downloaded merged splicing scores", commercial_allowed=False,
        terms_url=_OUTPUT_TERMS, scope="Non-commercial use only"),
    "alphagenome_atlas_features": dict(id="AlphaGenome-Output-Terms", label="Downloaded AVI feature importance", commercial_allowed=False,
        terms_url=_OUTPUT_TERMS, scope="Non-commercial use only"),
}


def output_mode():
    """Default preserves non-commercial operation; invalid settings fail closed."""
    value = os.getenv("DNAREPORT_OUTPUT_MODE", "noncommercial").strip().lower()
    return "noncommercial" if value in ("noncommercial", "non-commercial") else "commercial"


def output_policy():
    raw = os.getenv("DNAREPORT_OUTPUT_MODE", "noncommercial").strip().lower()
    valid = raw in ("noncommercial", "non-commercial", "commercial")
    return {"mode": output_mode(), "configuration_valid": valid,
            "configuration_note": "" if valid else
                "Unrecognized DNAREPORT_OUTPUT_MODE; commercial restrictions applied until configuration is corrected."}


def commercial_mode():
    return output_mode() == "commercial"


def prediction_license(kind):
    """Fresh metadata for a known dataset/access route; unknown routes are restricted."""
    result = dict(_LICENSES.get(kind, dict(id="unknown", label=kind,
        commercial_allowed=False, terms_url=_TERMS, scope="Commercial eligibility not established")))
    result["terms_verified_at"] = "2026-09-26"
    result["policy_basis"] = "Dataset and access-route eligibility under this application's output policy"
    return result


def atlas_output_allowed(detail):
    """Only the downloaded AVI artifact is permissive, not API AVI or mixtures."""
    if not isinstance(detail, dict) or detail.get("provenance") != "alphagenome_atlas_local_avi":
        return False
    metadata = detail.get("license") or {}
    if not isinstance(metadata, dict) or metadata.get("commercial_allowed") is False:
        return False
    tracks = detail.get("tracks") or []
    return bool("avi_score" in detail or tracks) and all(
        isinstance(track, dict) and track.get("scorer") == "AVI_SCORE" for track in tracks)


def filter_findings_for_output(findings):
    """Copy findings and remove restricted predictions and their derived prose.

    The app must also rebuild/filter report-level outcomes, actions, notes and
    coverage. This helper intentionally never modifies the stored original.
    """
    if not commercial_mode():
        return list(findings)
    copied = deepcopy(list(findings))
    out = []
    for finding in copied:
        source = (finding.source or "").lower()
        detail = finding.detail or {}
        atlas = detail.get("alphagenome_atlas")
        standalone_atlas = source.startswith("alphagenome_atlas")
        if source.startswith("alphamissense") or (source.startswith("alphagenome") and not standalone_atlas):
            continue
        if standalone_atlas and not atlas_output_allowed(atlas or detail):
            continue
        removed = []
        for key in ("alphamissense", "alphagenome"):
            if key in detail:
                removed.append(key)
                del detail[key]
        if atlas and not atlas_output_allowed(atlas):
            removed.append("alphagenome_atlas")
            del detail["alphagenome_atlas"]
        for key in ("alphagenome_atlas_splicing", "alphagenome_atlas_features"):
            if key in detail:
                removed.append(key)
                del detail[key]
        if removed:
            # Never retain model-composed text with the underlying scores removed.
            description = finding.description or ""
            split = re.split(r"\s+[—–-]\s+Alpha(?:Genome|Missense)\b", description, maxsplit=1)
            if len(split) > 1 and not re.search(r"Alpha(?:Genome|Missense)", split[0], re.I):
                finding.description = split[0]
            else:
                gene = str(detail.get("gene") or "").strip()
                sig = str(detail.get("clinical_significance") or "").strip()
                finding.description = (f"{gene + ': ' if gene else ''}{sig}" if sig else
                                       f"Variant {finding.marker}: non-commercial model output withheld.")
            finding.interpretation = None
            finding.deeper_dive = None
            finding.deeper_dive_meta = {}
            finding.evidence_chain = []
            finding.promoted = False
            finding.promoted_reason = ""
            for key in ("provenance", "interpretation", "deeper_dive", "evidence_chain", "prediction_summary"):
                detail.pop(key, None)
            if source in ("variant_lookup", "novel_variant") and not (
                detail.get("clinical_significance") or detail.get("gnomad") or detail.get("alphagenome_atlas")):
                continue
        finding.detail = detail
        out.append(finding)
    return out
