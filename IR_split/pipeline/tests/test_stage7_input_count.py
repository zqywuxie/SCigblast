"""Exercise the real embedded postprocessors without requiring IgBLAST."""
import contextlib
import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
PIPELINES = ("IR_split", "10X_split")
HEADER = "sequence_id\tproductive\tv_call\n"
AIRR = HEADER + "read1\tT\tTRAV1\nread2\tF\tTRAV2\nread3\tT\t*\n"
FASTA = ">read1\nACGT\n>read2\nTGCA\n>read3\nAAAA\n"


def postprocess_source(pipeline):
    source = (ROOT / pipeline / "pipeline/07.work_igblastn.sh").read_text(encoding="utf-8")
    command = '"${OUTPUT_DIR}/postprocess_summary.tsv" <<\'PY\'\n'
    return source.split(command, 1)[1].split("\nPY\n", 1)[0]


def execute(pipeline, batches, output, *, definitions_only=False):
    source = postprocess_source(pipeline)
    if definitions_only:
        source = source.split("files=sorted(root.rglob", 1)[0]
    namespace = {}
    opened = []
    original_open = Path.open

    def observe(path, *args, **kwargs):
        if path.suffix == ".fasta":
            opened.append(path.resolve())
        return original_open(path, *args, **kwargs)

    args = ["postprocess", str(batches), str(output), "representative", str(output / "postprocess_summary.tsv")]
    with patch.object(sys, "argv", args), patch.object(Path, "open", observe):
        exec(compile(source, f"{pipeline}/postprocess", "exec"), namespace)
    return namespace, opened


def fixture(root, *, symlinks=False, fasta=FASTA, airr=AIRR):
    batches, output = root / "batches", root / "output"
    output.mkdir()
    sample = batches / "batchA/sample"
    sample.mkdir(parents=True)
    canonical = sample / "batch_0.fasta"
    if fasta is not None:
        canonical.write_text(fasta, encoding="utf-8")
    (sample / "batch_0.map").write_text("read1\tCELL1\nread2\tCELL2\nread3\tCELL3\n", encoding="utf-8")
    for chain in ("TRA", "TRB", "TRD", "TRG"):
        folder = sample / chain
        folder.mkdir()
        (folder / "batch_0.tsv").write_text(airr, encoding="utf-8")
        if symlinks:
            (folder / "batch_0.fasta").symlink_to(canonical)
    return batches, output, canonical


class Stage7InputCountTests(unittest.TestCase):
    def assert_published(self, pipeline, output, expected_input=3):
        result = output / "batchA/sample/TCR.tsv"
        header = HEADER if pipeline == "IR_split" else HEADER.replace("sequence_id", "barcode")
        row = "read1\tT\tTRAV1\n" if pipeline == "IR_split" else "CELL1\tT\tTRAV1\n"
        self.assertEqual(result.read_bytes(), (header + row * 4).encode())
        with (output / "chain_summary.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["chain"] for r in rows], ["TRA", "TRB", "TRD", "TRG"])
        for row in rows:
            self.assertEqual(row["input_sequences"], str(expected_input))
            self.assertEqual(row["raw_rows"], "3")
            self.assertEqual(row["output_rows"], "1")
            self.assertEqual(row["status"], "OK")

    def test_legacy_shared_fasta_is_read_once_and_resume_recounts(self):
        for pipeline in PIPELINES:
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as tmp:
                batches, output, canonical = fixture(Path(tmp))
                _, opened = execute(pipeline, batches, output)
                self.assertEqual(opened, [canonical.resolve()])
                self.assert_published(pipeline, output)
                before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
                _, reopened = execute(pipeline, batches, output)
                self.assertEqual(reopened, [canonical.resolve()])
                after = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
                self.assertEqual(after, before)

    def test_chain_local_symlinks_reuse_actual_batch_count(self):
        for pipeline in PIPELINES:
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as tmp:
                try:
                    batches, output, canonical = fixture(Path(tmp), symlinks=True)
                except OSError as exc:
                    self.skipTest(f"symlink creation is unavailable: {exc}")
                _, opened = execute(pipeline, batches, output)
                self.assertEqual(opened, [canonical.resolve()])
                self.assert_published(pipeline, output)

    def test_missing_and_zero_fasta_keep_raw_row_fallback(self):
        for pipeline in PIPELINES:
            for fasta in (None, ""):
                with self.subTest(pipeline=pipeline, fasta=fasta), tempfile.TemporaryDirectory() as tmp:
                    batches, output, canonical = fixture(Path(tmp), fasta=fasta)
                    _, opened = execute(pipeline, batches, output)
                    self.assertEqual(opened, [] if fasta is None else [canonical.resolve()])
                    self.assert_published(pipeline, output)

    def test_empty_airr_keeps_empty_outputs_and_zero_counts(self):
        for pipeline in PIPELINES:
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as tmp:
                batches, output, _ = fixture(Path(tmp), fasta="", airr=HEADER)
                execute(pipeline, batches, output)
                self.assertFalse((output / "batchA/sample/TCR.tsv").exists())
                self.assertTrue((output / "batchA/sample/.NO_RESULTS").is_file())
                with (output / "chain_summary.csv").open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual([(r["input_sequences"], r["status"]) for r in rows], [("0", "EMPTY")] * 4)

    def test_cache_invalidates_changes_and_does_not_cache_read_failures(self):
        for pipeline in PIPELINES:
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as tmp:
                batches, output, canonical = fixture(Path(tmp))
                namespace, _ = execute(pipeline, batches, output, definitions_only=True)
                count = namespace["count_fasta"]
                with patch.object(Path, "open", side_effect=OSError("unreadable")):
                    self.assertEqual(count(canonical), 0)
                self.assertEqual(count(canonical), 3)
                prior = canonical.stat()
                # Same-size replacement changes the count, so size alone is insufficient.
                canonical.write_text(FASTA.replace(">read3", ";read3"), encoding="utf-8")
                os.utime(canonical, ns=(prior.st_atime_ns, prior.st_mtime_ns + 1_000_000_000))
                self.assertEqual(count(canonical), 2)
                canonical.unlink()
                self.assertEqual(count(canonical), 0)

    def test_malformed_airr_does_not_replace_existing_result(self):
        for pipeline in PIPELINES:
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as tmp:
                batches, output, _ = fixture(Path(tmp), airr="wrong\theader\n")
                previous = output / "batchA/sample/TCR.tsv"
                previous.parent.mkdir(parents=True)
                previous.write_bytes(b"previous output\n")
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    execute(pipeline, batches, output)
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(previous.read_bytes(), b"previous output\n")


if __name__ == "__main__":
    unittest.main()
