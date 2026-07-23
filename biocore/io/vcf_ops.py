"""VCF operations — thin, documented wrappers over bcftools.

Recovered from the eelgrass genetic-structure pipeline (frame 59ba13be). These
are organism-agnostic: reheader per-sample VCFs to unique IDs, merge with
absent→ref fill, and extract biallelic SNPs on a chromosome subset. The exact
bcftools invocations are preserved from the working pipeline.

Requires bcftools on PATH (htslib). Callers that only parse VCFs (no merge)
should use a pysam/cyvcf2 reader instead; these wrappers are for the
cohort-assembly steps that shell out to bcftools by design.
"""
from __future__ import annotations
import subprocess, os
from pathlib import Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def reheader(src_vcf: str, sample_name: str, out_vcf: str) -> str:
    """Rename the single sample in src_vcf to sample_name; bgzip + tabix out."""
    namefile = Path(out_vcf).with_suffix(".name.txt")
    namefile.write_text(sample_name + "\n")
    _run(["bcftools", "reheader", "-s", str(namefile), src_vcf, "-o", out_vcf])
    _run(["bcftools", "index", "-t", out_vcf])
    return out_vcf


def merge(vcfs: list[str], out_vcf: str, *, missing_to_ref: bool = True) -> str:
    """Merge per-sample VCFs. missing_to_ref (-0) fills absent genotypes as ref.

    NOTE (recovered caveat): per-sample calling + merge with -0 means uncalled
    sites become homozygous-reference, which biases absolute genetic distances.
    Documented here so downstream popgen can flag it. A joint re-genotype gives
    unbiased distances.
    """
    listfile = Path(out_vcf).with_suffix(".merge_list.txt")
    listfile.write_text("\n".join(vcfs) + "\n")
    cmd = ["bcftools", "merge", "-l", str(listfile), "-Oz", "-o", out_vcf]
    if missing_to_ref:
        cmd.insert(3, "-0")
    _run(cmd)
    _run(["bcftools", "index", "-t", out_vcf])
    return out_vcf


def biallelic_snps(vcf: str, out_vcf: str, regions: list[str] | None = None) -> str:
    """Extract biallelic SNPs (-m2 -M2 -v snps), optionally restricted to regions."""
    cmd = ["bcftools", "view", "-m2", "-M2", "-v", "snps"]
    if regions:
        cmd += ["-r", ",".join(regions)]
    cmd += [vcf, "-Oz", "-o", out_vcf]
    _run(cmd)
    _run(["bcftools", "index", "-t", out_vcf])
    return out_vcf


def n_records(vcf: str) -> int:
    return int(_run(["bcftools", "index", "-n", vcf]).stdout.strip())


def samples(vcf: str) -> list[str]:
    return _run(["bcftools", "query", "-l", vcf]).stdout.split()
