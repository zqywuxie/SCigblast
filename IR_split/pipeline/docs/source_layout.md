# Source-scoped IR outputs

Stage 6 retains the PANDAseq-relative sample directory. For example:

```
05.pandaseq/ir_raw/合寄_D01/原始文件名/LXH_P/*_merged.fastq
06.representative/ir_raw/representative_fasta/合寄_D01/原始文件名/LXH_P/representative.fasta
07.igblastn_out/ir_raw/合寄_D01/原始文件名/LXH_P/TCR.tsv
08.preprocessing/ir_raw/Datapoint.csv
```

`sample_id` remains `LXH_P`. Representative state/map/summary adds `sample_key`
(`合寄_D01/原始文件名/LXH_P`). UMI grouping and state lookup use this source-scoped
key. Identically named samples in different source directories never merge.
Datapoint `sample` is the leaf sample name and `batch` includes the dataset plus
the relative parent (`ir_raw/合寄_D01/原始文件名`). Model formulas remain unchanged.

On rerun, old flat representative outputs are renamed to a sibling
`*.legacy_flat.<timestamp>` directory. The runner also archives old IgBLAST and
preprocessing directories, then regenerates stages 6–8. Stages 2–5 can be reused
when their checkpoints remain valid. PANDAseq source data must still exist;
migration refuses when a previously recorded sample has no available source.
Backups are never automatically deleted or included as new preprocessing inputs.

10X already retains its relative source directories in representative FASTA,
IgBLAST, and stage-8 clustering. Its barcode+UMI grouping is unchanged.
