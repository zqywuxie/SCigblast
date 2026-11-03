# IR Stage 08 preprocessing implementation

## Frozen calculation boundary

`preprocess/models/` remains the source copy. The six model files are vendored
byte-for-byte into `IR_split/pipeline/models/`; no filtering threshold, formula,
grouping rule, or output field in those model files is changed.

## Pipeline integration

- `IR_split/pipeline/08.preprocessing.py` is the numbered post-IgBLAST stage.
- It discovers `TCR.tsv`/`BCR.tsv` below `07.igblastn_out`, supports TCR-only,
  BCR-only, and mixed samples, and writes `08.preprocessing/Datapoint.csv`,
  `processing_manifest.csv`, and `junction_peps/`.
- `IR_split/pipeline/analysis/main.py` and `analysis/run_analysis.sh` remain
  compatibility entry points and delegate to Stage 08.

## Adapter-only optimizations

`IR_split/pipeline/ir_preprocessing_adapter.py` owns representative-state
lookup and input handling. It streams state files into a compact SQLite alias
index, accepts the split/clean read-ID variants, validates sample membership,
and calls the frozen `AIRR_filter` unchanged. Per-sample checkpoints remain
atomic and are invalidated by input, state, model, adapter, and configuration
fingerprints.

The SHM model is also unchanged. A scoped worker-budget adapter prevents the
model's historical `cpu_count()-1` pool from expanding to hundreds of workers
on the shared host; `SCIGBLAST_IR_PREPROCESSING_SHM_WORKERS` and
`SCIGBLAST_IR_PREPROCESSING_TOTAL_PROCESS_BUDGET` control this limit.

## Validation

Python compilation and Bash syntax checks pass. A small TCR-only fixture was
run through Stage 08 successfully, including checkpoint and manifest output.
