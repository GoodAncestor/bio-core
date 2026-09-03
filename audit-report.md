**Blind audit — bio-core, 2026-09-05**

Audited commit `53bd1b1` in the existing detached worktree `/Users/cct/code/lanes-0905/w-bio`. No source files were fixed, refactored, committed, or merged. All execution was local; no network requests, dependency downloads, workflow dispatches, or third-party API calls were made. The request omitted the value of `-o`; this report uses `audit-report.md` as the fallback. All evidence needed to assess and rerun the findings is in this file, including the reproduction program below.

**Result: no demonstrated P0; seven P1 findings and three P2 findings.** The strongest reproduction takes a provider finding's topic field and causes a PDF to contain a separate local genotype file as an attachment. Two methylation errors change site inclusion or calculated percentages. These tests establish local behavior, not that a deployed service has already leaked a person's genome.

| ID | Rank | Finding |
|---|---|---|
| F1 | P1 | Unescaped finding metadata injects HTML and copies a local file into the PDF |
| F2 | P1 | VCF helpers retain readable sample-name and input-path manifests |
| F3 | P1 | Implicit canonical modBAM calls disappear: 20% becomes 100% methylation |
| F4 | P1 | bedMethyl coverage filter contradicts the documented column-10 contract |
| F5 | P1 | Default VCF merge constructs an invalid option sequence and fails |
| F6 | P1 | Partial diploid calls are reported as hemizygous, including on chromosome 1 |
| F7 | P1 | Concordance includes partial calls and depends on unphased allele order |
| F8 | P2 | Report asserts deletion without evidence and copies private diagnostic paths |
| F9 | P2 | Committed real biological fixture lacks verifiable dataset provenance/permission |
| F10 | P2 | Source distribution includes fixture-dependent tests but omits the fixture |

**Execution and limits.** Python 3.12.8, pytest 8.3.4, pysam 0.24.0: the repository suite produced **120 passed, 1 skipped in 0.26 seconds**. `tests/test_compare.py:89` skips because `allel` is unavailable. No IBS-distance or Mantel execution is claimed. There is no end-to-end organism-specific interpretation pipeline, clock model, clinical reference database, or CLI in this repository; those are downstream according to README. I ran the available readers, estimators, VCF helpers, report/PDF rendering, fixture tests, and builds.

The normal local setuptools 75.1.0 satisfies `setuptools>=61` but rejected `project.license = "Apache-2.0"`. Using already cached setuptools 84.0.0, without installing or downloading anything, built both the wheel and sdist successfully. The declared backend floor therefore does not guarantee an offline build; this is an observed packaging limitation, not evidence that current online CI fails. The source tests were run directly, not represented as a successful clean install. WeasyPrint 69.0 required `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` to find the already-installed native libraries; with that environment the PDF reproduction succeeded. The bcftools executable was absent, so the reproduction launches a thin executable wrapper around **real pysam.bcftools**, not a fake command result. Both successful VCF operations and the failing default merge went through that engine.

**F1 — P1: finding metadata can insert a local genotype file into a report.**

Location: `biocore/report/render.py:902` gathers raw topics; **line 905** inserts them into a single-quoted `data-topics` attribute without escaping. `to_pdf` at **line 1867** invokes WeasyPrint without restricting resource access. Related unescaped sinks are `tool_version` at line 1666 and the fallback outcome kind at line 766. Source links at lines 428, 551, and evidence-chain links at line 571 escape quotes but accept `javascript:` URLs.

Reproduction: run the embedded program and inspect its “Report retention, URLs, and PDF file read” output. It writes a synthetic private file containing `SYNTHETIC_ALICE 1-10-A-G A/G`, then sets `Finding.detail['topic']` to break out of the attribute and insert a `link rel="attachment"` referencing that file. It calls the unmodified `render_html` and `to_pdf`, then reads the resulting PDF attachments with pypdf. Actual output:

```text
attachment tag injected through Finding.detail.topic True
PDF attachments {'private-genotypes.txt': [b'SYNTHETIC_ALICE 1-10-A-G A/G']}
```

The injected file's contents were not supplied as finding text. The generated PDF itself contains the bytes, so publishing or returning that artifact would disclose them. The same missing escaping permits active browser markup; the reproduction also confirms that a `javascript:` source link survives HTML parsing and a version string inserts a literal script element. Browser script execution or network exfiltration was not attempted.

Cost of leaving it: anyone able to influence a finding topic, or another raw HTML sink, can change report markup. If the PDF process can read another sample's file and its path is known, that file can be bundled into the returned report. This is P1 rather than P0 because upstream control of topics and the production PDF process's filesystem permissions are outside this repository and were not established. The local data-to-artifact disclosure is nevertheless reproduced, not inferred from a dangerous function alone.

**F2 — P1: auxiliary VCF manifests retain identifying material.**

Location: `biocore/io/vcf_ops.py:25–29` and **40–47**. `reheader` writes the sample name into `Path(out_vcf).with_suffix('.name.txt')`; `merge` writes every caller-supplied input path into `.merge_list.txt`. Neither removes the file on success or exception. `.gitignore:1–7` ignores development caches/builds but does not exclude these biological sidecars.

Reproduction: the embedded program's “VCF sidecars and merge” section runs reheader, a failing default merge, and a successful merge with `missing_to_ref=False`. Actual retained files:

```text
renamed.vcf.name.txt       'SYNTHETIC_PATIENT_NAME\n'
merged.vcf.merge_list.txt  '/private/tmp/biocore-blind-audit/vcf/SYNTHETIC_ALICE.vcf.gz\n/private/tmp/biocore-blind-audit/vcf/SYNTHETIC_BOB.vcf.gz\n'
merged-no-fill.vcf.merge_list.txt  [the same two sample-bearing paths]
permissions: 0644 for all three under the audit process's umask
```

Cost of leaving it: copying an output directory, saving diagnostic attachments, or committing sidecars can disclose names and link samples to genomic outputs even when the VCF is withheld. The files remain after the main operation fails. Permissions are umask-dependent, not an unconditional library guarantee. No automatic public upload or past committed instance of these manifests was found; this finding establishes the material that such publication would expose.

**F3 — P1: implicit canonical bases are discarded from modBAM coverage.**

Location: `biocore/io/modbam.py:88–108`, especially the `if not mods: continue` at **89–90**, and iterating only decoded explicit calls at **98**. MM tags using `.` encode omitted canonical bases as implicit canonical calls. The implementation counts only positions returned as explicit modified-base entries, making equivalent encodings disagree.

