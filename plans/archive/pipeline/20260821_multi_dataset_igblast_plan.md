# Multi-dataset and IgBLAST post-processing plan

## Target behavior

- Each branch uses its own configuration names, and dataset input is always
  represented by arrays. An array of length one is the single-dataset case;
  longer arrays are multi-dataset runs.
- Dataset output names are derived from each input directory basename; no
  dataset-ID array is required. Duplicate basenames receive a stable short
  hash suffix.
- Raw IgBLAST output is temporary only and is removed after successful
  post-processing; it is not published under `igblast_out/raw/...`.
- Derived sample output keeps the existing `productive` and valid `v_call` filters.
- IR and 10X replace the derived output `sequence_id` with barcode.
- Chains are merged only within one physical sample:
  - `TCR.tsv`: TRA, TRB, TRD, TRG
  - `BCR.tsv`: IGH, IGK, IGL
- Derived TSVs contain only the original AIRR columns: no added `cell_type`/`chain` columns and no injected `#` comments.
- No cross-sample result is created.

## 1. Stable multi-dataset configuration

Modify:

- `IR_split/pipeline/pipeline_config.env`
- `10X_split/pipeline/pipeline_config.env`
- `PigIgblast/pipeline/pipeline_config.env`
- `Igblast_base/pipeline/pipeline_config.env`
- the four corresponding `run_*_pipeline.sh` files

Use arrays (without scalar single-dataset variables or a separate dataset-ID array):

```bash
SCIGBLAST_DATA_INPUTS=("/raw/20240829" "/raw/Lane03")
SCIGBLAST_SUBMISSIONS=("/ref/20240829.xlsx" "/ref/Lane03.xlsx")
SCIGBLAST_BARCODE_CSVS=("/ref/8bp_barcodes.csv")
```

Pig/base may retain their shorter aliases, but each runner must normalize them to the same internal arrays.

Rules:

- Remove semicolon-string parsing.
- Inputs must be non-empty and filesystem-valid.
- Submission/barcode arrays may contain one shared entry or one entry per dataset.
- Validate every input directory and reference file before starting any child pipeline.
- Output is `output/<input-basename>/...`; duplicate basenames get a stable
  hash suffix and the ordinal appears only in progress logs.
- The progress log records the derived output name, input, submission, and
  barcode reference for each child run.
- The first batch invocation runs match for every dataset and then exits for review. The second invocation continues every reviewed dataset.
- Dataset state and stage markers remain under `output/<dataset_id>/.pipeline_state`.

## 2. IR and 10X IgBLAST restoration

Modify:

- `IR_split/pipeline/07.work_igblastn.sh`
- `10X_split/pipeline/07.work_igblastn.sh`
- preferably add a branch-local `igblast_postprocess.py` to each pipeline
- `IR_split/pipeline/run_ir_pipeline.sh`
- `10X_split/pipeline/run_10x_pipeline.sh`

For each IgBLAST task:

1. Run IgBLAST into a temporary batch file; do not publish the raw outfmt-19
   file as a final result.
2. Parse columns by header name, not column position.
3. Retain rows where `productive` is `T` or `TRUE` and `v_call` is non-empty and not one of `*`, `-`, `NA`, `N/A`, `NONE`, `NULL`, `NO_HIT`, `UNMAPPED`, `NOT_FOUND`.
4. Resolve every retained `sequence_id` through the batch representative map and replace it with barcode. A missing map entry fails that sample publication instead of leaking a representative ID.
5. Merge filtered chain batches only inside the same full sample key (`raw-relative-parent + pair-stem + sample-id`).
6. Write one header and all retained records without adding columns, comments, or deduplicating equal sequence IDs.
7. Publish `TCR.tsv` and/or `BCR.tsv` beside the sample directory; never publish global aggregates.

Important semantic invariant: multiple UMIs under one barcode can yield repeated barcode values in `sequence_id`. Downstream code must not treat that field as a unique row key.

