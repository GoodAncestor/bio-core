# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Provider interface — the single abstraction the whole data layer rests on.

Every reference database (EWAS Catalog, GDC, ClinVar, ...) is wrapped in a
Provider. The annotation engine only ever calls ``get()``; it never knows or
cares whether the answer came from a local file or a remote API. That is what
lets the same code run interactively online and as an offline batch script.

Three backend styles (see docs/DESIGN.md §3.2), all sharing this interface:
  - Bundled       : static file shipped in the repo (manifests, clock coeffs)
  - SyncedCache   : local mirror rebuilt by ``refresh()`` (dumps, VCFs, GDC)
  - LiveThrough   : remote API queried on cache miss, result cached with a TTL

Error tolerance (docs/DESIGN.md §4.3.3): a provider that cannot reach its source
does not raise into the report. It records its state in ``status()`` and the
annotation engine proceeds with whatever providers are healthy.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Tier(str, Enum):
    """Evidence strength attached to every finding (docs/DESIGN.md §4.3.1)."""
    ROBUST = "robust"            # replicated / large curated cohort
    MODERATE = "moderate"        # real signal, limited replication
    SPECULATIVE = "speculative"  # preliminary or weak
    UNKNOWN = "unknown"          # marker present, nothing recorded


class Category(str, Enum):
    """What kind of interest a finding serves (docs/DESIGN.md §4.3.2)."""
    CLINICAL = "clinical"        # disease / cancer / pharmacogenomic
    AGING = "aging"              # epigenetic clocks, exposure signatures
    TRAIT = "trait"              # 23andMe-style popular-interest layer


class Health(str, Enum):
    OK = "ok"
    STALE = "stale"                  # cache older than its refresh interval
    UNAVAILABLE = "unavailable"      # source unreachable / errored


@dataclass
class ChainLink:
    """One traceable link in the path from a finding to its evidence."""
    kind: str
    label: str
    id: str | None = None
    url: str | None = None


@dataclass
class Interpretation:
    """What a finding means, in four parts, each traceable to data or copy."""
    found: str
    can_mean: str
    how_sure: str
    next_step: str = ""
    condition: str | None = None
    condition_ids: list[str] = field(default_factory=list)
    zygosity: str | None = None
    citations: list[ChainLink] = field(default_factory=list)
    copy_version: str = ""
    reviewed_by: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """One thing known about one marker, from one source."""
    marker: str                      # canonical probe id or chrom:pos
    source: str                      # provider name
    description: str                 # plain-language statement for the reader
    tier: Tier
    categories: list[Category]
    detail: dict[str, Any] = field(default_factory=dict)  # beta, p, n, tissue...
    link: str | None = None          # deep link back to the source record
    pmids: list[str] = field(default_factory=list)
    interpretation: Interpretation | None = None
    evidence_chain: list[ChainLink] = field(default_factory=list)
    promoted: bool = False
    promoted_reason: str = ""
    deeper_dive: str | None = None
    deeper_dive_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a full JSON-safe view with enum values and nested dictionaries."""
        from dataclasses import asdict

        result = asdict(self)
        result["tier"] = self.tier.value
        result["categories"] = [category.value for category in self.categories]
        return result


@dataclass
class ProviderStatus:
    name: str
    health: Health
    version: str | None = None       # source release / dump date
    fetched_at: str | None = None    # ISO timestamp of local copy
    record_count: int | None = None
    note: str | None = None          # e.g. "upstream 502 — retry scheduled"


class Provider:
    """Base class. Subclasses implement the three methods below."""

    name: str = "provider"

    def get(self, marker: str) -> list[Finding]:
        """Return findings for one marker. Empty list = nothing known (not an error)."""
        raise NotImplementedError

    def get_many(self, markers: Iterable[str]) -> dict[str, list[Finding]]:
        """Default batches over get(); override for a real bulk query."""
        return {m: self.get(m) for m in markers}

    def refresh(self) -> ProviderStatus:
        """Update the local copy from source. No-op for live-only providers."""
        raise NotImplementedError

    def status(self) -> ProviderStatus:
        """Report health, version, cache age. Never raises — errors become UNAVAILABLE."""
        raise NotImplementedError
