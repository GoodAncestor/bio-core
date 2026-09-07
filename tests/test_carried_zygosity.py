"""carried_variants() reports zygosity and call quality as fields, not prose."""
import pytest

pytest.importorskip("pysam")

from biocore.variants.carried import carried_variants


_VCF = """##fileformat=VCFv4.2
##contig=<ID=13>
##contig=<ID=X>
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FILTER=<ID=LowQual,Description="Low quality">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
13\t32316419\t.\tCAG\tC\t812.5\tPASS\t.\tGT:GQ:DP\t0/1:99:41
13\t32316500\t.\tA\tG\t50\tLowQual\t.\tGT:GQ:DP\t1/1:12:6
X\t1000\t.\tT\tC\t.\t.\t.\tGT\t1
"""


def _vcf(tmp_path):
    p = tmp_path / "s.vcf"
    p.write_text(_VCF)
    return str(p)


def test_het_call_carries_quality_fields(tmp_path):
    rows = {r["variant_id"]: r for r in carried_variants(_vcf(tmp_path))}
    r = rows["13-32316419-CAG-C"]
    assert r["zygosity"] == "het"
    assert r["filter"] == "PASS"
    assert r["qual"] == 812.5
    assert r["gq"] == 99 and r["dp"] == 41


def test_hom_and_filter_label(tmp_path):
    rows = {r["variant_id"]: r for r in carried_variants(_vcf(tmp_path))}
    r = rows["13-32316500-A-G"]
    assert r["zygosity"] == "hom"
    assert r["filter"] == "LowQual"


def test_haploid_is_hemi_and_missing_fields_are_none(tmp_path):
    rows = {r["variant_id"]: r for r in carried_variants(_vcf(tmp_path))}
    r = rows["X-1000-T-C"]
    assert r["zygosity"] == "hemi"
    assert r["qual"] is None and r["gq"] is None and r["dp"] is None
    assert r["filter"] is None


def test_partial_diploid_call_has_unknown_zygosity(tmp_path):
    """A known ALT plus a missing allele does not establish hemizygosity."""
    vcf = tmp_path / "partial.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1>\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t10\t.\tA\tG\t60\tPASS\t.\tGT\t1/.\n"
    )
    row = carried_variants(str(vcf))[0]
    assert row["genotype"] == "./G"
    assert row["zygosity"] == "unknown"
