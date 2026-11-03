# Four-pipeline architecture optimization plan

## 1. Scope and frozen contracts

This change covers four independent pipelines:

- `10X_split/pipeline`
- `IR_split/pipeline`
- `PigIgblast/pipeline`
- `Igblast_base/pipeline`

The pipelines continue to maintain their own scripts; no shared `common pipeline` is introduced.

The following calculation code is frozen and must not be changed in this work:

- `IR_split/pipeline/analysis/models/*`
- the corresponding preprocessing/expression/diversity/CSR/SHM model definitions

Allowed changes are limited to orchestration, input discovery, identity propagation, output paths, manifests, checkpointing, progress, resource control, error handling, and the 10X-specific cluster stage.

Biological identity contracts:

- 10X: `barcode` is a cell identity; UMI is a molecule identity within that cell.
- IR: barcode is a sample-demultiplexing key, not a cell identity; representative selection is within `dataset/library + sample_id + UMI`.
- Pig and Base: no barcode/UMI clustering stage.

## 2. Target pipeline graphs

### 2.1 10X

```text
01.match
→ 02.fastp
→ 03.prefilter (R1 TSO + R2 sample-primer barcode)
→ 04.clean
→ 05.pandaseq
→ 06.split_and_represent (one representative per barcode + UMI)
→ 07.igblast
→ 08.cluster (cell/chain clustering)
```

- Do not call the existing downstream preprocessing/models pipeline.
- Integrate `08.chain_cluster.py` into `run_10x_pipeline.sh`; it is currently standalone and has hard-coded Windows paths.
- Keep the public filtered IgBLAST output separate from cluster output; Stage 8 writes to `08.cluster/<dataset_id>` and never overwrites Stage 7.

The 10X cluster has two explicit levels:

1. Molecule-level deduplication:

   ```text
   dataset_id + sample_id + barcode + UMI + locus + normalized V + normalized J + CDR3
   ```

   Resolve duplicate annotations for the same molecule deterministically by alignment coverage, V score, V identity, then stable source order. Record conflicting/tied annotations in an audit summary.

2. Cell-chain candidate clustering:

   ```text
   dataset_id + sample_id + barcode + locus + normalized V + normalized J + CDR3
   ```

   Aggregate distinct supporting UMIs, choose the representative sequence by UMI support and then the same deterministic quality ordering, and apply the existing per-cell/per-locus candidate-retention rules. UMI is therefore retained at molecule level but is not incorrectly made part of the clonotype key.

Stage 7 currently replaces representative IDs with barcode values, which destroys the join key needed to recover UMI. Fix this without changing the public IgBLAST table by producing a compressed internal cluster contract beside Stage 7:

```text
.cluster_input/<sample>/<chain>.tsv.gz
representative_id,barcode,umi,locus,v_call,j_call,cdr3,...AIRR fields...
```

Build it by joining the pre-replacement IgBLAST row to `representative_map.tsv.gz`. Stage 8 consumes only this internal contract. Its fingerprint and DONE marker make it resumable; it can be removed only after Stage 8 succeeds and only when explicit cleanup is enabled.

### 2.2 IR bulk

```text
01.match
→ 02.fastp
→ 03.IR_split/presplit registration
→ 04.clean
→ 05.pandaseq
→ 06.representative (dataset/library + sample_id + UMI)
→ 07.igblast
→ 08.analysis/preprocessing
```

- Keep the current preprocessing `umi_counts` behavior and all model calculations unchanged.
- Change only `analysis/main.py`, `analysis/run_analysis.sh`, and runner wiring when needed for dataset-scoped paths, manifests, recovery, and error reporting.
- The `representative` variant runs Stage 8.
- The `merged` variant has no valid one-record-per-UMI contract and therefore must mark analysis as `NOT_APPLICABLE`, not silently manufacture UMI counts. If merged-mode preprocessing is desired later, it requires a separately specified biological counting contract.
- Keep `dataset/library + sample_id + UMI` as the representative key so identical sample IDs in different datasets never merge.

### 2.3 Pig

```text
01.match → review → database preflight → 02.fastp → 03.clean → 04.pandaseq → 05.igblast
```

- Preserve Pig species/Chain selection and Pig database behavior.
- Do not add 10X clustering or IR preprocessing.
- Move database preflight after the first match/review gate so database problems do not prevent sample mapping review.

### 2.4 IgBLAST Base

```text
01.match → 02.fastp → 03.clean → 04.pandaseq → 05.igblast
```

- Preserve Chain metadata in the match manifest; barcode fields remain present but empty if the common manifest schema is retained.
- Do not add split, representative, clustering, or IR preprocessing.

## 3. Unified stage-first output layout

