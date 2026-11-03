# Pig IgBLAST pipeline implementation plan

## Goal

Refactor `PigIgblast` into a complete, restartable pig-specific pipeline:

```text
sample matching -> fastp -> clean header -> PANDAseq -> sample-specific IgBLAST
```

The pipeline must preserve raw-data relative paths, name outputs by submission `sample_id`, accept only `Speicies=Pig`, and route each sample only to the chains declared by `Chain`.

The current workbook contains these routes:

| Dual Index | sample_id | Chain | IgBLAST chains |
|---|---|---|---|
| A02 | AB1_0618 | ab | TRA,TRB |
| A03 | AB2_0618 | ab | TRA,TRB |
| A05 | A_0618 | TCRα | TRA |
| A06 | B_0618 | TCRβ | TRB |

The example filename contains `A01`, which is absent from the workbook. It must be reported as an unmatched error and must not be guessed to another sample.

## Target files and stage layout

Create/refactor:

```text
PigIgblast/
├── pipeline_config.env
├── pipeline_config.py
├── run_pig_pipeline.sh
├── stop.sh
├── 01.match_sample.py
├── 02.run_fastp.sh
├── 03.clean_header.py
├── 04.work_pandaseq.sh
├── 05.work_igblastn.py
├── 01.match/sample_manifest.csv
├── 02.fastp/data + report
├── 03.clean_data
├── 04.pandaseq
└── 05.igblastn_out
```

Retire the unsafe global-process behavior in `stop_igblast.sh`; retain a compatibility wrapper only if needed.

Implementation note: active scripts now live under `PigIgblast/pipeline/`. All
stage data, reports, summaries, PID and log files are written to the sibling
directory `PigIgblast/output/` via `OUTPUT_ROOT` in `pipeline_config.env`.

Do not add the 10X TSO, primer, cell-barcode, UMI, split, or representative-sequence stages. They are not part of this pig paired-end workflow.

## 1. Central configuration

Add a branch-local `pipeline_config.env` and Python loader. Configure only absolute raw/reference inputs and branch-relative outputs.

Required settings include:

- raw FASTQ root and `_R1.fq.gz` / `_R2.fq.gz` suffixes. Pig pipeline pairing uses
  `R1/R2` exclusively; the legacy `_j/_v` convention is not used;
- submission XLSX path and first-sheet selection;
- pig IgBLAST root, executable, auxiliary file, and database version;
- CPU and 300 GB memory budgets;
- per-stage threads, memory per job, and maximum jobs;
- cleanup, partial-sample policy, and force-rerun switches.

Compute effective concurrency as the minimum of configured jobs, CPU budget divided by threads per task, and memory budget divided by memory per task. Reject a configuration that cannot run one task.

## 2. Submission and FASTQ matching

Implement `01.match_sample.py` as the only source of sample identity for all later stages.

### Workbook rules

- Read the first worksheet.
- Normalize headers with BOM removal, Unicode NFKC, whitespace collapse, and Chinese/English punctuation normalization.
- Resolve the actual columns:
  - `Sample ID File new name`;
  - a header beginning with `Dual Index`;
  - `Speicies`, while also accepting corrected `Species`;
  - `Chain`;
  - a header beginning with `Note`.
- Carry the merged `Note` value downward. The current workbook merges `N2:N5`.
- Require non-empty, safe `sample_id` and Dual Index values.
- Reject conflicting duplicate sample IDs or Dual Index values within the same Note batch.

### Species gate

Normalize the value and accept only an exact case-insensitive `pig`. Missing, unknown, human, mouse, or other values are errors and never reach fastp or IgBLAST.

### Chain normalization

Use a strict alias table:

```text
ab / AB / αβ          -> TRA,TRB
TCRα / TCRA / TRA     -> TRA
TCRβ / TCRB / TRB     -> TRB
```

Unknown or empty values are errors. Do not default to TRA/TRB and do not interpret a bare `B` as beta.

### Filename parsing

Parse R1/R2 first, then extract the delimiter-bounded Dual Index immediately before the lane/read suffix. The target form is:

```text
..._A01_L01_R1.fq.gz
```

Use a strict equivalent of:

```regex
(?:^|[_-])(?P<dual_index>[A-H]\d{2})(?=_(?:L\d{2}_)?R[12](?:_001)?\.(?:fq|fastq)\.gz$)
```

R1 and R2 must produce the same pair key and Dual Index. Missing mates, duplicate mates, multiple candidate indexes, or an index absent from the submission are errors. Note is a hard batch-path boundary using normalized whole path components, not fuzzy substring matching.

### Manifest contract

Write one row per physical FASTQ pair to `0.match/sample_manifest.csv`:

```text
submission_row,sample_id,dual_index,species,chain_raw,igblast_chains,note,
pair_id,raw_relative_dir,r1_relative_path,r2_relative_path,r1_size,r2_size,
match_method,status,error
```

Store relative raw paths after verifying their resolved paths remain under the configured raw root. Write the manifest atomically and add a JSON run summary with workbook/raw/config hashes and OK/ERROR counts. Every downstream script consumes only `status=OK` manifest rows and never rediscovers sample identity independently.

