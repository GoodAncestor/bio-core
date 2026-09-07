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
from contextlib import ExitStack
import re
from ..methylation.model import MethylSite, Context

# 5mC / 5hmC modification codes as they appear in MM tags
MOD_5MC = "m"
MOD_5HMC = "h"


def pileup_methyl_targets(bam_path: str, *, targets: dict[str, set[int]],
                         reference_fasta: str, mod_code: str = MOD_5MC,
                         min_prob: float = 0.8, min_mapping_quality: int = 20,
                         stats: dict | None = None,
                         provenance: dict | None = None) -> Iterator[MethylSite]:
    """Bounded-memory research pileup at requested cytosine coordinates.

    Targets include both strand coordinates of each CpG, in the BAM's contig
    naming scheme. Only primary, nonduplicate, QC-passing alignments contribute.
    The largest of canonical and *all modeled cytosine modifications* must
    reach min_prob; ambiguous/unknown calls are omitted, not made canonical.
    Memory is O(target sites + longest read), never all genomic cytosines.
    This deliberately does not claim bit-for-bit modkit threshold equivalence.
    """
    import pysam
    if mod_code not in {MOD_5MC, MOD_5HMC}:
        raise ValueError("mod_code must be 'm' (5mC) or 'h' (5hmC)")
    if not 0.5 < min_prob < 1:
        raise ValueError("min_prob must be strictly between 0.5 and 1")
    if min_mapping_quality < 0:
        raise ValueError("min_mapping_quality must be nonnegative")
    counts = stats if stats is not None else {}
    counts.update(reads=0, reads_used=0, reads_filtered=0, reads_without_mod_tags=0,
                  unknown_calls=0, ambiguous_calls=0, valid_calls=0)
    tally = defaultdict(lambda: [0, 0, 0])  # modified, canonical, other mod
    with ExitStack() as stack:
        bam = stack.enter_context(pysam.AlignmentFile(bam_path, "rb"))
        fasta = stack.enter_context(pysam.FastaFile(reference_fasta))
        sample_ids = sorted({rg["SM"] for rg in bam.header.to_dict().get("RG", [])
                             if rg.get("SM")})
        if len(sample_ids) > 1:
            raise ValueError("BAM contains multiple RG SM sample identities; split by sample first")
        if provenance is not None:
            provenance.update(sample_ids=sample_ids, bam_programs=bam.header.to_dict().get("PG", []),
                              reference_fasta=reference_fasta,
                              probability_method="maximum state, ML bin midpoint, all C modifications",
                              min_prob=min_prob, min_mapping_quality=min_mapping_quality)
        for chrom, positions in targets.items():
            if positions and chrom in bam.references:
                if chrom not in fasta.references:
                    raise ValueError(f"BAM contig {chrom!r} absent from reference FASTA")
                if bam.get_reference_length(chrom) != fasta.get_reference_length(chrom):
                    raise ValueError(f"BAM/reference length mismatch for {chrom}")
        for read in bam.fetch(until_eof=True):
            counts["reads"] += 1
            if (read.is_unmapped or read.is_secondary or read.is_supplementary
                    or read.is_duplicate or read.is_qcfail or read.mapping_quality < min_mapping_quality
                    or read.query_sequence is None):
                counts["reads_filtered"] += 1
                continue
            positions = targets.get(read.reference_name)
            if not positions:
                continue
            ap = {q: r for q, r in read.get_aligned_pairs(matches_only=True) if r in positions}
            if not ap:
                continue
            mm = next((read.get_tag(t) for t in ("MM", "Mm") if read.has_tag(t)), None)
            if mm is None:
                counts["reads_without_mod_tags"] += 1
                continue
            if read.has_tag("MN") and read.get_tag("MN") != len(read.query_sequence):
                raise ValueError("BAM MN tag differs from sequence length; modification tags may be stale")
            groups = {}
            for group in mm.split(";"):
                if not group:
                    continue
                match = re.fullmatch(r"([ACGTUN])([+-])([a-z]+|[0-9]+)([.?]?)(?:,[0-9]+)*", group)
                if match is None:
                    raise ValueError("Malformed BAM MM group")
                canon, sign, codes, skip = match.groups()
                if canon != "C":
                    continue
                strand = int(sign == "-") ^ int(read.is_reverse)
                for code in ([int(codes)] if codes.isdigit() else codes):
                    key = (strand, code)
                    if key in groups:
                        raise ValueError("Duplicate cytosine modification group in MM tag")
                    groups[key] = skip != "?"  # omitted flag has '.' semantics
            if not any(code == mod_code for _, code in groups):
                counts["reads_without_mod_tags"] += 1
                continue
            decoded = read.modified_bases
            if decoded is None:
                raise ValueError("Unable to decode BAM MM/ML modification tags")
            calls = {(strand, code): dict(values) for (canon, strand, code), values in decoded.items()
                     if canon == "C"}
            counts["reads_used"] += 1
            for q, pos in ap.items():
                for strand in (0, 1):
                    if (strand, mod_code) not in groups:
                        continue
                    if read.query_sequence[q].upper() != ("C" if strand == 0 else "G"):
                        continue
                    probabilities = {}
                    unknown = False
                    for (s, code), implicit in groups.items():
                        if s != strand:
                            continue
                        qual = calls.get((s, code), {}).get(q)
                        if qual is None and implicit:
                            probabilities[code] = 0.0
                        elif qual is None or qual < 0:
                            unknown = True
                            break
                        else:
                            probabilities[code] = (qual + 0.5) / 256.0
                    if unknown:
                        counts["unknown_calls"] += 1
                        continue
                    total = sum(probabilities.values())
                    if total > 1 + len(probabilities) / 512:
                        raise ValueError("BAM modification probabilities exceed a mutually exclusive distribution")
                    probabilities[None] = max(0.0, 1.0 - total)
                    state = max(probabilities, key=probabilities.get)
                    if probabilities[state] < min_prob:
                        counts["ambiguous_calls"] += 1
                        continue
                    if _context_from_ref(fasta, read.reference_name, pos, strand == 0) != Context.CG:
                        continue
                    cell = tally[(read.reference_name, pos, strand)]
                    cell[0 if state == mod_code else 1 if state is None else 2] += 1
                    counts["valid_calls"] += 1
        for (chrom, pos, strand), (nmod, ncan, nother) in sorted(tally.items()):
            yield MethylSite(chrom, pos, Context.CG, nmod, ncan,
                             "+" if strand == 0 else "-", nmod + ncan + nother,
                             mod_code, nother)