Reproduction: “modBAM omitted canonical calls” writes five reads at the same cytosine: one high-probability modified call and four canonical calls. Encoding canonical calls explicitly as ML=0 produces `(n_mod=1, n_canonical=4, fraction=0.2)`. Encoding the same canonical information with `MM:C+m.;` produces `(1,0,1.0)`. At `min_coverage=5`, the explicit version is retained and the implicit version disappears. A two-cytosine example also loses an entirely canonical position.

Cost of leaving it: methylation is biased upward and legitimately covered sites are dropped according to tag encoding. A downstream summary, comparison, or clock can change without biological data changing. `tests/test_modbam.py:35–70` only exercises explicit selected calls; its passing counts do not validate implicit canonical semantics. This is a counting error before any organism-specific interpretation.

**F4 — P1: the coverage threshold uses a different denominator from the documented input contract.**

Location: `biocore/io/bedmethyl.py:62` and **79**, `biocore/methylation/model.py:49–50`; contradicted by `docs/PROVENANCE.md:11` (“cov=col10”) and `bedmethyl.py:40` (“valid coverage”). The reader discards column 10 and filters on `n_mod + n_canonical`. Valid coverage can also include other modification calls, so those values differ in real fixture rows.

Reproduction: “Coverage threshold” constructs two rows, both with column-10 coverage 5. Row A has 4 modified, 0 canonical, 1 other-modification read; row B has 0 modified and 5 canonical reads. Applying the documented coverage cutoff and the documented estimator gives **4/9 = 44.4444%**. The implementation drops row A and reports **0%**, with one covered site instead of two. The fixture has **114 of 5,000 rows** where column 10 differs from the implementation's coverage.

Cost of leaving it: sites are selected according to modification composition as well as coverage; studies using the documented cutoff can publish a different result. If modified-plus-canonical coverage is the intended scientific choice, the current contract and claimed recovered-awk equivalence are false and must be resolved before interpreting results. This audit does not prescribe which estimator the downstream study should use.

**F5 — P1: default cohort merge cannot execute.**

Location: `biocore/io/vcf_ops.py:42–45`, specifically **44**. Inserting `-0` at index 3 separates `-l` from its filename.

Reproduction: “VCF sidecars and merge” runs two valid bgzipped/indexed single-sample VCFs through the default API. Actual argument order:

```text
bcftools merge -l -0 /private/tmp/biocore-blind-audit/vcf/merged.vcf.merge_list.txt -Oz -o /private/tmp/biocore-blind-audit/vcf/merged.vcf.gz
```

The real engine returned an error including `Failed to open ...merged.vcf.merge_list.txt: unknown file type`; the wrapper raised `CalledProcessError`. With `missing_to_ref=False`, both records merged successfully and missing calls remained `(None, None)` for the absent sample. Biallelic extraction and tabix indexing then succeeded on that output.

Cost of leaving it: the documented default cohort-assembly pipeline fails before distance analysis, while leaving sensitive manifests. There is no repository test for these wrappers. The documented missing-to-reference bias is a separate, acknowledged modeling choice; it does not explain or excuse the malformed command.

**F6 — P1: a missing diploid allele is converted into biological hemizygosity.**

Location: `biocore/variants/carried.py:66–68`; downstream label in `biocore/report/render.py:519`.

Reproduction: “Genotypes” creates a chromosome-1 VCF with `GT=1/.`. `carried_variants` returns `genotype='./G', zygosity='hemi'`. Passing this returned zygosity into the report's typed interpretation yields the chip **“one copy (X or Y)”**, despite the call being a partially missing diploid chromosome-1 call.

Cost of leaving it: downstream interpretation can mistake missing evidence for ploidy, changing copy-count/inheritance explanations. The extractor can legitimately preserve the known ALT allele, but the unknown allele does not establish a single-copy chromosome. Existing tests cover a real haploid `GT=1` case and miss `GT=1/.`.

**F7 — P1: comparison does not consistently use fully called, equivalent genotypes.**

Location: `biocore/compare/genotype_calls.py:21–22` misses a leading missing allele; **25–32** does not normalize diploid allele order; **52–58** uses those results. The discordance helper at **99–120** compares raw strings and also contradicts hemizygous normalization.

Reproduction: “Genotypes” prints these outputs for one shared marker:

| A | B | Actual matching/shared | Actual discordance | Correct interpretation |
|---|---|---|---|---|
| `./A` | `./A` | `(1,1)` | none, shared=1 | Exclude an incompletely called marker; shared=0 |
| `A/G` | `G/A` | `(0,1)` | `other: 1` | Same unphased diploid genotype; matching=1 |
| `A` | `A/A` | `(1,1)` | `other: 1` | Concordance and discordance should agree under the documented collapse |

For `./A`, KING's independent classifier reports zero sites while concordance reports one, exposing an internal denominator disagreement. The carried-variant extractor sorts missing alleles first (`./G`), so this is also a plausible composition of this repository's APIs.

Cost of leaving it: agreement/QC percentages change with formatting and missingness, potentially altering sample-identity or reproducibility conclusions. The source comment at lines 11–13 promises shared, fully called markers. No claim is made that the tested biallelic KING formula itself is wrong.

**F8 — P2: the report makes an unsupported deletion promise.**

Location: `biocore/report/render.py:1094–1095`; diagnostic-note inclusion at **1028–1030**. Any truthy `scan_stats` inserts “deleted — it is not retained after this report is generated,” with no retention-state parameter, deletion callback, or check. This library's readers do not delete their inputs.

Reproduction: the report section of the program leaves a synthetic upload on disk, renders with `scan_stats={'markers_scanned':1}`, and prints `deletion promise True input still exists True`. A `ProviderStatus.note` containing the upload's absolute path is also copied verbatim as text into the report (`private path in report True`). HTML escaping prevents markup injection through that note but does not anonymize it.

Cost of leaving it: users may share a report or trust a retention promise that the API has no evidence to make. Diagnostic notes can disclose identifying filenames in otherwise carefully presented results. A downstream service may implement deletion correctly; this audit did not inspect one and does not allege that all deployed uploads are retained.

**F9 — P2: the real fixture's attribution and permission cannot be audited.**

Location: `tests/fixtures/sample_bedmethyl_5k.bed.gz` (binary artifact; decompressed records 1–5000) and `docs/PROVENANCE.md:21–24`. `NOTICE:1–5` attributes software to GoodAncestor but does not identify this dataset. The repository supplies no accession, original dataset authors/citation, release identifier, dataset terms, or explicit owner declaration establishing permission to redistribute the fixture. Its provenance is described as real eelgrass “sample 1,” not as generated synthetic data.

