"""Tests for biocore.compare — genotype concordance, discordance typing, KING,
and (if scikit-allel is present) IBS distance + Mantel."""
import math
import pytest
from biocore.compare.genotype_calls import (
    concordance_pair, concordance_matrix, discordance_breakdown,
    king_relatedness, norm_geno, geno_class, is_missing,
)


def test_hemizygous_normalization():
    assert norm_geno("A") == "A/A"
    assert norm_geno("A/G") == "A/G"
    assert is_missing("./.") and is_missing(".") and is_missing("A/.")
    assert geno_class("A/A") == ("hom", frozenset(["A"]))
    assert geno_class("A/G") == ("het", frozenset(["A", "G"]))
    assert geno_class("./.") is None


def test_concordance_pair_and_hemi():
    a = {"rs1": "A/G", "rs2": "C/C", "rs3": "T/T", "rs4": "./."}
    b = {"rs1": "A/G", "rs2": "C/C", "rs3": "T/A", "rs4": "G/G"}
    # rs4 dropped (missing in a); rs1,rs2 match, rs3 differs -> 2/3
    m, s = concordance_pair(a, b)
    assert (m, s) == (2, 3)
    # hemizygous: "C" vs "C/C" matches under normalization, not without
    a2 = {"rsX": "C"}
    b2 = {"rsX": "C/C"}
    assert concordance_pair(a2, b2, normalize_hemizygous=True) == (1, 1)
    assert concordance_pair(a2, b2, normalize_hemizygous=False) == (0, 1)


def test_concordance_matrix_self_is_one():
    calls = {"s1": {"rs1": "A/G", "rs2": "C/C"},
             "s2": {"rs1": "A/G", "rs2": "C/T"}}
    labels, mat, overlap = concordance_matrix(calls)
    i = labels.index("s1")
    j = labels.index("s2")
    assert mat[i][i] == 1.0 and mat[j][j] == 1.0
    assert mat[i][j] == pytest.approx(0.5)   # 1 of 2 shared match
    assert overlap[i][j] == 2


def test_discordance_categories():
    a = {"r1": "A/A", "r2": "A/G", "r3": "A/A", "r4": "A/G"}
    b = {"r1": "G/G",   # opposite homozygote
         "r2": "A/A",   # het vs hom, shared allele A
         "r3": "T/T",   # opposite homozygote (disjoint)
         "r4": "A/G"}   # concordant
    cats, examples, nshared = discordance_breakdown(a, b)
    assert nshared == 4
    assert cats.get("opposite_homozygote") == 2
    assert cats.get("het_vs_hom_shared_allele") == 1
    assert "other" not in cats


def test_king_self_vs_unrelated():
    # identical genotypes -> kinship ~0.5 (self); many het sites, zero IBS0
    calls = {f"rs{i}": ("A/G" if i % 2 == 0 else "A/A") for i in range(100)}
    self_k = king_relatedness(calls, dict(calls))
    assert self_k["N_ibs0"] == 0
    assert self_k["kinship"] == pytest.approx(0.5, abs=0.05)
    # opposite homozygotes everywhere -> all IBS0; kinship undefined (no hets,
    # denom N_het_i+N_het_j = 0) so it is NaN by construction — the correct
    # KING behavior, not an error.
    hom_ref = {f"rs{i}": "A/A" for i in range(100)}
    hom_alt = {f"rs{i}": "G/G" for i in range(100)}
    unrel = king_relatedness(hom_ref, hom_alt)
    assert unrel["N_ibs0"] == 100
    assert math.isnan(unrel["kinship"])
    # a mix with some shared hets AND opposite homs -> finite, strongly negative
    mixed_a = {f"rs{i}": ("A/G" if i < 20 else "A/A") for i in range(100)}
    mixed_b = {f"rs{i}": ("A/G" if i < 20 else "G/G") for i in range(100)}
    mk = king_relatedness(mixed_a, mixed_b)
    assert mk["N_ibs0"] == 80 and mk["N_hethet"] == 20
    assert mk["kinship"] < 0   # 2*IBS0 dominates shared hets


def test_king_restrict_markers():
    calls = {"rsA": "A/G", "rsB": "A/G", "rsX_1": "A/G"}
    other = {"rsA": "A/G", "rsB": "A/A", "rsX_1": "G/G"}
    auto = {"rsA", "rsB"}
    r = king_relatedness(calls, other, restrict_markers=auto)
    assert r["n_sites"] == 2   # rsX_1 excluded


# distance module — only if scikit-allel present
def test_mantel_perfect_correlation():
    allel = pytest.importorskip("allel")
    import numpy as np
    from biocore.compare.distance import mantel
    D = np.array([[0, 1, 2], [1, 0, 3], [2, 3, 0]], dtype=float)
    r, p = mantel(D, D.copy(), n_perm=999, seed=1)
    assert r == pytest.approx(1.0)
    assert p <= 0.5
