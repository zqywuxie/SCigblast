"""Exercise the output checker embedded in both Base/Pig launchers."""
import csv
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNERS = (
    ROOT / "Igblast_base/pipeline/run_igblast_base.sh",
    ROOT / "PigIgblast/pipeline/run_pig_pipeline.sh",
)


def artifact_checker_source(runner: Path) -> str:
    source = runner.read_text(encoding="utf-8")
    match = re.search(
        r"stage_artifacts_ready\(\)\s*\{.*?<<'PY'\s*\n(.*?)\nPY\s*\n\}",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"stage_artifacts_ready helper missing in {runner}")
    return match.group(1)


def pandaseq_checker_source(runner: Path) -> str:
    source = runner.read_text(encoding="utf-8")
    match = re.search(
        r"stage_pandaseq_ready\(\)\s*\{.*?<<'PY'\s*\n(.*?)\nPY\s*\n\}",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"stage_pandaseq_ready helper missing in {runner}")
    return match.group(1)


class StageArtifactValidationTests(unittest.TestCase):
    def run_checker(self, code: str, summary: Path, *specs: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", code, str(summary), ",", *specs],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_successful_summary_checks_every_sample_artifact(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner.parent.parent.name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                artifacts = [root / "A_R1.fq.gz", root / "A_R2.fq.gz", root / "B_R1.fq.gz", root / "B_R2.fq.gz"]
                for path in artifacts:
                    path.write_bytes(b"gzip-placeholder")
                summary = root / "summary.csv"
                with summary.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["sample", "status", "r1", "r2"])
                    writer.writeheader()
                    writer.writerow({"sample": "A", "status": "OK", "r1": artifacts[0], "r2": artifacts[1]})
                    writer.writerow({"sample": "B", "status": "OK", "r1": artifacts[2], "r2": artifacts[3]})
                code = artifact_checker_source(runner)
                self.assertEqual(self.run_checker(code, summary, "r1", "r2").returncode, 0)

                artifacts[3].unlink()
                self.assertNotEqual(self.run_checker(code, summary, "r1", "r2").returncode, 0)

    def test_zero_length_fastq_fails_but_empty_fasta_can_be_valid(self):
        code = artifact_checker_source(RUNNERS[0])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fastq = root / "empty.fq.gz"
            fastq.touch()
            summary = root / "summary.csv"
            with summary.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["status", "r1"])
                writer.writeheader()
                writer.writerow({"status": "OK", "r1": fastq})
            self.assertNotEqual(self.run_checker(code, summary, "r1").returncode, 0)

            fasta = root / "valid-empty.fasta"
            fasta.touch()
            with summary.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["status", "output"])
                writer.writeheader()
                writer.writerow({"status": "OK", "output": fasta})
            self.assertEqual(self.run_checker(code, summary, "exists:output").returncode, 0)

    def test_pandaseq_validates_each_sample_and_keeps_empty_fasta_valid(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner.parent.parent.name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                rows = []
                for sample in ("A", "B"):
                    out = root / sample
                    out.mkdir()
                    pair = f"pair_{sample}"
                    (out / f"{pair}_merged.fasta").write_bytes(b"")
                    (out / ".DONE").write_text(
                        f"status=DONE\nsample_id={sample}\npair_id={pair}\n", encoding="utf-8"
                    )
                    rows.append({"sample_id": sample, "pair_id": pair, "status": "OK"})
                summary = root / "pandaseq_summary.csv"
                with summary.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["sample_id", "pair_id", "status"])
                    writer.writeheader()
                    writer.writerows(rows)
                code = pandaseq_checker_source(runner)
                self.assertEqual(
                    subprocess.run(
                        [sys.executable, "-c", code, str(summary), str(root)],
                        capture_output=True,
                    ).returncode,
                    0,
                )
                (root / "B" / "pair_B_merged.fasta").unlink()
                self.assertNotEqual(
                    subprocess.run(
                        [sys.executable, "-c", code, str(summary), str(root)],
                        capture_output=True,
                    ).returncode,
                    0,
                )

    def test_fastp_summary_records_each_exact_output_pair(self):
        runner = ROOT / "Igblast_base/pipeline/02.run_fastp.sh"
        source = runner.read_text(encoding="utf-8")
        marker = '"${PYTHON_BIN}" - "$REPORT_DIR" "${REPORT_DIR}/fastp_summary.csv" "$OUTPUT_DIR" <<\'PY\'\n'
        self.assertIn(marker, source)
        code = source.split(marker, 1)[1].split("\nPY", 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reports = root / "report" / "sampleA"
            data = root / "data" / "sampleA"
            reports.mkdir(parents=True)
            data.mkdir(parents=True)
            (reports / "sampleA.json").write_text(json.dumps({
                "summary": {
                    "before_filtering": {"total_reads": 100, "total_bases": 500, "q20_rate": 0.8, "q30_rate": 0.6},
                    "after_filtering": {"total_reads": 90, "total_bases": 450, "q20_rate": 0.9, "q30_rate": 0.7},
                }
            }), encoding="utf-8")
            output = root / "fastp_summary.csv"
            result = subprocess.run(
                [sys.executable, "-c", code, str(root / "report"), str(output), str(root / "data")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with output.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(Path(row["r1_output"]), data / "sampleA_R1.fq.gz")
            self.assertEqual(Path(row["r2_output"]), data / "sampleA_R2.fq.gz")
            self.assertEqual(float(row["q30_rate_after"]), 0.7)
            self.assertEqual(float(row["q30_pct_before"]), 60.0)


if __name__ == "__main__":
    unittest.main()