Reproduction: run `git log --all -- tests/fixtures/sample_bedmethyl_5k.bed.gz`, `git show 9ff7384:docs/PROVENANCE.md`, and inspect the current provenance/NOTICE plus the inventory below. The same fixture blob `e94c351fc02c120c824681c38d5a904d78cc3383` has been committed since `9ff7384`. Its 5,000 rows contain per-site measurements from one biological sample. The gzip header carries the generic original filename `sample_bedmethyl_5k.bed` and timestamp 1784835674; neither is an observed human sample identifier.

Cost of leaving it: a reviewer cannot verify redistribution rights, acknowledge the original dataset correctly, or recover the claimed validation input. This is a missing-evidence finding, **not a finding of licence infringement or human genomic disclosure**. The no-network constraint prevented independent verification of current external data terms. The repository's Apache licence alone does not document the fixture's provenance.

**F10 — P2: the distributed tests cannot reproduce fixture validation.**

Location: `pyproject.toml:16–18` supplies the setuptools backend but no fixture-inclusion configuration; `tests/test_bedmethyl.py:10` requires the omitted gzip file. The generated `biocore.egg-info/SOURCES.txt` includes all bedMethyl test code but not `tests/fixtures/sample_bedmethyl_5k.bed.gz`.

Reproduction: build with the offline command below, extract the generated sdist, then run its `tests/test_bedmethyl.py`. Actual result: **5 failed**, all `FileNotFoundError` for the missing fixture. The sdist has the tests; neither wheel nor sdist has the gzip fixture. This is also a useful privacy boundary in the observed build: the biological fixture is committed to Git but was not copied into either distribution.

Cost of leaving it: recipients cannot rerun the stated validation from the release archive. A passing source checkout does not establish a reproducible distributed test suite. This is distinct from F9's missing permission/provenance.

**Numbers checked against the documentation.**

The shipped 5,000-row fixture yields these measurements. Independent calculations below parse raw columns without using MethylSite or the library's aggregation code.

| Context | Total sites | Library covered sites | Column-10 covered sites | Library weighted methylation | Column-10-cutoff weighted methylation | Docs' 2M-line claim |
|---|---:|---:|---:|---:|---:|---:|
| CG | 618 | 395 | 405 | 92.899914% | 92.867212% | 48.9% |
| CHG | 755 | 508 | 510 | 40.734415% | 40.771526% | 16.5% |
| CHH | 3627 | 2218 | 2219 | 0.477587% | 0.477496% | 1.5% |

The independent column-10 counts sum modified/canonical reads to CG `(3294,253)`, CHG `(1913,2779)`, CHH `(101,21051)`. These agree with the table's ratios. The implementation is internally consistent with its own different cutoff. The large difference from 48.9/16.5/1.5 is **not proof the 2-million-line result is wrong**: only the first 5,000 rows are available, and genomic sampling is not uniform. The 2M input, recovered awk program, expected-output file, and numerical “boundary-effect tolerance” are absent, so the stated validation is not reproducible here. `test_weighted_methylation_matches_manual` repeats the library's own count/filter definition and never asserts the published percentages; it cannot independently validate that claim.

**History review.**

Inspected all 49 commits reachable from local refs, reflogs, and `git fsck --full --no-reflogs --unreachable`. Two additional local commits (`93588c1`, `df56c82`) and their objects were inspected: 51 commit trees, 56 distinct historical paths, and 137 distinct file blobs in total. The extra commits are report/glossary revisions; the unreachable renderer blob is code, not a separate data dump. Historical path enumeration uses every commit tree, so duplicate/renamed blobs are not missed.

The only historical path absent from HEAD is `biocore/providers_base.py`, a removed duplicate of provider code. No committed VCF/BAM, sample-name manifest, merge list, generated report, SQLite database, tabix index, or credential material was found in those objects. This is a statement about available local history; no remote refs or deleted hosted artifacts were queried.

Commit `dbc7b7a` removed an internal session frame and project identifier from prose, not from Git history. `git show 9ff7384:docs/PROVENANCE.md` still recovers them. They identify an analysis session/project, not an established human participant; their persistence is worth knowing without inflating it into a human-data leak. Ordinary commit author identity is also present, separate from biological sample identity. The real eelgrass fixture has one unchanged compressed blob throughout history.

**Public-artifact and persistence map.**

There is no deployment/storage implementation in this repo. Its workflows install dependencies, run tests, and request sibling image rebuilds. No workflow commits outputs or uploads reports/manifests as an artifact. The dispatch payload only specifies an event type. Public exposure therefore has identifiable boundaries: committed source/history; built distributions; downstream files/logs; and reports handed to a caller. The report contains individual variants, methylation readings, outcomes, and full marker IDs by design. Hiding rows with CSS/JavaScript, shortening an allele label, or changing report views is not redaction; full IDs remain in `data-marker` and links, and hidden findings remain in the HTML.

