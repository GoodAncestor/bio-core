# bio-core

Organism-agnostic bioinformatics **mechanism** — the shared foundation for
[MethylAsk](https://github.com/GoodAncestor/MethylAsk) (human methylation),
[GeneAsk](https://github.com/GoodAncestor/GeneAsk) (human variants), and the
[seagrass](https://github.com/GoodAncestor/seagrass) plant-epigenomics work.

## What lives here (and what doesn't)

bio-core is **pure mechanism**: file I/O, the context-aware methylation model,
coordinate handling, provider/cache interfaces, evidence tiering, and report
rendering. It contains **no organism-specific knowledge** — no clinical
databases, no epigenetic clocks, no plant pathways. Those live in the
downstream repos that depend on bio-core.

The split is knowledge vs mechanism: a modBAM from a human and from eelgrass are
the same file read by the same code (mechanism, here); what a marker *means* is
organism-specific (knowledge, downstream).

## Modules

- `biocore.methylation.model` — context-aware model (cytosine + CG/CHG/CHH context + level) and the weighted-methylation estimator
- `biocore.io.bedmethyl` — modkit 18-column bedMethyl reader (streaming)
- `biocore.io.vcf_ops` — bcftools wrappers (reheader / merge / biallelic filter)
- `biocore.io.fetch` — resumable, retrying downloader for slow academic mirrors
- `biocore.providers` — provider interface, cache, error-tolerant status, evidence tiering
- `biocore.report` — one report model → HTML/PDF

## Provenance

The methylation reader and VCF ops were recovered and refactored from the
eelgrass analysis pipeline; the provider/tiering/report layer was lifted from
MethylAsk. See `docs/PROVENANCE.md`.
