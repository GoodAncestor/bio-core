# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""bedMethyl reader — modkit 18-column contract.

Column contract recovered verbatim from the eelgrass methylation pipeline
(eelgrass analysis session, agg.awk). modkit bedMethyl columns (1-based):
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
               min_coverage: int = 0, mod_codes: Iterable[str] | None = None,
               strict: bool = False, stats: dict | None = None) -> Iterator[MethylSite]:
    """Yield MethylSite rows from a modkit bedMethyl file.

    contexts / chroms: optional allow-lists (e.g. {"CG"} or {"Chr01",...}).
    min_coverage: skip rows below this valid coverage (0 = keep all).
    """
    ctx_filter = {Context.parse(c) for c in contexts} if contexts else None
    chrom_filter = set(chroms) if chroms else None
    mod_filter = set(mod_codes) if mod_codes is not None else None
    if min_coverage < 0:
        raise ValueError("min_coverage must be nonnegative")
    counts = stats if stats is not None else {}
    counts.update(rows=0, malformed_rows=0)
    with _open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip() or line.startswith(("#", "track ", "browser ")):
                continue
            counts["rows"] += 1
            # Older modkit versions use spaces in the extension columns.
            f = line.split()
            try:
                if len(f) < 18:
                    raise ValueError("expected 18 bedMethyl columns")
                pos, end = int(f[_START]), int(f[_END])
                valid_coverage = int(f[_COV])
                nmod, ncan, nother = int(f[_NMOD]), int(f[_NCAN]), int(f[13])
                if (pos < 0 or end <= pos or f[_STRAND] not in {"+", "-", "."}
                        or min(valid_coverage, nmod, ncan, nother) < 0
                        or valid_coverage != nmod + ncan + nother):
                    raise ValueError("invalid coordinates, strand, or count totals")
            except (ValueError, IndexError) as exc:
                counts["malformed_rows"] += 1
                if strict:
                    raise ValueError(f"Invalid bedMethyl row {lineno}: {exc}") from exc
                continue
            chrom = f[_CHROM]
            if chrom_filter and chrom not in chrom_filter:
                continue
            mod = f[_MODINFO].split(",")
            if mod_filter is not None and mod[0] not in mod_filter:
                continue
            ctx = Context.parse(mod[1]) if len(mod) > 1 else Context.UNKNOWN
            if ctx_filter and ctx not in ctx_filter:
                continue
            if valid_coverage < min_coverage:
                continue
            yield MethylSite(chrom=chrom, pos=pos, context=ctx,
                             n_mod=nmod, n_canonical=ncan,
                             strand=f[_STRAND] if len(f) > _STRAND else ".",
                             valid_coverage=valid_coverage, mod_code=mod[0],
                             n_other_mod=nother)


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