Also fix 10X chain selection to use the full sample key rather than sample ID alone, and align `10X_split/pipeline/01.match_sample_barcode.py` with IR's multi-candidate Dual Index behavior.

## 3. Pig and base IgBLAST behavior

Modify:

- `PigIgblast/pipeline/05.work_igblastn.py`
- `PigIgblast/pipeline/run_pig_pipeline.sh`
- `Igblast_base/pipeline/run_igblast_base.sh`
- base IgBLAST wrapper/config as needed

Pig/base retain original `sequence_id` because they have no representative barcode map. They otherwise use the same raw/derived split, filters, within-sample TCR/BCR merge, no extra columns/comments, and no cross-sample merge.

Pig should continue downstream for manifest `OK` samples even when other match rows are errors; failed mappings remain pending for a later submission update.

## 4. Summaries and resume safety

Keep statistics in separate summary files, never in sequence TSV columns. Define:

- `input_sequences`: FASTA records sent to the task
- `raw_rows`: non-comment IgBLAST result rows
- `mapped_rows`: rows with valid `v_call`
- `productive_rows`: productive rows
- `output_rows`: rows satisfying both filters
- `mapping_percent = mapped_rows / input_sequences`
- `retained_percent = output_rows / input_sequences`

Add a post-processing schema version. Separate raw-task fingerprints from derived-output fingerprints so a filter change reuses valid raw IgBLAST files. Fingerprints must include the current dataset identity, mapping summary, representative inventory/map, database/chain settings, and post-processing version.

Final DONE validation must enumerate every expected full sample key and require either its expected TCR/BCR file or an explicit zero-result marker. It must not succeed because one arbitrary TSV exists.

## 5. Pipeline logic to document after implementation

- IR: match → matched-only fastp → IR barcode/UMI split + sidecar → clean → PANDAseq → barcode+UMI representative → chain-specific IgBLAST → raw preservation → filter → barcode sequence ID → within-sample TCR/BCR.
- 10X: match → matched-only fastp → R1 TSO/barcode/UMI and R2 primer prefilter → clean → PANDAseq → barcode+UMI representative → chain-specific IgBLAST → raw preservation → filter → barcode sequence ID → within-sample TCR/BCR.
- Pig: Note+Dual Index+Species match → matched-only fastp → clean → PANDAseq → requested pig-chain IgBLAST → raw preservation → filter → within-sample TCR/BCR.
- Base: match → matched-only fastp → clean → PANDAseq → requested-chain IgBLAST → filter → within-sample TCR/BCR.

## 6. Known failure points to report after implementation

- IR PANDAseq IDs may fail to join the UMI sidecar after header normalization.
- Barcode replacement intentionally creates duplicate `sequence_id` values across UMIs.
- Same sample IDs across lanes require full-key chain lookup.
- Strict fixed-position TSO matching can discard valid 10X reads; monitor prefilter summaries.
- FASTQ pair/header mismatches must fail only the affected sample.
- Submission changes must invalidate affected sample state without rerunning completed unrelated samples.
- Pig database V/J/D completeness and auxiliary-file compatibility remain external reference risks.
- Array elements, duplicate basenames, missing paths, and reordered datasets must not silently reuse another dataset's outputs.

## Verification

- Run `bash -n` on all four runners and modified shell scripts.
- Run `python -m py_compile` on all modified/new Python files.
- Verify raw IgBLAST files are absent after successful post-processing.
- Verify false/nonproductive and invalid-v-call rows are absent from derived output.
- Verify IR/10X barcode replacement and failure on missing representative map.
- Verify derived headers exactly match AIRR headers and contain no injected comments/columns.
- Verify TCR/BCR merging stays inside a single full sample key and never crosses samples.
- Verify repeated barcode sequence IDs are retained, not deduplicated.
- Verify duplicate basenames receive distinct stable suffixes and missing paths fail before child startup.
- Reorder arrays and confirm the same derived basename/hash output state is reused.
- Correct one previous mapping error and confirm only the newly valid sample proceeds.