def _implicit_canonical_keys(read) -> set[tuple[str, int, object]]:
    """Decode MM group headers whose ``.`` flag marks omissions canonical."""
    try:
        mm = read.get_tag("MM")
    except KeyError:
        try:
            mm = read.get_tag("Mm")
        except KeyError:
            return set()
    keys = set()
    for group in mm.split(";"):
        header = group.split(",", 1)[0]
        if len(header) < 4 or header[-1] != "." or header[1] not in "+-":
            continue
        canon, strand, codes = header[0].upper(), int(header[1] == "-"), header[2:-1]
        decoded_codes = [int(codes)] if codes.isdigit() else list(codes)
        keys.update((canon, strand, code) for code in decoded_codes)
    return keys


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
    if c0 != "C" or not nxt:
        return Context.UNKNOWN
    if nxt[0] == "G":
        return Context.CG
    if len(nxt) < 2:
        return Context.UNKNOWN
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
        mods = read.modified_bases or {}
        # read_pos -> ref_pos map for this read
        ap = dict(read.get_aligned_pairs(matches_only=True))  # {query_pos: ref_pos}
        explicit_positions = defaultdict(set)
        for key, calls in mods.items():
            canon, strand, code = key
            if code != mod_code:
                continue
            strand_fwd = (strand == 0)
            for read_pos, qual in calls:
                explicit_positions[key].add(read_pos)
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
        for key in _implicit_canonical_keys(read):
            canon, strand, code = key
            if code != mod_code:
                continue
            strand_fwd = (strand == 0)
            for read_pos, base in enumerate(read.query_sequence.upper()):
                if base != canon or read_pos in explicit_positions[key]:
                    continue
                ref_pos = ap.get(read_pos)
                if ref_pos is not None:
                    tally[(read.reference_name, ref_pos, strand_fwd)][1] += 1

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
