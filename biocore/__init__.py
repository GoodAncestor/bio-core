# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""bio-core: organism-agnostic bioinformatics mechanism.

Shared foundation for MethylAsk (human methylation), GeneAsk (human variants),
and the seagrass plant-epigenomics work. Contains only MECHANISM — file I/O,
the context-aware methylation model, coordinate handling, provider/cache
interfaces, evidence tiering, and report rendering. No organism-specific
knowledge (no clinical databases, no clocks, no plant pathways) lives here.
"""
__version__ = "0.0.1"
