"""bedMethyl reader — modkit 18-column contract.

Column contract recovered verbatim from the eelgrass methylation pipeline
(frame 59ba13be, agg.awk). modkit bedMethyl columns (1-based):
   1  chrom
   2  start (0-based)
   3  end
   4  mod info, comma-joined: "<mod_code>,<context>,<extra>"  e.g. "m,CG,0"
   6  strand
  10  valid coverage
  11  percent modified (0..100)
  12  N modified reads
  13  N canonical (unmodified) reads

Streaming by design: methylomes are ~79M lines. read_sites() yields MethylSite
objects lazily so a whole-genome file never loads into memory at once.
"""
from __future__ import annotations
import gzip
from typing import Iterator, Iterable
from ..methylation.model import MethylSite, Context

# 0-based column indices into the 18-col bedMethyl row
_CHROM, _START, _END, _MODINFO = 0, 1, 2, 3
_STRAND, _COV, _PCT, _NMOD, _NCAN = 5, 9, 10, 11, 12


def _open(path: str):
    return gzip.open(path, "rt") if str(path).endswith(".gz") else open(path)


def read_sites(path: str, *, contexts: Iterable[str] | None = None,
               chroms: Iterable[str] | None = None,
               min_coverage: int = 0) -> Iterator[MethylSite]:
    """Yield MethylSite rows from a modkit bedMethyl file.

    contexts / chroms: optional allow-lists (e.g. {"CG"} or {"Chr01",...}).
    min_coverage: skip rows below this valid coverage (0 = keep all).
    """
    ctx_filter = {Context.parse(c) for c in contexts} if contexts else None
    chrom_filter = set(chroms) if chroms else None
    with _open(path) as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) <= _NCAN:
                continue
            chrom = f[_CHROM]
            if chrom_filter and chrom not in chrom_filter:
                continue
            mod = f[_MODINFO].split(",")
            ctx = Context.parse(mod[1]) if len(mod) > 1 else Context.UNKNOWN
            if ctx_filter and ctx not in ctx_filter:
                continue
            try:
                nmod = int(f[_NMOD]); ncan = int(f[_NCAN])
            except ValueError:
                continue
            if (nmod + ncan) < min_coverage:
                continue
            yield MethylSite(chrom=chrom, pos=int(f[_START]), context=ctx,
                             n_mod=nmod, n_canonical=ncan, strand=f[_STRAND] if len(f) > _STRAND else ".")


def summarize_by_context(path: str, min_coverage: int = 5) -> dict:
    """One streaming pass -> per-context weighted methylation + site counts.

    Mirrors the recovered agg.awk CTX output: for each context, n_sites,
    n_sites at cov>=min, and weighted methylation = Σn_mod/Σ(n_mod+n_canonical)
    over covered sites.
    """
    agg: dict[Context, list[int]] = {}  # ctx -> [n, n_cov, sm, scan]
    for s in read_sites(path):
        a = agg.setdefault(s.context, [0, 0, 0, 0])
        a[0] += 1
        if s.coverage >= min_coverage:
            a[1] += 1; a[2] += s.n_mod; a[3] += s.n_canonical
    out = {}
    for ctx, (n, ncov, sm, scan) in agg.items():
        denom = sm + scan
        out[ctx.value] = {
            "n_sites": n, "n_sites_covered": ncov,
            "weighted_methylation": (sm / denom if denom else 0.0),
        }
    return out