All four runners use one root plus an explicit dataset label. They must not create `OUTPUT_ROOT/<dataset>/01.match/...`.

```text
output/
├── dataset_manifest.csv
├── 01.match/<dataset_id>/...
├── 02.fastp/<dataset_id>/{data,report}/...
├── 03.<pipeline-specific>/<dataset_id>/...
├── ...
├── <final-stage>/<dataset_id>/...
├── logs/<dataset_id>/pipeline.log
└── .pipeline_state/<dataset_id>/...
```

Pipeline-specific endings:

- 10X: `07.igblastn_out/<dataset_id>` and `08.cluster/<dataset_id>`
- IR: `07.igblastn_out/<dataset_id>` and `08.analysis/<dataset_id>`
- Pig/Base: `05.igblastn_out/<dataset_id>`

Dataset IDs are derived from the input-directory basename after sanitization. Duplicate basenames receive a stable short path hash. Record the original absolute input path and resolved label in `dataset_manifest.csv`.

The parent batch runner passes both:

```text
SCIGBLAST_OUTPUT_ROOT=<shared output root>
SCIGBLAST_DATASET_LABEL=<resolved dataset id>
```

Each child uses a local `stage_root(stage)` helper. Submission directories remain shared pools; match scripts select workbook rows by Note/path rather than positional input/submission pairing.

## 4. Critical file changes

### 4.1 10X

- `10X_split/pipeline/run_10x_pipeline.sh`
  - replace per-dataset output-root spawning with shared root + dataset label;
  - define stage roots, dataset-scoped logs/state, Stage 8 invocation and completion checks;
  - include Stage 8 and the Stage 7 cluster-input fingerprint in pipeline completion.
- `10X_split/pipeline/00.pipeline_config.env`
  - add cluster enable/workers/memory/chunk parameters; remove any automatic preprocessing entry.
- `10X_split/pipeline/01.match_sample.py` through `07.work_igblastn.sh`
  - consume explicit stage paths or `SCIGBLAST_DATASET_LABEL`, never infer a dataset from `OUTPUT_ROOT`;
  - Stage 7 emits the internal molecule annotation contract before barcode replacement.
- `10X_split/pipeline/06.split_and_represent.py`
  - guarantee map rows include representative ID, dataset/sample, barcode and UMI;
  - keep existing representative selection semantics and ambiguity recording.
- `10X_split/pipeline/08.chain_cluster.py`
  - replace hard-coded paths with environment/config inputs;
  - stream/chunk by sample or barcode partition instead of concatenating the entire dataset in memory;
  - implement the two-level key contract and atomic per-sample outputs/summary.
- `10X_split/pipeline/stop.sh`
  - discover the new dataset-scoped PID registry and Stage 8 workers.

### 4.2 IR

- `IR_split/pipeline/run_ir_pipeline.sh`
  - retain the existing stage-first structure;
  - make the representative/merged analysis boundary explicit;
  - pass dataset-scoped Stage 7 and Stage 8 paths and validate their manifests.
- `IR_split/pipeline/06.representative.py`
  - ensure the key includes dataset/library + sample + UMI and consume only active-manifest inputs;
  - preserve calculation/selection semantics.
- `IR_split/pipeline/analysis/main.py` and `analysis/run_analysis.sh`
  - path/discovery/checkpoint changes only; do not edit model formulas.
- `IR_split/pipeline/analysis/models/*`
  - no changes.

### 4.3 Pig

- `PigIgblast/pipeline/run_pig_pipeline.sh`
- `PigIgblast/pipeline/00.pipeline_config.env`
- `PigIgblast/pipeline/01.match_sample.py`
- `PigIgblast/pipeline/02.run_fastp.sh`
- `PigIgblast/pipeline/03.clean_header.py`
- `PigIgblast/pipeline/04.work_pandaseq.sh`
- `PigIgblast/pipeline/05.work_igblastn.py`
- `PigIgblast/pipeline/stop.sh`

Apply dataset-label path propagation, stage-first roots, dataset-scoped logs/state, pipeline-stage markers, and database-preflight ordering without changing Pig IgBLAST calculation/database rules.

### 4.4 Base

- `Igblast_base/pipeline/run_igblast_base.sh`
- `Igblast_base/pipeline/00.pipeline_config.env`
- `Igblast_base/pipeline/01.match_sample.py`
- `Igblast_base/pipeline/02.run_fastp.sh`
- `Igblast_base/pipeline/03.clean_header.sh`
- `Igblast_base/pipeline/04.work_pandaseq.sh`
- `Igblast_base/pipeline/05.work_igblastn.sh`
- `Igblast_base/pipeline/stop.sh`

Apply the same dataset-label and stage-first path contract while retaining Base's existing chain mapping and IgBLAST behavior.

## 5. Manifest, resume, and failure contract

