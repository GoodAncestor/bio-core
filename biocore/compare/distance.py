# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Dosage-based sample distance from a multi-sample VCF, plus the Mantel test.

Where genotype_calls compares string genotypes marker-by-marker, this compares
0/1/2 alt-allele dosage across a cohort VCF via scikit-allel — the right tool
when you already have a merged biallelic-SNP VCF. Recovered/refactored from the
Zostera popgen analysis (session cells 44-47); the same IBS logic served the
human six-genomes distance.

Needs scikit-allel + scipy (the 'popgen' extra). Imported lazily.
"""
from __future__ import annotations


def ibs_distance_matrix(vcf_path: str):
    """Pairwise identity-by-state distance from a biallelic-SNP VCF.

    Distance = mean over sites of |g_i - g_j| / 2 on 0/1/2 alt-allele dosage,
    using only sites called (non-missing) in ALL samples to avoid missing-data
    artifacts. Returns (samples, distance_matrix).

    Caveat carried from the source analysis: if the VCF was built by per-sample
    calling + `bcftools merge -0` (absent -> ref), absolute distances are biased
    (missing reads as reference). Relative structure is robust; a joint
    re-genotype gives unbiased absolute distances.
    """
    import allel
    from scipy.spatial.distance import pdist, squareform
    callset = allel.read_vcf(vcf_path, fields=["samples", "calldata/GT"])
    samples = list(callset["samples"])
    gt = allel.GenotypeArray(callset["calldata/GT"])
    called_all = ~gt.is_missing().any(axis=1)
    gt_c = gt.compress(called_all, axis=0)
    ac = gt_c.count_alleles()
    seg = ac.is_segregating() & (ac.max_allele() == 1)
    gt_s = gt_c.compress(seg, axis=0)
    gn = gt_s.to_n_alt(fill=-1).astype(float).T  # samples x sites
    D = squareform(pdist(gn, metric="cityblock")) / (2 * gn.shape[1])
    return samples, D


def mantel(dist_a, dist_b, n_perm: int = 9999, seed: int = 0):
    """Mantel test between two square distance matrices (Pearson r + perm p).

    A general matrix-correlation test — used in the plant work to test coupling
    between a genetic distance matrix and a methylation distance matrix, but it
    is not tied to any organism or data type.
    """
    import numpy as np
    from scipy.spatial.distance import squareform
    from scipy.stats import pearsonr
    a = squareform(dist_a, checks=False)
    b = squareform(dist_b, checks=False)
    r0 = pearsonr(a, b)[0]
    rng = np.random.RandomState(seed)
    k = dist_a.shape[0]
    cnt = 0
    for _ in range(n_perm):
        p = rng.permutation(k)
        rp = pearsonr(squareform(dist_a[np.ix_(p, p)], checks=False), b)[0]
        if abs(rp) >= abs(r0):
            cnt += 1
    return r0, (cnt + 1) / (n_perm + 1)