| Producer / files | Individual-level information it can retain | Observed boundary / handling |
|---|---|---|
| `reheader`: caller-named output VCF, `<out>.tbi`, `.name.txt` | Genotypes and sample names; index coordinates/counts; clear sample name sidecar | Output plus sidecar persist; F2 reproduced |
| `merge`: output VCF, `.tbi`, `.merge_list.txt` | Cohort genotypes/sample names; genomic index; absolute sample-bearing input paths | Sidecar persists on both failure and success |
| `biallelic_snps`: output VCF, `.tbi` | Genotypes, header sample identifiers, selected-coordinate index | Successful output reproduced; no anonymization claimed |
| bcftools output headers and captured stderr | Command history, filenames, sample/contig diagnostics | Captured by `_run`; exception can reach caller logs. Failure exposed a synthetic path; successful merge/view headers also retained absolute command paths |
| `fetch`: arbitrary `dest`, `<dest>.part`, parent directory | Full/partial downloaded data, sample-bearing filenames | Local-file execution verified checksum rejection leaves `.part`; successful completion replaces dest and removes part |
| `fetch` retry/error logs | Retry log has exception class/count/bytes; verification errors include dest basename | Retry text itself does not interpolate URL/body; caller-supplied logger decides persistence |
| `RateLimiter`: caller-named SQLite DB, possible rollback `-journal` | Key, calendar minute, count; a sample ID only if caller misuses key | Test databases contain service/test keys, no marker/genotype table. No WAL requested |
| `read_sites`, `summarize_by_context`, `pileup_methyl`, `carried_variants`, comparisons | In-memory per-site data, variants, labeled matrices, discordance examples | No automatic result-file writer; callers may serialize them |
| `Finding.to_dict`, `SampleReport` | Complete detail, interpretation, evidence, marker and genotype data | No anonymization; not a logging/public-summary API |
| `render_html` return value; caller HTML output; browser print/PDF; `to_pdf(out_path)` | Findings, full variant IDs, diagnostic notes, caller title, sources, timestamps, PDF attachments | Real artifact disclosure in F1; no implicit input deletion in F8 |
| Git checkout / repository archive | Everything committed, including biological fixture and synthetic test constants | Full current/history inventory below |
| wheel, sdist, build staging, egg-info, distribution metadata | Source literals, synthetic test fixtures in sdist, filenames in manifests, build paths/logs | Observed membership below; biological gzip not packaged |
| pytest temp VCF/BAM/FASTA and indexes | Sample IDs and genomic content if tests use real inputs | Current authored test sequences/calls are synthetic examples; generated files persist in basetemp |
| `.pytest_cache`, Python `.pyc`, build/test stdout/stderr | Node IDs/failure names, source paths and string literals, exception values on failures | Current successful tests show test names, not linked individual data; `.pyc` can retain embedded fixture constants |
| pip/CI install caches and logs, workflow runner checkout | Source archives/wheels, installation paths and diagnostics | CI not executed; no existing CI cache artifacts inspected; cannot enumerate external runner files offline |

The next inventories enumerate concrete repository/build/test files. “Can carry” means the file's format/content role could contain individual-level material; it does not mean a human participant was found. There is no way to enumerate arbitrary caller-selected filenames or files created by uninspected downstream providers; those are described by producer above.

**Every committed file at HEAD, with observed package membership.** W = wheel; S = source distribution; “—” = neither.

