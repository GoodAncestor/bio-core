from biocore.providers.base import Finding, Tier, Category, Interpretation, ChainLink


def test_finding_defaults_keep_old_constructor_working():
    f = Finding(marker="13-1-A-G", source="clinvar", description="x",
                tier=Tier.ROBUST, categories=[Category.CLINICAL])
    assert f.interpretation is None
    assert f.evidence_chain == [] and f.promoted is False
    assert f.promoted_reason == "" and f.deeper_dive is None


def test_to_dict_nests_interpretation_and_chain():
    gene = ChainLink(kind="gene", label="BRCA2", id="NCBIGene:675",
                     url="https://www.ncbi.nlm.nih.gov/gene/675")
    interp = Interpretation(found="A change in BRCA2.", can_mean="Raises risk.",
                            how_sure="Several labs agree.", next_step="Confirm it.",
                            condition="Hereditary breast and ovarian cancer syndrome",
                            condition_ids=["MedGen:C0677776"], zygosity="het",
                            citations=[gene], copy_version="2026-09-02",
                            reviewed_by=["medical geneticist"])
    f = Finding(marker="13-1-A-G", source="clinvar", description="x",
                tier=Tier.ROBUST, categories=[Category.CLINICAL],
                interpretation=interp, evidence_chain=[gene], promoted=True,
                promoted_reason="Several labs agree.", deeper_dive="More detail.",
                deeper_dive_meta={"kind": "clinical"})
    d = f.to_dict()
    assert d["tier"] == "robust" and d["categories"] == ["clinical"]
    assert d["interpretation"]["condition_ids"] == ["MedGen:C0677776"]
    assert d["interpretation"]["citations"][0]["label"] == "BRCA2"
    assert d["evidence_chain"][0]["id"] == "NCBIGene:675"
    assert d["promoted"] is True and d["deeper_dive_meta"] == {"kind": "clinical"}
