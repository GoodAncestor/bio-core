"""Tests for the modBAM MM/ML methylation reader (pysam-backed).

Builds a tiny modBAM with known 5mC calls and confirms the pileup recovers the
expected per-position modified/canonical counts, probability thresholding, and
CpG context resolution from a reference FASTA.
"""
import os
import pytest

pysam = pytest.importorskip("pysam")
from biocore.io.modbam import pileup_methyl


@pytest.fixture(scope="module")
def modbam(tmp_path_factory):
    d = tmp_path_factory.mktemp("modbam")
    ref = "ACGTTCGAACGTTAGCCGGATCGATCGTACGTAGCTAGCT"
    fa = str(d / "ref.fa")
    with open(fa, "w") as fh:
        fh.write(">chr1\n" + ref + "\n")
    pysam.faidx(fa)

    header = {"HD": {"VN": "1.6", "SO": "coordinate"},
              "SQ": [{"LN": len(ref), "SN": "chr1"}]}
    c_positions = [i for i, b in enumerate(ref) if b == "C"]
    chosen = c_positions[:3]
    c_order = {p: idx for idx, p in enumerate(c_positions)}
    deltas, prev = [], -1
    for cp in chosen:
        idx = c_order[cp]
        deltas.append(idx - prev - 1)
        prev = idx
    mm = "C+m," + ",".join(str(x) for x in deltas) + ";"
    ml = [240, 240, 60]  # strong, strong, weak

    bam = str(d / "test.bam")
    with pysam.AlignmentFile(bam, "wb", header=header) as out:
        for r in range(5):
            a = pysam.AlignedSegment()
            a.query_name = f"read{r}"
            a.query_sequence = ref
            a.flag = 0
            a.reference_id = 0
            a.reference_start = 0
            a.mapping_quality = 60
            a.cigar = [(0, len(ref))]
            a.query_qualities = pysam.qualitystring_to_array("I" * len(ref))
            a.set_tag("MM", mm, "Z")
            a.set_tag("ML", ml)
            out.write(a)
    sbam = str(d / "test.sorted.bam")
    pysam.sort("-o", sbam, bam)
    pysam.index(sbam)
    return sbam, fa, chosen


def test_pileup_counts_and_threshold(modbam):
    sbam, fa, chosen = modbam
    sites = {s.pos: s for s in pileup_methyl(sbam, min_prob=0.5, reference_fasta=fa)}
    # first two chosen C's are strong -> fully modified; third weak -> canonical
    assert sites[chosen[0]].n_mod == 5 and sites[chosen[0]].n_canonical == 0
    assert sites[chosen[1]].n_mod == 5 and sites[chosen[1]].n_canonical == 0
    assert sites[chosen[2]].n_mod == 0 and sites[chosen[2]].n_canonical == 5


def test_context_resolved_from_fasta(modbam):
    sbam, fa, chosen = modbam
    sites = {s.pos: s for s in pileup_methyl(sbam, min_prob=0.5, reference_fasta=fa)}
    # ref positions 1 and 5 are both C followed by G -> CpG
    assert sites[chosen[0]].context.value == "CG"


def test_context_unknown_without_fasta(modbam):
    sbam, fa, chosen = modbam
    sites = list(pileup_methyl(sbam, min_prob=0.5))  # no reference
    assert sites and all(s.context.value == "?" or s.context.name == "UNKNOWN" for s in sites)


def test_min_coverage_filter(modbam):
    sbam, fa, chosen = modbam
    assert list(pileup_methyl(sbam, min_coverage=99, reference_fasta=fa)) == []
