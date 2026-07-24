"""Carried-variant extractor (biocore.variants.carried) against a tiny VCF."""
import pytest
pytest.importorskip("pysam")
from biocore.variants.carried import carried_variants, n_samples


def _write_vcf(tmp_path):
    p = tmp_path / "s.vcf"
    p.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=13>\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "13\t32316419\t.\tCAG\tC\t100\tPASS\t.\tGT\t0/1\n"
        "13\t100\t.\tA\tG\t100\tPASS\t.\tGT\t0/0\n")   # hom-ref: not carried
    return str(p)


def test_n_samples(tmp_path):
    assert n_samples(_write_vcf(tmp_path)) == 1


def test_carried_skips_homref_and_normalises(tmp_path):
    cv = carried_variants(_write_vcf(tmp_path))
    assert len(cv) == 1                                  # hom-ref dropped
    assert cv[0]["variant_id"] == "13-32316419-CAG-C"    # chr stripped, chr-pos-ref-alt
    assert cv[0]["platform"] == "WGS"
