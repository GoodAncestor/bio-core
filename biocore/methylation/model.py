"""Context-aware methylation data model — the type the whole system is built on.

The load-bearing decision (see MethylAsk docs/GENOMIC_SCOPE.md): a methylation
call is *a cytosine, its sequence context, and its level* — NOT "a CpG and its
beta". Humans are ~entirely CpG, but plants use CpG + CHG + CHH and the CHH
(RdDM) signal is central, not incidental; non-CpG methylation is real in humans
too (brain, stem cells). Representing context explicitly is simply more correct,
and it is what lets one core serve MethylAsk, GeneAsk, and the seagrass work.

Context codes follow the modkit / bedMethyl convention:
  CG  (== CpG)   CHG            CHH            (H = A/C/T)
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class Context(str, Enum):
    CG = "CG"      # CpG — the human-relevant context
    CHG = "CHG"    # plant symmetric non-CpG
    CHH = "CHH"    # plant asymmetric — RdDM-deposited
    UNKNOWN = "?"

    @classmethod
    def parse(cls, token: str) -> "Context":
        t = (token or "").strip().upper()
        if t in ("CG", "CPG"):
            return cls.CG
        if t == "CHG":
            return cls.CHG
        if t == "CHH":
            return cls.CHH
        return cls.UNKNOWN


@dataclass(frozen=True)
class MethylSite:
    """One cytosine's methylation call at a genomic position."""
    chrom: str
    pos: int                 # 0-based start (bedMethyl convention)
    context: Context
    n_mod: int               # reads supporting methylation
    n_canonical: int         # reads supporting unmethylated
    strand: str = "."

    @property
    def coverage(self) -> int:
        return self.n_mod + self.n_canonical

    @property
    def fraction(self) -> float:
        """Per-site methylation fraction (0..1); 0 when uncovered."""
        c = self.coverage
        return self.n_mod / c if c else 0.0


def weighted_methylation(sites, min_coverage: int = 5) -> float:
    """Weighted methylation over sites: sum(n_mod) / sum(n_mod + n_canonical),
    counting only sites with coverage >= min_coverage.

    This is the exact estimator used across the eelgrass methylation work
    (from the eelgrass analysis session): wMeth = Σn_mod / Σ(n_mod + n_canonical),
    cov>=5. It is coverage-weighted (high-coverage sites count more), which is
    the standard whole-genome methylation summary, not a mean of per-site
    fractions.
    """
    sm = scan = 0
    for s in sites:
        if s.coverage >= min_coverage:
            sm += s.n_mod
            scan += s.n_canonical
    denom = sm + scan
    return sm / denom if denom else 0.0
