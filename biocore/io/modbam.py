# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""modBAM reader — extract methylation calls from ONT MM/ML tags.

An ONT modBAM carries base-modification calls inline with aligned reads: the MM
tag lists which bases are modified and the ML tag the per-call probabilities
(SAM tag spec / modkit convention). This reader piles those calls up per
reference position and yields MethylSite rows, so an ONT run feeds the same
context-aware methylation model as a modkit bedMethyl file — this is the
methylation half of the modBAM split DNA-Report routes.

Mechanism (via pysam's decoded MM/ML, `AlignedSegment.modified_bases`):
  key   = (canonical_base, strand, mod_code)   e.g. ('C', 0, 'm') for 5mC
  value = [(read_pos, qual), ...]  qual = 256*probability (-1 if unknown)
We map each read_pos to its reference coordinate, threshold the probability, and
accumulate modified vs canonical counts per (chrom, ref_pos). Sequence context
(CG/CHG/CHH) needs the reference base neighbourhood, so it is resolved by an
optional reference FASTA; without one, context is UNKNOWN and callers that need
CHG/CHH (plants) must supply the FASTA.

The variant half of the split is produced separately by a variant caller on the
same BAM; this module is only the methylation stream.
"""
from __future__ import annotations
from typing import Iterator, Iterable
from collections import defaultdict
from ..methylation.model import MethylSite, Context

# 5mC / 5hmC modification codes as they appear in MM tags
MOD_5MC = "m"
MOD_5HMC = "h"


def _context_from_ref(fasta, chrom: str, pos: int, strand_fwd: bool) -> Context:
    """Resolve CpG/CHG/CHH context from the reference at a cytosine position.
    pos is 0-based reference coordinate of the C (on the given strand)."""
    if fasta is None:
        return Context.UNKNOWN
    try:
        # fetch the C and its two downstream bases on the relevant strand
        if strand_fwd:
            tri = fasta.fetch(chrom, pos, pos + 3).upper()
            c0 = tri[0] if tri else ""
            nxt = tri[1:3]
        else:
            # reverse strand: context reads downstream in genomic-decreasing dir
            tri = fasta.fetch(chrom, max(0, pos - 2), pos + 1).upper()
            comp = {"A": "T", "T": "A", "G": "C", "C": "G", "N": "N"}
            rc = "".join(comp.get(b, "N") for b in reversed(tri))
            c0 = rc[0] if rc else ""
            nxt = rc[1:3]
    except (KeyError, ValueError):
        return Context.UNKNOWN
    if c0 != "C" or len(nxt) < 2:
        return Context.UNKNOWN
    if nxt[0] == "G":
        return Context.CG
    if nxt[1] == "G":
        return Context.CHG
    return Context.CHH


def pileup_methyl(bam_path: str, *,
                  mod_code: str = MOD_5MC,
                  min_prob: float = 0.5,
                  min_coverage: int = 0,
                  reference_fasta: str | None = None,
                  contexts: Iterable[str] | None = None) -> Iterator[MethylSite]:
    """Yield MethylSite rows piled up from MM/ML tags in a modBAM.

    A base call counts as modified when its probability >= min_prob, else
    canonical. Sites are emitted once all reads are tallied (streaming per
    reference position is not possible without coordinate-sorted guarantees, so
    this accumulates in a dict — fine for a per-sample run).
    """
    import pysam
    ctx_filter = {Context.parse(c) for c in contexts} if contexts else None
    fasta = pysam.FastaFile(reference_fasta) if reference_fasta else None
    thr = int(round(min_prob * 256))

    # (chrom, pos, strand_fwd) -> [n_mod, n_canonical]
    tally: dict = defaultdict(lambda: [0, 0])

    bam = pysam.AlignmentFile(bam_path, "rb")
    for read in bam.fetch(until_eof=True):
        if read.is_unmapped or read.query_sequence is None:
            continue
        mods = read.modified_bases
        if not mods:
            continue
        # read_pos -> ref_pos map for this read
        ap = dict(read.get_aligned_pairs(matches_only=True))  # {query_pos: ref_pos}
        for key, calls in mods.items():
            canon, strand, code = key
            if code != mod_code:
                continue
            strand_fwd = (strand == 0)
            for read_pos, qual in calls:
                ref_pos = ap.get(read_pos)
                if ref_pos is None:
                    continue
                cell = tally[(read.reference_name, ref_pos, strand_fwd)]
                if qual < 0:
                    continue  # unknown probability — not counted either way
                if qual >= thr:
                    cell[0] += 1
                else:
                    cell[1] += 1

    for (chrom, pos, strand_fwd), (nmod, ncan) in tally.items():
        cov = nmod + ncan
        if cov < min_coverage:
            continue
        ctx = _context_from_ref(fasta, chrom, pos, strand_fwd)
        if ctx_filter is not None and ctx not in ctx_filter:
            continue
        # coverage and fraction are computed properties on MethylSite
        yield MethylSite(chrom=chrom, pos=pos, context=ctx,
                         n_mod=nmod, n_canonical=ncan,
                         strand=("+" if strand_fwd else "-"))
    bam.close()
    if fasta is not None:
        fasta.close()
