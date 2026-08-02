# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Provider registry + annotation engine.

Holds the set of providers, runs health checks, and assembles findings for a
sample. Error-tolerant: a provider whose status() is UNAVAILABLE is skipped for
lookups but still reported, so the report can show a "source unavailable at
generation time" note instead of silently omitting it (docs/DESIGN.md §4.3.3).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .base import Provider, Finding, Health, ProviderStatus


@dataclass
class SampleReport:
    findings: dict[str, list[Finding]] = field(default_factory=dict)  # marker -> findings
    provider_status: list[ProviderStatus] = field(default_factory=list)

    def all_findings(self) -> list[Finding]:
        return [f for fs in self.findings.values() for f in fs]


class Registry:
    def __init__(self, providers: list[Provider] | None = None):
        self._providers: list[Provider] = list(providers or [])

    def register(self, provider: Provider) -> None:
        self._providers.append(provider)

    def status(self) -> list[ProviderStatus]:
        return [p.status() for p in self._providers]

    def annotate(self, markers: list[str]) -> SampleReport:
        rep = SampleReport()
        statuses = self.status()
        rep.provider_status = statuses
        healthy = {s.name for s in statuses if s.health is not Health.UNAVAILABLE}
        for p in self._providers:
            if p.name not in healthy:
                continue
            got = p.get_many(markers)
            for marker, fs in got.items():
                if fs:
                    rep.findings.setdefault(marker, []).extend(fs)
        return rep