Create one dataset manifest at the output root with at least:

```text
dataset_id,input_root,submission_sources,pipeline,input_mode,
match_dir,fastp_dir,...,final_dir,status,error
```

Each stage writes a dataset-scoped stage manifest containing:

```text
task_id,dataset_id,sample_id,source_pair,chain,input_paths,output_paths,
input_fingerprint,config_fingerprint,script_fingerprint,status,error,updated_at
```

Rules:

- Downstream stages consume only `status=OK/DONE` rows from the preceding manifest; no broad recursive discovery of stale output.
- A DONE marker is valid only when fingerprints match and required outputs/summaries are present and non-empty where expected.
- All temporary and marker files are unique per task/PID and published by atomic rename.
- Match ERROR rows remain recorded but do not enter downstream processing; valid rows continue.
- Within a stage, other samples continue after one sample fails. The stage ends non-zero, records `PARTIAL/FAILED`, and later stages do not start automatically unless the runner has an explicit, documented partial-continuation rule.
- Re-running processes only missing, failed, or fingerprint-changed tasks.
- Legacy dataset-first outputs are read-only migration sources. Do not mix them into a new stage-first run or silently treat old markers as valid; provide a dry-run migration report or use a new output root.

## 6. Concurrency and resource handling

- Keep datasets serial by default; parallelize samples/tasks inside a stage. This prevents four heavy datasets from each claiming the full 300 GB budget.
- Use per-stage controls for max jobs, threads/job and reserved GB/job.
- Compute admission as the minimum of configured jobs, thread budget, memory budget, and live `MemAvailable`.
- Below 150 GB available memory, stop dispatching new work and log `RESOURCE_PAUSED`; let active tasks finish. Use a lower emergency threshold only for terminating active process groups.
- Use `wait -n`/completion-driven scheduling so a slow early task does not block dispatch after other tasks finish.
- 10X Stage 8 should partition by sample/barcode and merge compact summaries, not load all IgBLAST rows into one DataFrame.
- Preserve per-task progress as `submitted/running/done/failed/skipped/total`, elapsed time and peak RSS.
- Each runner registers PID/PGID/run ID under `.pipeline_state/<dataset_id>/pids`; each local `stop.sh` reads this registry first and does not scan every server process repeatedly.

## 7. Verification plan

### Contract fixtures

Prepare four tiny datasets plus one duplicate-basename dataset pair:

- 10X: two cells, multiple UMIs, same V/J/CDR3 across multiple UMIs, one conflicting annotation for the same UMI, and one cell with more than two chain candidates.
- IR: two samples that share the same UMI value, plus repeated sample names in different datasets; verify no cross-sample/dataset merge.
- Pig: Pig and non-Pig submission rows with multiple Chain values and database preflight.
- Base: TCR-only, BCR-only, and mixed-chain samples.

### Assertions

1. All pipelines write stage-first directories and dataset-scoped logs/state.
2. Duplicate dataset basenames receive deterministic distinct labels.
3. 10X Stage 6 emits exactly one representative per barcode+UMI; Stage 8 molecule count equals distinct retained UMIs and does not merge cells or datasets.
4. 10X public IgBLAST output remains unchanged by Stage 8; cluster output and ambiguity summary are separate.
5. IR representative and Datapoint results match the pre-change golden outputs byte-for-byte/numerically for the same input, proving model definitions did not change.
6. IR merged mode reports analysis `NOT_APPLICABLE` rather than producing fabricated UMI metrics.
7. Pig and Base IgBLAST result contents match golden outputs; only paths/manifests differ.
8. Interrupt every stage after at least one task completes, rerun, and verify completed tasks skip while incomplete tasks resume.
9. Inject one sample failure: other samples in that stage complete, the stage returns non-zero, and the next stage does not start.
10. Run two pipelines concurrently and lower available memory in a controlled test; new task dispatch pauses below the threshold without deleting intermediates.
11. Run each local `stop.sh`; only that pipeline/run's registered process groups stop.

## 8. Recommended implementation order

1. Freeze small golden outputs and add path/manifest/identity fixtures.
2. Introduce the shared-root + dataset-label + stage-root contract in 10X, Pig and Base; keep IR as the reference implementation.
3. Update every affected stage script, manifest, marker, log and stop path; verify multi-input resume before changing the 10X biological handoff.
4. Extend 10X representative mapping and Stage 7 internal cluster input so barcode and UMI remain traceable.
5. Refactor and integrate 10X Stage 8 with two-level molecule/chain clustering, chunked execution and separate output.
6. Confirm IR Stage 8 wiring and representative-only boundary without modifying any analysis model calculation.
7. Add failure, restart, memory-pressure and stop tests, then test all four pipelines end to end on small fixtures.
