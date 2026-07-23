# Provenance — where bio-core's code came from

Every module traces to a specific source. The methylation and VCF mechanism was
recovered from the eelgrass analysis (Claude Science frame `59ba13be`,
project `proj_66ffa9d0a3de`) via the execution log, then refactored into library
form. The provider/report layer was lifted from MethylAsk.

| bio-core module | Source | Recovered/lifted from |
|---|---|---|
| `methylation/model.py` | eelgrass agg.awk | weighted-methylation formula Σn_mod/Σ(n_mod+n_canonical), cov>=5; context CG/CHG/CHH (frame 59ba13be, cells ~12-38) |
| `io/bedmethyl.py` | eelgrass agg.awk | modkit 18-col contract: context=col4 split ',' field2, cov=col10, nmod=col12, ncan=col13 (frame 59ba13be) |
| `io/vcf_ops.py` | eelgrass VCF prep | bcftools reheader/merge -0/biallelic-filter pipeline (frame 59ba13be, cells 38-43) |
| `io/fetch.py` | MethylAsk | resumable downloader |
| `providers/base.py` | MethylAsk | provider interface, Finding/Tier/Category |
| `providers/registry.py` | MethylAsk | provider registry + annotation engine |
| `report/render.py` | MethylAsk | report model → HTML/PDF |

## Validation

The bedMethyl reader was validated against the recovered awk ground truth on
real eelgrass sample 1 (first 2M lines, cov>=5): CG 48.9% / CHG 16.5% / CHH 1.5%
weighted methylation — matching the awk output within boundary-effect tolerance.
See `tests/test_bedmethyl.py`.
