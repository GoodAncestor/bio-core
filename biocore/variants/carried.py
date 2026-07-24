"""Carried-variant extraction from a single-sample VCF (organism-agnostic).

Mechanism, not knowledge: yields the sites where a sample carries a
non-reference allele, keyed as 'chrom-pos-ref-alt' (GRCh38) — the neutral
representation a downstream interpreter (GeneAsk's ClinVar screen, a trait
table, a cancer-gene panel) matches against. Uses pysam; no bcftools shell-out.

Chromosome names are normalised to the panel convention (no 'chr' prefix), and
each per-ALT allele is emitted separately so a multiallelic site maps to the
right key.
"""
from __future__ import annotations


def _norm_chrom(c: str) -> str:
    c = str(c)
    return c[3:] if c.lower().startswith("chr") else c


def n_samples(vcf_path: str) -> int:
    """Number of sample columns in the VCF (0 if sites-only)."""
    import pysam
    with pysam.VariantFile(vcf_path) as vf:
        return len(list(vf.header.samples))


def carried_variants(vcf_path: str, *, sample: str | None = None,
                     platform: str = "WGS") -> list[dict]:
    """Return [{variant_id, genotype, platform}, ...] for sites where the sample
    carries at least one ALT allele. variant_id = 'chrom-pos-ref-alt' (GRCh38).
    """
    import pysam
    out: list[dict] = []
    with pysam.VariantFile(vcf_path) as vf:
        samples = list(vf.header.samples)
        if not samples:
            return out
        sn = sample or samples[0]
        for rec in vf:
            if not rec.alts:
                continue
            call = rec.samples.get(sn)
            if call is None:
                continue
            alleles = call.get("GT")
            if not alleles:
                continue
            carried_idx = {a for a in alleles if a not in (None, 0)}
            if not carried_idx:
                continue  # homozygous ref / no-call
            chrom = _norm_chrom(rec.chrom)
            # genotype as plus-strand bases (e.g. 'A/G'); '.' for missing
            def base(i):
                if i is None:
                    return "."
                if i == 0:
                    return rec.ref
                try:
                    return rec.alts[i - 1]
                except IndexError:
                    return "."
            geno = "/".join(sorted(base(a) for a in alleles))
            for i in carried_idx:
                alt = rec.alts[i - 1] if i - 1 < len(rec.alts) else None
                if not alt:
                    continue
                vid = f"{chrom}-{rec.pos}-{rec.ref}-{alt}"
                out.append({"variant_id": vid, "genotype": geno, "platform": platform})
    return out
