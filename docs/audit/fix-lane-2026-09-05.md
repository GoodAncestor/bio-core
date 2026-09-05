# Lane report: genomics correctness and local-file-safe PDF rendering

## Outcome

All seven P1 findings in `audit.md` were reproduced and fixed. Each change has a regression test that pins the corrected security boundary or published value. No network access was used.

The five priority fixes are F1, F3, F4, F6, and F7. F2 and F5 were also completed without restructuring unrelated code. The three P2 findings were not changed; their evidence limits are recorded below.

## Test-first evidence

The new focused tests were run against the audited implementation before source changes. Pytest printed `12 failed in 0.15s`; the failures included the injected attachment tag, missing PDF fetch restriction, the absent implicit-canonical calls, the dropped column-10-covered site, `hemi` for `1/.`, partial-call concordance, allele-order discordance, retained mode-0644 VCF manifests, and the malformed merge arguments.

After the fixes, the same focused selection printed `12 passed in 0.06s`. Additional coverage for both renderer link paths and merge failure cleanup brought the final focused security/VCF selection to `7 passed in 0.03s`.

## Findings fixed

### F1 — report metadata and PDF local-file attachment

- `biocore/report/render.py:33-51` now bounds metadata attributes to 512 characters, bounds URLs to 2,048 characters, escapes attribute values with quote escaping, and permits only HTTP(S) report links. Finding topics, aggregate card topics, sources, the fallback outcome kind, and the tool version use the bounded escape path (`render.py:468`, `render.py:561-567`, `render.py:798`, `render.py:934`, `render.py:1698`). Finding and evidence-chain links reject `javascript:` and `file:` (`render.py:448-454`, `render.py:570-605`).
- `biocore/report/render.py:1893-1905` gives WeasyPrint a data-only resource fetcher. Thus even raw caller-supplied HTML cannot make PDF generation read a local file or fetch a remote resource.
- `tests/test_report_security.py:30-82` pins markup escaping, active/local URL rejection, the 512-character bound, and PDF local-resource refusal.
- The audit attachment reproduction now prints `injected attachment tag: False`, `rendered PDF attachments: {}`, and, when the attachment tag is passed directly to `to_pdf`, `direct raw HTML PDF attachments: {}`. Before the fix, the audit extracted `private-genotypes.txt` with the synthetic genotype bytes.

### F3 — implicit canonical modBAM calls

- `biocore/io/modbam.py:34-51` recognizes MM groups whose `.` flag defines omitted canonical bases as confidently canonical, including groups with no explicit modified calls (for which pysam returns an empty `modified_bases`). `modbam.py:108-139` counts aligned, omitted canonical bases while excluding explicit positions.
- `tests/test_modbam.py:84-135` proves encoding invariance and retention of an entirely canonical second cytosine.
- Reasoning pinned by the test: one explicitly modified read plus four implicitly canonical reads has coverage 5 and methylation `1 / (1 + 4) = 0.2`. The corrected reproduction reports `(1, 4, 0.2)` for both implicit and explicit encodings; the site no longer disappears at `min_coverage=5`.

### F4 — bedMethyl coverage contract

- `biocore/methylation/model.py:38-58` represents bedMethyl valid coverage separately from the modification-specific denominator. `coverage` uses column 10 when supplied, while `fraction` remains `n_mod / (n_mod + n_canonical)`.
- `biocore/io/bedmethyl.py:58-68` parses and filters on column 10 as documented.
- `tests/test_bedmethyl.py:49-67` records why other modification calls affect valid coverage but do not enter the 5mC-versus-canonical estimator.
- Both synthetic rows now pass at coverage 5. Their summary is two covered sites and `4 / (4 + 5) = 0.4444444444444444`, rather than one site and 0%.

### F6 — partial diploid zygosity

- `biocore/variants/carried.py:65-76` distinguishes a true one-allele haploid call from a multi-allele genotype containing missing data. A carried ALT from `1/.` is preserved, but its zygosity is `unknown`, not `hemi`.
- `tests/test_carried_zygosity.py:53-65` pins the chromosome-1 `1/.` result to genotype `./G` and zygosity `unknown`.
- This follows the available evidence: one known ALT establishes carriage, but the missing second allele establishes neither ploidy nor copy count.

### F7 — concordance and discordance equivalence

- `biocore/compare/genotype_calls.py:21-32` treats a missing allele in either slash-separated position as incomplete and canonicalizes unphased allele order. `genotype_calls.py:48-60` applies that normalization to concordance. `genotype_calls.py:84-113` uses the same equivalence and hemizygous collapse in discordance classification.
- `tests/test_compare.py:35-45` pins the values and documents the reasoning: `./A` contributes zero shared sites; `A/G` and `G/A` are one matching unphased genotype; `A` and `A/A` agree under the documented hemizygous collapse.
- Corrected reproduction: `./A` gives concordance `(0, 0)` and discordance shared count 0; both equivalent-call cases give concordance `(1, 1)` and no discordance.

### F2 — VCF sidecar retention

- `biocore/io/vcf_ops.py:24-37` creates bcftools input manifests as unpredictable mode-0600 temporary files beside the requested output and removes them in a `finally` block. Reheader and merge both use this boundary (`vcf_ops.py:40-44`, `vcf_ops.py:48-62`).
- `tests/test_vcf_ops.py:10-37` and `tests/test_vcf_ops.py:40-73` pin permissions and cleanup after success and exceptions for both operations.
- The integration reproduction retained no `.name.txt` or `.merge_list.txt` files.

### F5 — default VCF merge argument order

- `biocore/io/vcf_ops.py:56-61` constructs `bcftools merge -0 -l <manifest> ...`, keeping `-l` adjacent to its filename.
- `tests/test_vcf_ops.py:40-59` pins that adjacency and requires `-0` before `-l`.
- The local pysam.bcftools-backed reproduction now succeeds. It produced records at positions 10 and 20 with the absent sample filled as `(0, 0)`, matching the documented `missing_to_ref=True` behavior.

## Final verification

The relevant full suite was run with:

`/Users/cct/miniconda3/bin/python -m pytest -q -rs --basetemp=/private/tmp/biocore-lane-tests-final`

It printed:

```text
=========================== short test summary info ============================
SKIPPED [1] tests/test_compare.py:104: could not import 'allel': No module named 'allel'
133 passed, 1 skipped in 0.15s
```

`git diff --check` also completed with no output.

## P2 findings and repository limits

- F8 was not changed. The repository has no retention-state input, deletion callback, or downstream service implementation, so it cannot establish that an uploaded file was deleted. Removing or conditionally rendering the promise requires an API/product decision not present here. Likewise, the repository does not define a safe rule for redacting diagnostic paths from arbitrary provider notes.
- F9 was not changed. The fixture's accession, owner permission, exact release, and redistribution terms cannot be established from this repository under the required offline constraint. Adding provenance would require authoritative information rather than a guessed citation or licence.
- F10 was not changed. The source distribution demonstrably omits the fixture while including fixture-dependent tests, but the repository does not state whether release archives are intended to carry biological fixtures or omit those tests. That packaging/privacy choice needs an owner decision, especially because F9 is unresolved.

No behavior was invented for these unresolved P2 items.