| File | Individual-level assessment | Package |
|---|---|---|
| `.github/workflows/notify-images.yml` | No sample data observed; configuration/prose/software attribution only | — |
| `.github/workflows/test.yml` | No sample data observed; configuration/prose/software attribution only | — |
| `.gitignore` | No sample data observed; configuration/prose/software attribution only | — |
| `LICENSE` | No sample data observed; configuration/prose/software attribution only | S |
| `NOTICE` | No sample data observed; configuration/prose/software attribution only | S |
| `README.md` | No sample data observed; configuration/prose/software attribution only | S |
| `biocore/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/compare/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/compare/distance.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/compare/genotype_calls.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/io/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/io/bedmethyl.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/io/fetch.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/io/modbam.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/io/vcf_ops.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/methylation/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/methylation/model.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/net/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/net/pace.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/providers/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/providers/base.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/providers/registry.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/report/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/report/render.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/report/sources.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/report/terms.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/variants/__init__.py` | Can: code literals/examples; no linked individual data observed | WS |
| `biocore/variants/carried.py` | Can: code literals/examples; no linked individual data observed | WS |
| `docs/DISCLAIMER.md` | No sample data observed; configuration/prose/software attribution only | — |
| `docs/PROVENANCE.md` | Can: sample/session provenance; sample 1 here, no human linkage | — |
| `pyproject.toml` | No sample data observed; configuration/prose/software attribution only | S |
| `tests/fixtures/sample_bedmethyl_5k.bed.gz` | Yes: real single-eelgrass per-site measurements; no human linkage found | — |
| `tests/test_bedmethyl.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_card_reading.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_carried.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_carried_zygosity.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_compare.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_direction.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_glossary_link.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_inline_glossary.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_interpretation_model.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_magnitude.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_meaning_card.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_modality_breakdown.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_modbam.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_outcome_view.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_pace.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_rail_and_views.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_reference_and_uncarried.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_render.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_report_scale.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_source_attribution.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_sources_scan.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_stat_display.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |
| `tests/test_terms.py` | Can: authored synthetic variant/sequence/report literals; no donor linkage found | S |

Historical-only file: `biocore/providers_base.py` — code duplicate, no observed individual-level records. Build/tests contain no automatic Git commit operation.

**Every additional archive member (files, not directory entries).** Package source/test files are mapped individually above. These remaining metadata files can reveal filenames, source constants, or sample-bearing file names if packaging inputs change; this build contained only the source/metadata listed.

| Archive | Additional member | Individual-level assessment |
|---|---|---|
| wheel | `biocore-0.0.1.dist-info/licenses/LICENSE` | Package metadata/licence/prose; no individual record observed |
| wheel | `biocore-0.0.1.dist-info/licenses/NOTICE` | Package metadata/licence/prose; no individual record observed |
| wheel | `biocore-0.0.1.dist-info/METADATA` | Package metadata/licence/prose; no individual record observed |
| wheel | `biocore-0.0.1.dist-info/WHEEL` | Package metadata/licence/prose; no individual record observed |
| wheel | `biocore-0.0.1.dist-info/top_level.txt` | Package metadata/licence/prose; no individual record observed |
| wheel | `biocore-0.0.1.dist-info/RECORD` | Filename/hash manifest; no individual sample name observed |
| sdist | `PKG-INFO` | Package metadata/licence/prose; no individual record observed |
| sdist | `biocore.egg-info/PKG-INFO` | Package metadata/licence/prose; no individual record observed |
| sdist | `biocore.egg-info/SOURCES.txt` | Filename/hash manifest; no individual sample name observed |
| sdist | `biocore.egg-info/dependency_links.txt` | Package metadata/licence/prose; no individual record observed |
| sdist | `biocore.egg-info/requires.txt` | Package metadata/licence/prose; no individual record observed |
| sdist | `biocore.egg-info/top_level.txt` | Package metadata/licence/prose; no individual record observed |
| sdist | `setup.cfg` | Package metadata/licence/prose; no individual record observed |

Built archives: `biocore-0.0.1-py3-none-any.whl` and `biocore-0.0.1.tar.gz`. SHA-256: `b19a9736076fea8677d27c31ffd00e64ad1e0e4d1b8db4a590190301e0977fcf` and `7f558a001386ba97ccf54a3e4287f11294377d3cfa6a1f2bcbaac41841d0a73b`, respectively. These archives contain exactly the file memberships above. Transient sdist staging mirrors its member paths; wheel build staging mirrors `biocore/*.py` paths and its dist-info entries. The build log records those copy destinations. No additional fixture table or secret manifest appeared in the build.

**Every observed source-tree build/test byproduct and pytest data file.** Paths below are relative to the worktree, except `pytest-temp/`, which is under `/private/tmp/biocore-blind-audit`.

| File | Individual-level assessment |
|---|---|
| `.pytest_cache/.gitignore` | Test names or package metadata; no individual record observed |
| `.pytest_cache/CACHEDIR.TAG` | Test names or package metadata; no individual record observed |
| `.pytest_cache/README.md` | Test names or package metadata; no individual record observed |
| `.pytest_cache/v/cache/nodeids` | Test names or package metadata; no individual record observed |
| `.pytest_cache/v/cache/stepwise` | Test names or package metadata; no individual record observed |
| `biocore/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/compare/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/compare/__pycache__/genotype_calls.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/io/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/io/__pycache__/bedmethyl.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/io/__pycache__/fetch.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/io/__pycache__/modbam.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/io/__pycache__/vcf_ops.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/methylation/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/methylation/__pycache__/model.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/net/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/net/__pycache__/pace.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/providers/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/providers/__pycache__/base.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/report/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/report/__pycache__/render.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/report/__pycache__/sources.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/report/__pycache__/terms.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/variants/__pycache__/__init__.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore/variants/__pycache__/carried.cpython-312.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `biocore.egg-info/PKG-INFO` | Test names or package metadata; no individual record observed |
| `biocore.egg-info/SOURCES.txt` | File-list manifest; no sample filenames observed |
| `biocore.egg-info/dependency_links.txt` | Test names or package metadata; no individual record observed |
| `biocore.egg-info/requires.txt` | Test names or package metadata; no individual record observed |
| `biocore.egg-info/top_level.txt` | Test names or package metadata; no individual record observed |
| `tests/__pycache__/test_bedmethyl.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_card_reading.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_carried.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_carried_zygosity.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_compare.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_direction.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_glossary_link.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_inline_glossary.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_interpretation_model.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_magnitude.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_meaning_card.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_modality_breakdown.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_modbam.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_outcome_view.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_pace.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_rail_and_views.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_reference_and_uncarried.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_render.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_report_scale.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_source_attribution.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_sources_scan.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_stat_display.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `tests/__pycache__/test_terms.cpython-312-pytest-8.3.4.pyc` | Source path and literal constants (including synthetic fixtures); not runtime sample memory |
| `pytest-temp/modbam0/ref.fa` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/modbam0/ref.fa.fai` | Genomic position index; relates to synthetic test sequence |
| `pytest-temp/modbam0/test.bam` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/modbam0/test.sorted.bam` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/modbam0/test.sorted.bam.bai` | Genomic position index; relates to synthetic test sequence |
| `pytest-temp/test_acquire_refuses_rather_th0/r.db` | Service/test key, minute and request count; inspected keys are not sample IDs |
| `pytest-temp/test_acquire_waits_for_the_nex0/r.db` | Service/test key, minute and request count; inspected keys are not sample IDs |
| `pytest-temp/test_carried_skips_homref_and_0/s.vcf` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/test_haploid_is_hemi_and_missi0/s.vcf` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/test_het_call_carries_quality_0/s.vcf` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/test_hom_and_filter_label0/s.vcf` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/test_n_samples0/s.vcf` | Synthetic sample/sequence/genotype fixture, potentially sensitive if replaced with real data |
| `pytest-temp/test_separate_keys_do_not_shar0/r.db` | Service/test key, minute and request count; inspected keys are not sample IDs |
| `pytest-temp/test_two_limiters_on_one_db_sh0/r.db` | Service/test key, minute and request count; inspected keys are not sample IDs |
| `pytest-temp/test_window_allows_exactly_the0/r.db` | Service/test key, minute and request count; inspected keys are not sample IDs |
| `pytest-temp/test_window_resets_on_the_minu0/r.db` | Service/test key, minute and request count; inspected keys are not sample IDs |

Pytest also creates `*current` directory symlinks to its latest temporary cases, and a transient basetemp lock; these identify filesystem paths, not extra genomic records. The run did not request JUnit XML, HTML test reports, coverage files, or application result tables. They were not produced. SQLite can create/remove a transaction journal; a persistent journal was not observed.

**Reference-data attribution and licence review.**

The only committed measurement dataset is the eelgrass gzip fixture (F9). The modBAM reference FASTA and all VCFs created by tests are authored synthetic inputs, with no external reference download. No GRCh38 reference sequence, ClinVar dump, gnomAD file, methylation clock coefficients, EWAS table, or other clinical reference dataset was bundled in the observed wheel/sdist. “GRCh38” in an identifier docstring is not a build-validation check or evidence that a GRCh38 dataset was used.

The registry below records the repository's own licence assertions. They were inspected locally; they are **not independent confirmation of current licence terms**. Attribution tests pass for registered direct sources and AlphaMissense/AlphaGenome enrichments. Displaying a source name and a licence label does not by itself prove that downstream redistribution, attribution particulars, share-alike, or noncommercial obligations are satisfied. Relevant audit follow-up requires the exact dataset release and its supplied terms, unavailable here.

| Registry key | Named producer | Repository's licence assertion | Evidence/obligation boundary |
|---|---|---|---|
| `clinvar` | NCBI — ClinVar | Public domain | Registry attribution only; no underlying dataset release/terms supplied |
| `clinvar_mirror` | NCBI — ClinVar (full mirror) | Public domain | Registry attribution only; no underlying dataset release/terms supplied |
| `gwas_catalog` | NHGRI-EBI — GWAS Catalog | CC BY 4.0 | Registry attribution only; no underlying dataset release/terms supplied |
| `gnomad` | Broad Institute — gnomAD | ODbL / free | Registry attribution only; no underlying dataset release/terms supplied |
| `cpic` | Clinical Pharmacogenetics Implementation Consortium — CPIC | CC BY-SA 4.0 | Registry attribution only; no underlying dataset release/terms supplied |
| `alphamissense` | Google DeepMind — AlphaMissense | CC BY-NC-SA 4.0 | Noncommercial flag and prediction attribution shown; underlying release/API terms not bundled |
| `alphagenome` | Google DeepMind — AlphaGenome | Non-commercial API terms | Noncommercial flag and prediction attribution shown; underlying release/API terms not bundled |
| `ewas_catalog` | MRC-IEU, University of Bristol — EWAS Catalog | Academic / cite | Registry attribution only; no underlying dataset release/terms supplied |
| `gdc` | NCI Genomic Data Commons — GDC / TCGA | NIH data use | Registry attribution only; no underlying dataset release/terms supplied |
| `uniprot` | UniProt Consortium — UniProt | CC BY 4.0 | Registry attribution only; no underlying dataset release/terms supplied |
| `marker_reference` | per-marker citation — Published reference values | Per-paper citation | Per-model/per-paper attribution must come from downstream; no exact model/paper specified here |
| `epigenetic_clock` | published clock models — Epigenetic clocks | Per-clock citation | Per-model/per-paper attribution must come from downstream; no exact model/paper specified here |
| `ewas_atlas` | CNCB-NGDC — EWAS Atlas | Academic / cite | Registry attribution only; no underlying dataset release/terms supplied |
| `methbank` | CNCB-NGDC — MethBank | Academic / cite | Registry attribution only; no underlying dataset release/terms supplied |

`report/sources.py:110` aliases `pharmgkb` to CPIC and `opengwas` to GWAS Catalog. The alias mechanism is test-covered, but that does not establish those services' datasets are interchangeable or that actual upstream attribution is complete. With no downstream dataset/provider implementation, this is an attribution verification gap, not a demonstrated licence violation. The panel also asserts the tool is a nonprofit/noncommercial service at `render.py:1043–1045` regardless of caller; this generic library cannot establish its caller's status.

**What held up.**

The weighted-sum formula agrees with independent arithmetic when the same sites are selected; it is not accidentally a mean of site fractions. Explicit modBAM probabilities, the tested CpG context, and explicit coverage filtering pass. The default carried-variant tests correctly skip reference/no-call sites, split carried ALT alleles, normalize chromosome prefixes, and retain quality metadata; carrying a LowQual label is intentional and is not misreported here as a hidden filter. The tested biallelic KING self-comparison is 0.5 and its no-heterozygote denominator produces NaN. Rate-limiter tests pass for shared counters, separate keys, and deadline-limited waiting. Source attribution/enrichment tests pass for their declared mapping. Most report text fields escape markup correctly; F1 identifies specific exceptions. VCF subprocess arguments are a list with no shell interpolation, so sample names are not a demonstrated shell-command injection. The fetch checksum test refused wrong bytes, retained its resumable partial, then accepted matching bytes and removed the partial. Current CI gates image dispatch on the test job and has no sample-bearing dispatch payload.

**Rerunning the audit evidence.**

Run from the unchanged checkout at the audited commit. The following commands use the already-installed local Python and cached backend used in this audit. On another offline machine, point `PY` to Python 3.12 with pytest/pysam and, for the PDF section, weasyprint/pypdf plus its native libraries. No dependency installation or networking is part of the reproduction. The synthetic payload only reads the synthetic file it creates.

```sh
PY=/Users/cct/miniconda3/bin/python
"$PY" -m pytest -q -rs --basetemp=/private/tmp/biocore-audit-rerun-tests
PYTHONPATH=/Users/cct/.cache/uv/archive-v0/lTYeg74X-iWJpsfK \
  "$PY" -m build --no-isolation --outdir /private/tmp/biocore-audit-rerun-dist
```

Save the following complete program as `/private/tmp/biocore-audit-repro.py`, then run:

```sh
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  /Users/cct/miniconda3/bin/python /private/tmp/biocore-audit-repro.py
```

It uses `/private/tmp/biocore-blind-audit` for synthetic files. It installs a Python audit hook rejecting socket connections, binding, and address lookup; bcftools subprocesses operate only on these local files. Each finding above names its output section. The section wrapper prints exceptions so unavailable PDF libraries do not erase the other evidence; **inspect all output**, rather than treating the program's process status as a pass/fail suite.

```python
import os, sys, json, gzip, subprocess, traceback
from pathlib import Path
sys.path.insert(0, os.getcwd())
ROOT=Path('/private/tmp/biocore-blind-audit'); ROOT.mkdir(exist_ok=True)
def offline(event,args):
    if event in ('socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo'):
        raise RuntimeError('Audit forbids network: '+event)
sys.addaudithook(offline)
def section(name,fn):
    print('\n###',name,flush=True)
    try: fn()
    except Exception: traceback.print_exc()

def vcf():
    import pysam
    from biocore.io.vcf_ops import reheader, merge, biallelic_snps
    bindir=ROOT/'bin';bindir.mkdir(exist_ok=True)
    exe=bindir/'bcftools'
    exe.write_text('#!'+sys.executable+'\nimport sys,pysam.bcftools\ngetattr(pysam.bcftools,sys.argv[1])(*sys.argv[2:],catch_stdout=False)\n')
    exe.chmod(0o700);os.environ['PATH']=str(bindir)+os.pathsep+os.environ['PATH']
    d=ROOT/'vcf';d.mkdir(exist_ok=True)
    for name,pos in [('SYNTHETIC_ALICE',10),('SYNTHETIC_BOB',20)]:
        p=d/(name+'.vcf')
        p.write_text('##fileformat=VCFv4.2\n##contig=<ID=1,length=100>\n##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t'+name+'\n1\t'+str(pos)+'\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n')
        pysam.tabix_compress(str(p),str(p)+'.gz',force=True);pysam.tabix_index(str(p)+'.gz',preset='vcf',force=True)
    reheader(str(d/'SYNTHETIC_ALICE.vcf.gz'),'SYNTHETIC_PATIENT_NAME',str(d/'renamed.vcf.gz'))
    inputs=[str(d/(name+'.vcf.gz')) for name in ('SYNTHETIC_ALICE','SYNTHETIC_BOB')]
    try: merge(inputs,str(d/'merged.vcf.gz'))
    except subprocess.CalledProcessError as e: print('default merge failed:',e.cmd,'stderr:',e.stderr.strip())
    merge(inputs,str(d/'merged-no-fill.vcf.gz'),missing_to_ref=False)
    biallelic_snps(str(d/'merged-no-fill.vcf.gz'),str(d/'snps.vcf.gz'),regions=['1'])
    for p in sorted(d.glob('*.txt')):print(p.name,repr(p.read_text()))
    print('output modes',{p.name:oct(p.stat().st_mode&0o777) for p in d.glob('*.txt')})
    print('genotypes without fill',[(r.pos,[r.samples[s]['GT'] for s in r.samples]) for r in pysam.VariantFile(str(d/'merged-no-fill.vcf.gz'))])
section('VCF sidecars and merge',vcf)

def methyl():
    from biocore.io.bedmethyl import read_sites,summarize_by_context
    from biocore.methylation.model import weighted_methylation
    p=ROOT/'threshold.bed'
    p.write_text('1\t0\t1\tm,CG,0\t5\t+\t0\t1\t0,0,0\t5\t80\t4\t0\t1\t0\t0\t0\t0\n1\t1\t2\tm,CG,0\t5\t+\t1\t2\t0,0,0\t5\t0\t0\t5\t0\t0\t0\t0\t0\n')
    print('coverage filter sites',[(s.pos,s.coverage) for s in read_sites(str(p),min_coverage=5)])
    print('synthetic summary',summarize_by_context(str(p)),'documented col10 result',4/9)
    p=Path('tests/fixtures/sample_bedmethyl_5k.bed.gz'); agg={}
    for line in gzip.open(p,'rt'):
        f=line.rstrip().split('\t');a=agg.setdefault(f[3].split(',')[1],[0,0,0])
        if int(f[9])>=5:a[0]+=1;a[1]+=int(f[11]);a[2]+=int(f[12])
    print('fixture actual',json.dumps(summarize_by_context(str(p)),sort_keys=True))
    print('fixture col10', {k:{'covered':v[0],'nmod':v[1],'ncan':v[2],'weighted':v[1]/(v[1]+v[2])} for k,v in agg.items()})
section('Coverage threshold',methyl)

def modbam():
    import pysam
    from biocore.io.modbam import pileup_methyl
    d=ROOT/'mods';d.mkdir(exist_ok=True)
    # Equivalent implicit (.) vs explicit probability-zero canonical bases.
    for implicit in (True,False):
        p=d/('implicit.bam' if implicit else 'explicit.bam')
        with pysam.AlignmentFile(str(p),'wb',header={'HD':{'VN':'1.6'},'SQ':[{'SN':'1','LN':100}]}) as out:
            for n in range(5):
                a=pysam.AlignedSegment();a.query_name=f'read{n}';a.query_sequence='CC';a.flag=0;a.reference_id=0;a.reference_start=0;a.cigar=[(0,2)];a.mapping_quality=60
                a.set_tag('MM','C+m.,0;' if implicit else 'C+m?,0,0;','Z');a.set_tag('ML',[240] if implicit else [240,0]);out.write(a)
        print(p.name,[(s.pos,s.n_mod,s.n_canonical,s.fraction) for s in pileup_methyl(str(p))])
    # Same position: one high call and four implicit canonical calls.
    for implicit in (True,False):
        p=d/('mixed-implicit.bam' if implicit else 'mixed-explicit.bam')
        with pysam.AlignmentFile(str(p),'wb',header={'HD':{'VN':'1.6'},'SQ':[{'SN':'1','LN':100}]}) as out:
            for n in range(5):
                a=pysam.AlignedSegment();a.query_name=f'read{n}';a.query_sequence='C';a.flag=0;a.reference_id=0;a.reference_start=0;a.cigar=[(0,1)];a.mapping_quality=60
                a.set_tag('MM','C+m.;' if implicit and n else 'C+m?,0;','Z')
                if not implicit or n==0:a.set_tag('ML',[240 if n==0 else 0])
                out.write(a)
        print(p.name,[(s.pos,s.n_mod,s.n_canonical,s.fraction) for s in pileup_methyl(str(p))], 'at cov5',list(pileup_methyl(str(p),min_coverage=5)))
section('modBAM omitted canonical calls',modbam)

def genotypes():
    from biocore.compare.genotype_calls import concordance_pair,discordance_breakdown,king_relatedness
    from biocore.variants.carried import carried_variants
    for a,b in [('./A','./A'),('A/G','G/A'),('A','A/A')]:
        print(a,b,'concordance',concordance_pair({'rs':a},{'rs':b}),'discordance',discordance_breakdown({'rs':a},{'rs':b}),'KING sites',king_relatedness({'rs':a},{'rs':b})['n_sites'])
    p=ROOT/'partial.vcf';p.write_text('##fileformat=VCFv4.2\n##contig=<ID=1,length=100>\n##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n1\t10\t.\tA\tG\t60\tPASS\t.\tGT\t1/.\n')
    print('partial diploid',carried_variants(str(p)))
    from biocore.providers.base import Finding,Tier,Category,Interpretation
    from biocore.report.render import render_html
    z=carried_variants(str(p))[0]['zygosity']
    f=Finding('1-10-A-G','clinvar','Synthetic',Tier.ROBUST,[Category.CLINICAL],interpretation=Interpretation('Found','Meaning','Evidence','Next',zygosity=z))
    print('partial chr1 call rendered as X/Y', 'one copy (X or Y)' in render_html([f],[]))
section('Genotypes',genotypes)

def reports():
    from biocore.report.render import render_html,to_pdf
    from biocore.providers.base import Finding,Tier,Category,ProviderStatus,Health
    from html.parser import HTMLParser
    f=Finding('1-10-A-G','clinvar','SYNTHETIC_GENOTYPE_A/G',Tier.ROBUST,[Category.CLINICAL],link='javascript:globalThis.auditRead=document.body.textContent')
    raw=ROOT/'retained-upload.vcf';raw.write_text('SYNTHETIC_PRIVATE_INPUT')
    h=render_html([f],[ProviderStatus('provider',Health.OK,note=str(raw))],scan_stats={'markers_scanned':1},tool_version='<script>globalThis.auditRead=document.body.textContent</script>')
    (ROOT/'report.html').write_text(h)
    print('deletion promise', 'deleted — it is not retained' in h,'input still exists',raw.exists(),'private path in report',str(raw) in h)
    class Parser(HTMLParser):
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='a' and attrs.get('href','').startswith('javascript:'): print('executable href',attrs['href'])
    Parser().feed(h)
    print('raw version script inserted','<script>globalThis.auditRead=document.body.textContent</script>' in h)
    # Demonstrate a real local-only file disclosure through raw version HTML into PDF.
    secret=ROOT/'private-genotypes.txt';secret.write_text('SYNTHETIC_ALICE 1-10-A-G A/G')
    payload='<link rel="attachment" href="'+secret.as_uri()+'" download="private-genotypes.txt">'
    f.detail['topic'] = "'>" + payload + "<span data-x='"
    h=render_html([f],[])
    print('attachment tag injected through Finding.detail.topic',payload in h)
    to_pdf(h,str(ROOT/'attachment.pdf'))
    from pypdf import PdfReader
    pdf=PdfReader(str(ROOT/'attachment.pdf'))
    print('PDF attachments',dict(pdf.attachments))
section('Report retention, URLs, and PDF file read',reports)
```

**Observed reproduction output.**

```text

### VCF sidecars and merge
default merge failed: ['bcftools', 'merge', '-l', '-0', '/private/tmp/biocore-blind-audit/vcf/merged.vcf.merge_list.txt', '-Oz', '-o', '/private/tmp/biocore-blind-audit/vcf/merged.vcf.gz'] stderr: Traceback (most recent call last):
  File "/private/tmp/biocore-blind-audit/bin/bcftools", line 3, in <module>
    getattr(pysam.bcftools,sys.argv[1])(*sys.argv[2:],catch_stdout=False)
  File "/Users/cct/miniconda3/lib/python3.12/site-packages/pysam/utils.py", line 84, in __call__
    raise SamtoolsError(
pysam.utils.SamtoolsError: 'bcftools returned with error -1: stdout=None, stderr=Failed to open /private/tmp/biocore-blind-audit/vcf/merged.vcf.merge_list.txt: unknown file type\n'
merged-no-fill.vcf.merge_list.txt '/private/tmp/biocore-blind-audit/vcf/SYNTHETIC_ALICE.vcf.gz\n/private/tmp/biocore-blind-audit/vcf/SYNTHETIC_BOB.vcf.gz\n'
merged.vcf.merge_list.txt '/private/tmp/biocore-blind-audit/vcf/SYNTHETIC_ALICE.vcf.gz\n/private/tmp/biocore-blind-audit/vcf/SYNTHETIC_BOB.vcf.gz\n'
renamed.vcf.name.txt 'SYNTHETIC_PATIENT_NAME\n'
output modes {'renamed.vcf.name.txt': '0o644', 'merged.vcf.merge_list.txt': '0o644', 'merged-no-fill.vcf.merge_list.txt': '0o644'}
genotypes without fill [(10, [(0, 1), (None, None)]), (20, [(None, None), (0, 1)])]

### Coverage threshold
coverage filter sites [(1, 5)]
synthetic summary {'CG': {'n_sites': 2, 'n_sites_covered': 1, 'weighted_methylation': 0.0}} documented col10 result 0.4444444444444444
fixture actual {"CG": {"n_sites": 618, "n_sites_covered": 395, "weighted_methylation": 0.9289991445680068}, "CHG": {"n_sites": 755, "n_sites_covered": 508, "weighted_methylation": 0.4073441502988898}, "CHH": {"n_sites": 3627, "n_sites_covered": 2218, "weighted_methylation": 0.004775865330054851}}
fixture col10 {'CHH': {'covered': 2219, 'nmod': 101, 'ncan': 21051, 'weighted': 0.004774962178517398}, 'CHG': {'covered': 510, 'nmod': 1913, 'ncan': 2779, 'weighted': 0.4077152600170503}, 'CG': {'covered': 405, 'nmod': 3294, 'ncan': 253, 'weighted': 0.9286721172822103}}

### modBAM omitted canonical calls
implicit.bam [(0, 5, 0, 1.0)]
explicit.bam [(0, 5, 0, 1.0), (1, 0, 5, 0.0)]
mixed-implicit.bam [(0, 1, 0, 1.0)] at cov5 []
mixed-explicit.bam [(0, 1, 4, 0.2)] at cov5 [MethylSite(chrom='1', pos=0, context=<Context.UNKNOWN: '?'>, n_mod=1, n_canonical=4, strand='+')]

### Genotypes
./A ./A concordance (1, 1) discordance ({}, [], 1) KING sites 0
A/G G/A concordance (0, 1) discordance ({'other': 1}, [('rs', 'A/G', 'G/A', 'other')], 1) KING sites 1
A A/A concordance (1, 1) discordance ({'other': 1}, [('rs', 'A', 'A/A', 'other')], 1) KING sites 1
partial diploid [{'variant_id': '1-10-A-G', 'genotype': './G', 'platform': 'WGS', 'zygosity': 'hemi', 'filter': 'PASS', 'qual': 60.0, 'gq': None, 'dp': None}]
partial chr1 call rendered as X/Y True

### Report retention, URLs, and PDF file read
deletion promise True input still exists True private path in report True
executable href javascript:globalThis.auditRead=document.body.textContent
raw version script inserted True
attachment tag injected through Finding.detail.topic True
PDF attachments {'private-genotypes.txt': [b'SYNTHETIC_ALICE 1-10-A-G A/G']}
```

For F10, after the build command above, extract the archive and run its fixture tests (choose a fresh destination if rerunning):

```sh
/Users/cct/miniconda3/bin/python - <<'EXTRACT'
import tarfile
from pathlib import Path
archive = Path('/private/tmp/biocore-audit-rerun-dist/biocore-0.0.1.tar.gz')
with tarfile.open(archive) as source:
    source.extractall('/private/tmp/biocore-blind-audit/sdist-check', filter='data')
EXTRACT
```

Then run:

```sh
/Users/cct/miniconda3/bin/python -m pytest -q \
  /private/tmp/biocore-blind-audit/sdist-check/biocore-0.0.1/tests/test_bedmethyl.py
```

The audit's corresponding extraction path above was actually created. Observed summary:

```text
=========================== short test summary info ============================
FAILED ../../../../../private/tmp/biocore-blind-audit/sdist-check/biocore-0.0.1/tests/test_bedmethyl.py::test_reads_all_three_contexts
FAILED ../../../../../private/tmp/biocore-blind-audit/sdist-check/biocore-0.0.1/tests/test_bedmethyl.py::test_column_contract
FAILED ../../../../../private/tmp/biocore-blind-audit/sdist-check/biocore-0.0.1/tests/test_bedmethyl.py::test_weighted_methylation_matches_manual
FAILED ../../../../../private/tmp/biocore-blind-audit/sdist-check/biocore-0.0.1/tests/test_bedmethyl.py::test_context_filter
FAILED ../../../../../private/tmp/biocore-blind-audit/sdist-check/biocore-0.0.1/tests/test_bedmethyl.py::test_summarize_by_context
5 failed in 0.07s
```

The audit-generated synthetic VCFs, BAMs, PDFs, helper program, logs, and distribution copies live in `/private/tmp/biocore-blind-audit`; they are evidence files, not proposed source changes. The HTML/PDF evidence intentionally contains synthetic identifiers and the demonstrated attachment. This report is the sole requested deliverable. No findings depend on opening those separate evidence files.

Final verification: `git diff --exit-code` succeeded. `git status --short` showed only `?? audit-report.md`; generated caches/egg-info are ignored. No tracked source or fixture bytes changed.
