"""Sample-comparison primitives — organism-agnostic genotype comparison.

Two input representations, both genotype-in / matrix-out:

- `genotype_calls` — compares string genotypes ({sample: {marker: "A/G"}}),
  the representation you get from consumer arrays (23andMe) and simple callers.
  Pairwise concordance, discordance typing, and KING-robust relatedness.
- `distance` — compares dosage from a multi-sample VCF via scikit-allel.
  Pairwise IBS distance matrix and the Mantel matrix-correlation test.

Recovered/refactored from two independent projects that did the same operation
for different questions: the human "six genomes" concordance work (do my tests
agree?) and the Zostera popgen work (how distinct are these plants?). Nothing
here is human- or plant-specific.
"""