Default to strict mode: write the complete error report, exit nonzero, and do not start fastp if any matching error exists. An explicit partial mode may process valid pairs but the overall run must still return nonzero.

## 3. fastp, clean header, and PANDAseq

Refactor the current scripts using the hardened 10X runner patterns while keeping this workflow non-10X.

- fastp reads pairs from the manifest, not from an unrestricted recursive scan.
- Output each pair under `<raw-relative-parent>/<sample_id>/<pair_id>/` so multiple lanes never overwrite one another.
- Put FASTQ payloads in `02.fastp/data` and HTML/JSON/CSV reports in `02.fastp/report`.
- Produce a consolidated fastp CSV from JSON with before/after reads and percentages.
- Clean headers into `03.clean_data`, preserving the same hierarchy and canonical paired IDs acceptable to PANDAseq.
- PANDAseq writes per-pair merged/unaligned FASTA and logs under `04.pandaseq`, then deterministically aggregates multiple successful pairs for one sample before IgBLAST.
- Calculate PANDAseq counts from actual FASTA headers rather than relying only on uncertain `STAT` log lines.
- Use temporary outputs followed by atomic rename; never publish partial gzip, FASTA, JSON, or summary files.

## 4. Sample-specific pig IgBLAST

Refactor the IgBLAST stage into `05.work_igblastn.py`.

- Read `sample_id -> igblast_chains` from the manifest.
- Schedule exactly the required sample-chain jobs. For the current workbook this is six tasks: two chains each for AB1_0618 and AB2_0618, TRA only for A_0618, and TRB only for B_0618.
- Fix species to pig and Ig sequence type to TCR; remove general human/mouse/BCR defaults from this branch.
- Preserve source-relative sample paths in output directories.
- Produce per-sample `TCR.tsv`, chain-aware run summaries, and no cross-sample aggregate.
- Add an explicit chain column while merging TRA/TRB rows.
- Validate output headers and query counts; empty or malformed TSVs are failures, not completion markers.

### Database preflight

Build absolute database and auxiliary paths from the configured DB root. Before processing:

- verify `igblastn -version` and `blastdbcmd`;
- verify non-empty `pig.aux`;
- validate each requested database prefix with `blastdbcmd -db <prefix> -info`;
- require TRA V/J and TRB V/D/J;
- pass TRA D only if a validated database actually exists;
- pass C-region databases only when configured and validated;
- record the exact database prefixes in provenance and marker files.

The current repository only contains path assumptions for `database_251117/pig_new`; the database entities must be verified on the Linux server before production execution.

## 5. Restart, cleanup, progress, and stopping

Add per-pair/per-sample stage markers containing:

```text
status,stage_version,manifest_sha256,config_sha256,input_fingerprint,
output_fingerprint,exit_code,timestamp
```

A stage is skipped only when the marker is DONE, hashes match, required outputs exist, and outputs pass structural validation. Input or Chain changes invalidate only the affected sample and its downstream work. Failed or interrupted tasks never write DONE.

`run_pig_pipeline.sh` must:

- use `set -euo pipefail`;
- write one PID file per invocation under `.pids/`, allowing independent runs;
- run preflight and the five stages in order;
- display sample/task progress and resource usage;
- retain all intermediate data after failure or interruption;
- write `.pipeline.DONE` with manifest and final-summary hashes only after full success;
- clean large intermediates only after full success and only when explicitly enabled.

`stop.sh` must read and validate all invocation PID files, recursively terminate only those process trees using TERM then KILL, and refuse to use global `pgrep -f igblastn` matching.

## 6. Summaries

Generate:

- `match_summary.csv/json`;
- `fastp_summary.csv`;
- `pandaseq_summary.csv`;
- `igblast_run_summary.tsv`;
- `pipeline_summary.tsv/json`.

The final summary should contain one row per sample-chain with sample ID, Dual Index, raw Chain, normalized chain, pair count, raw/filtered/merged/query/matched counts, rates, stage statuses, elapsed time, peak RSS, output path, and error text. Build concurrent summaries from task fragments and merge atomically rather than appending from background jobs.

## 7. Verification

1. Unit-test header normalization, merged Note fill-down, species gating, chain aliases, strict Dual Index parsing, R1/R2 pairing, ambiguous/missing indexes, and path containment.
2. Confirm the real workbook resolves A02/A03/A05/A06 to the four expected samples and six expected sample-chain jobs.
3. Confirm the example A01 pair is recorded as ERROR and blocks strict execution.
4. Use tiny gzipped FASTQ fixtures and fake fastp/PANDAseq/IgBLAST executables for an end-to-end directory, routing, summary, and failure-policy test.
5. Test multiple lanes for one sample without overwrite and deterministic FASTA aggregation.
6. Test restart behavior after unchanged inputs, one changed FASTQ, one changed Chain, missing/truncated outputs, failure, and SIGTERM.
7. Verify CPU/memory concurrency caps and confirm `stop.sh` leaves unrelated IgBLAST processes untouched.
8. On the server, run database preflight and a 1-10 sequence TRA/TRB smoke test before the first production dataset.
