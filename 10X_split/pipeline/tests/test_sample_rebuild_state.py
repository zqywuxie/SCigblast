"""Regression tests for replacing complete 10X sample snapshots."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "split_represent", Path(__file__).resolve().parents[1] / "06.split_and_represent.py"
)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class SampleRebuildStateTests(unittest.TestCase):
    def row(self, sample, barcode, umi):
        return {"sample": sample, "barcode": barcode, "umi": umi,
                "sequence": "ACGT", "header": f"read-{umi}",
                "is_representative": "Yes", "error": ""}

    def test_complete_rebuild_removes_disappeared_umis_and_barcodes(self):
        old = [self.row("batch/S", "BC1", "U1"),
               self.row("batch/S", "BC1", "U2"),
               self.row("batch/S", "BC2", "U3"),
               self.row("other/S", "BC9", "U9")]
        new = [self.row("batch/S", "BC1", "U1")]
        merged = stage.replace_umi_samples(old, new, {"batch/S"})
        self.assertEqual([(r["sample"], r["barcode"], r["umi"]) for r in merged],
                         [("other/S", "BC9", "U9"), ("batch/S", "BC1", "U1")])
        self.assertEqual(stage.replace_umi_samples(old, [], {"batch/S"}), [old[-1]])

    def test_phase1_fingerprint_tracks_input_and_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sample"
            root.mkdir()
            marker = root / ".phase1.DONE"
            marker.write_text(json.dumps({"status": "DONE", "input": "/data/sample.fa",
                                          "size": 10, "mtime_ns": 100,
                                          "config_fingerprint": "a"}), encoding="utf-8")
            before = stage.phase1_input_fingerprints(tmp)
            marker.write_text(json.dumps({"status": "DONE", "input": "/data/sample.fa",
                                          "size": 11, "mtime_ns": 101,
                                          "config_fingerprint": "a"}), encoding="utf-8")
            after = stage.phase1_input_fingerprints(tmp)
            self.assertNotEqual(before, after)

    def test_phase1_checkpoint_rejects_missing_uncommitted_barcode_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.fa"
            source.write_text(">r1\nACGT\n", encoding="utf-8")
            sample_out = root / "sample"
            tsv_dir = sample_out / stage.BARCODE_TSV_DIRNAME
            tsv_dir.mkdir(parents=True)
            (sample_out / stage.SUMMARY_CSV).write_text("sample\n", encoding="utf-8")
            (sample_out / stage.RUN_LOG_TXT).write_text("complete\n", encoding="utf-8")
            (tsv_dir / "BC1.tsv").write_text("U1\tr1\tACGT\n", encoding="utf-8")
            stat = source.stat()
            names_hash = hashlib.sha256(b"BC1.tsv").hexdigest()
            marker = sample_out / ".phase1.DONE"
            marker.write_text(json.dumps({
                "status": "DONE", "input": str(source.resolve()),
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "config_fingerprint": stage.CONFIG_FINGERPRINT,
                "payload_tsv_count": 1, "payload_tsv_names_sha256": names_hash,
            }), encoding="utf-8")

            self.assertTrue(stage.phase1_checkpoint_valid(marker, source, sample_out, root, {}))
            (tsv_dir / "BC1.tsv").unlink()
            self.assertFalse(stage.phase1_checkpoint_valid(marker, source, sample_out, root, {}))

    def test_phase1_checkpoint_allows_payload_cleanup_after_reducer_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.fa"
            source.write_text(">r1\nACGT\n", encoding="utf-8")
            sample_out = root / "sample"
            sample_out.mkdir()
            (sample_out / stage.SUMMARY_CSV).write_text("sample\n", encoding="utf-8")
            (sample_out / stage.RUN_LOG_TXT).write_text("complete\n", encoding="utf-8")
            stat = source.stat()
            marker_data = {
                "status": "DONE", "input": str(source.resolve()),
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "config_fingerprint": stage.CONFIG_FINGERPRINT,
                "payload_tsv_count": 0,
                "payload_tsv_names_sha256": hashlib.sha256(b"").hexdigest(),
            }
            marker = sample_out / ".phase1.DONE"
            marker.write_text(json.dumps(marker_data), encoding="utf-8")
            committed = {"sample": stage._phase1_marker_fingerprint(marker_data)}

            self.assertTrue(stage.phase1_checkpoint_valid(marker, source, sample_out, root, committed))


if __name__ == "__main__":
    unittest.main()
