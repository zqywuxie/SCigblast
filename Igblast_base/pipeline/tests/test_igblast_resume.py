"""Exercise real Base/Pig stage functions with a fake IgBLAST subprocess."""
import ast
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pipeline_igblast_cache as cache
from pipeline_parallel import FastaCountCache, bounded_completed
from pipeline_input_manifest import pig_output_relative


class StageFixture:
    def __init__(self, folder, mode, chains=("TRA",)):
        self.root = Path(folder)
        self.mode = mode
        self.chains = chains
        self.species = "pig" if mode == "pig" else "human"
        self.version = "pig_chain" if mode == "pig" else "human"
        self.source = ROOT / ("PigIgblast/pipeline/05.work_igblastn.py" if mode == "pig" else "Igblast_base/pipeline/05.work_igblastn.sh")
        code = self.source.read_text(encoding="utf-8")
        if mode == "base":
            code = code.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        functions = ast.Module(body=[node for node in ast.parse(code).body if isinstance(node, ast.FunctionDef)], type_ignores=[])
        self.ns = dict(Path=Path, os=os, csv=csv, re=re, shutil=shutil, subprocess=subprocess,
                       sys=sys, tempfile=tempfile, hashlib=hashlib, ThreadPoolExecutor=ThreadPoolExecutor,
                       bounded_completed=bounded_completed, FastaCountCache=FastaCountCache,
                       pig_output_relative=pig_output_relative,
                       FASTA_COUNTS=FastaCountCache(), dependency_snapshot=cache.dependency_snapshot,
                       task_fingerprint=cache.task_fingerprint, PIPELINE=self.source.parent,
                       HERE=self.source.parent, INPUT=self.root/"input", OUTPUT=self.root/"output",
                       MANIFEST=self.root/"manifest.csv", SUMMARY=self.root/"output/igblast_summary.tsv",
                       DB_ROOT=self.root/"db", VERSION=self.version, SPECIES=self.species,
                       RAW_ROOT=None, RAW_INPUT_ROOT=None, BIN=sys.executable, THREADS=2, MAX_JOBS=2,
                       REQUESTED=2, MEMORY_PER_JOB=1, DEPENDENCIES={}, TCR={"TRA", "TRB", "TRD", "TRG"},
                       SUPPORTED=cache.SUPPORTED, BAD_V={"", "*", "-", "NA", "N/A", "NONE", "NULL", "NO_HIT", "UNMAPPED", "NOT_FOUND"})
        exec(compile(functions, str(self.source), "exec"), self.ns)
        fasta = self.root/"input/pair/sample/pair_merged.fasta"
        fasta.parent.mkdir(parents=True)
        fasta.write_text(">read\nACGT\n", encoding="utf-8")
        with self.ns["MANIFEST"].open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["sample_id", "pair_id", "r1_path", "status", "igblast_chains"])
            writer.writeheader()
            writer.writerow(dict(sample_id="sample", pair_id="pair", r1_path="/raw/R1.fq", status="OK", igblast_chains=",".join(chains)))
        for chain in chains:
            for part in ("V", "D", "J"):
                db = self.index(chain, part)
                db.parent.mkdir(parents=True, exist_ok=True)
                db.write_bytes(b"index")
        aux = self.aux()
        aux.parent.mkdir(parents=True)
        aux.write_bytes(b"aux")
        self.failed = set()
        self.calls = []

    def aux(self):
        return self.root/"db/optional_file"/("pig.aux" if self.mode == "pig" else "human_gl.aux")

    def index(self, chain, part):
        return self.root/"db/database_251117"/self.version/chain/f"{self.species}_gl_{chain}_{part}.nin"

    def snapshot(self, **kwargs):
        values = dict(mode=self.mode, db_root=self.ns["DB_ROOT"], binary=self.ns["BIN"],
                      species=self.species, database_version=self.version, threads=self.ns["THREADS"],
                      chains=list(self.chains), implementation=self.source)
        values.update(kwargs)
        return cache.dependency_snapshot(**values)

    def fake_run(self, args, **kwargs):
        chain = Path(args[args.index("-germline_db_V")+1]).parent.name
        self.calls.append(chain)
        if chain in self.failed:
            return subprocess.CompletedProcess(args, 1)
        Path(args[args.index("-out")+1]).write_text(
            f"sequence_id\tv_call\tproductive\nread-{chain}\t{chain}V1\tT\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    def run(self):
        with patch.object(subprocess, "run", side_effect=self.fake_run), redirect_stdout(io.StringIO()):
            return self.ns["main"]()

    def summary(self):
        with (self.root/"output/chain_summary.csv").open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


class IgblastResumeTests(unittest.TestCase):
    def test_parallel_outputs_match_serial_and_fasta_is_scanned_once_per_run(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode, ("TRB", "TRA", "IGH"))
                fasta = fixture.root/"input/pair/sample/pair_merged.fasta"
                fasta.write_text(">read1\nACGT\n>read2\nACGT\n>read3\nACGT\n>read4\nACGT\n", encoding="utf-8")
                original_open = Path.open
                scans = []

                def track_open(path, *args, **kwargs):
                    if path == fasta and (not args or args[0] == "r"):
                        scans.append(path)
                    return original_open(path, *args, **kwargs)

                def fake_run(args, **kwargs):
                    chain = Path(args[args.index("-germline_db_V")+1]).parent.name
                    Path(args[args.index("-out")+1]).write_text(
                        "sequence_id\tv_call\tproductive\n"
                        f"read1-{chain}\t{chain}V1\tT\nread2-{chain}\t\tT\n"
                        f"read3-{chain}\t{chain}V1\tF\nread4-{chain}\t{chain}V2\tTRUE\n", encoding="utf-8")
                    return subprocess.CompletedProcess(args, 0)

                fixture.fake_run = fake_run
                snapshots = []
                with patch.dict(os.environ, {"SCIGBLAST_FORCE_RERUN": "1"}), patch.object(Path, "open", track_open):
                    for workers in (1, 3):
                        fixture.ns["MAX_JOBS"] = workers
                        self.assertEqual(fixture.run(), 0)
                        snapshots.append({str(p.relative_to(fixture.ns["OUTPUT"])): p.read_bytes()
                                          for p in fixture.ns["OUTPUT"].rglob("*") if p.suffix in {".csv", ".tsv"}})
                        self.assertTrue(all(r["input_sequences"] == "4" for r in fixture.summary()))
                self.assertEqual(snapshots[0], snapshots[1])
                self.assertEqual(len(scans), 2)  # One scan per invocation, not per selected chain.
                fasta.write_text(">new1\nACGT\n>new2\nACGT\n", encoding="utf-8")
                self.assertEqual(fixture.run(), 0)
                self.assertTrue(all(r["input_sequences"] == "2" for r in fixture.summary()))

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires POSIX GNU Bash")
    def test_runner_reuses_successful_stage_and_rejects_changed_dependencies(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode)
                self.assertEqual(fixture.run(), 0)
                original = json.dumps(fixture.snapshot())
                fixture.index("TRA", "V").write_bytes(b"new-index")
                changed = json.dumps(fixture.snapshot())
                runner = fixture.source.parent/("run_igblast_base.sh" if mode == "base" else "run_pig_pipeline.sh")
                source = runner.read_text(encoding="utf-8")
                start = source.index("stage_script(){")
                end = source.index('echo "[BASE 1/5]') if mode == "base" else source.index("# Matching is cheap")
                functions = source[start:end]
                state = fixture.root/"state"
                state.mkdir()
                script = '''set -euo pipefail
STATE_DIR="$1"
SCRIPT_DIR="$2"
IGBLAST_DIR="$3"
PYTHON_BIN="$4"
SCIGBLAST_IGBLAST_DEPENDENCIES="$5"
NEW_DEPENDENCIES="$6"
STAGE="$7"
CONFIG_SHA256=config
hash_file(){ sha256sum "$1" | awk '{print $1}'; }
stage_root(){ printf '%s' "$IGBLAST_DIR"; }
''' + functions + '''
MAPPING_SHA256=mapping
mark_stage "$STAGE"
stage_done "$STAGE"
SCIGBLAST_IGBLAST_DEPENDENCIES="$NEW_DEPENDENCIES"
if stage_done "$STAGE"; then exit 20; fi
SCIGBLAST_IGBLAST_DEPENDENCIES="$5"
stage_done "$STAGE"
rm "$IGBLAST_DIR/.tasks/pair/sample/TRA.tsv"
if stage_done "$STAGE"; then exit 21; fi
'''
                result = subprocess.run(["bash", "-c", script, "test", str(state), str(fixture.source.parent),
                                         str(fixture.ns["OUTPUT"]), sys.executable, original, changed,
                                         "05.igblast" if mode == "base" else "igblast"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_same_dependencies_reuse_tasks_and_publish_output_path(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode)
                self.assertEqual(fixture.run(), 0)
                self.assertEqual(fixture.run(), 0)
                self.assertEqual(fixture.calls, ["TRA"])
                row = fixture.summary()[0]
                self.assertTrue(Path(row["output_path"]).is_file())
                self.assertEqual(row["output_rows"], "1")

    def test_reference_change_only_invalidates_affected_chain(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode, ("TRA", "TRB"))
                self.assertEqual(fixture.run(), 0)
                old = fixture.snapshot()
                fixture.index("TRA", "V").write_bytes(b"changed-index")
                new = fixture.snapshot()
                self.assertNotEqual(old["fingerprint"], new["fingerprint"])
                self.assertNotEqual(old["chains"]["TRA"], new["chains"]["TRA"])
                self.assertEqual(old["chains"]["TRB"], new["chains"]["TRB"])
                self.assertEqual(fixture.run(), 0)
                self.assertEqual(fixture.calls.count("TRA"), 2)
                self.assertEqual(fixture.calls.count("TRB"), 1)

    def test_aux_tool_parameters_filter_and_reference_version_invalidate(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode)
                old = fixture.snapshot()
                fixture.aux().write_bytes(b"new-aux")
                self.assertNotEqual(old["fingerprint"], fixture.snapshot()["fingerprint"])
                fake_tool = fixture.root/"fake-tool"
                fake_tool.write_bytes(b"tool")
                first = fixture.snapshot(binary=str(fake_tool))
                fake_tool.write_bytes(b"new-tool")
                self.assertNotEqual(first["fingerprint"], fixture.snapshot(binary=str(fake_tool))["fingerprint"])
                stable = fixture.snapshot()
                self.assertNotEqual(stable["fingerprint"], fixture.snapshot(threads=3)["fingerprint"])
                with patch.object(cache, "FILTER_VERSION", "new-filter-version"):
                    self.assertNotEqual(stable["fingerprint"], fixture.snapshot()["fingerprint"])
                with patch.dict(os.environ, {"SCIGBLAST_IGBLAST_REFERENCE_VERSION": "release-2"}):
                    self.assertNotEqual(stable["fingerprint"], fixture.snapshot()["fingerprint"])

    def test_snapshot_reuse_does_not_scan_database_again(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = StageFixture(folder, "base")
            snapshot = fixture.snapshot()
            with patch.object(cache, "metadata", side_effect=AssertionError("unexpected metadata scan")):
                self.assertEqual(fixture.snapshot(inherited=json.dumps(snapshot)), snapshot)

    def test_optional_c_database_addition_and_change_only_invalidate_affected_chain(self):
        for mode in ('base', 'pig'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode, ('TRA','TRB'))
                self.assertEqual(fixture.run(), 0)
                previous = fixture.snapshot()
                fixture.index('TRA', 'C').write_bytes(b'C-index')
                added = fixture.snapshot()
                self.assertNotEqual(previous['chains']['TRA'],added['chains']['TRA'])
                self.assertEqual(previous['chains']['TRB'],added['chains']['TRB'])
                self.assertEqual(fixture.run(), 0)
                self.assertEqual(fixture.calls.count('TRA'),2)
                self.assertEqual(fixture.calls.count('TRB'),1)
                fixture.index('TRA','C').write_bytes(b'updated-C-index')
                self.assertEqual(fixture.run(),0)
                self.assertEqual(fixture.calls.count('TRA'),3)
                self.assertEqual(fixture.calls.count('TRB'),1)

    def test_failed_group_removes_previous_aggregate_and_can_recover(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode)
                self.assertEqual(fixture.run(), 0)
                aggregate = fixture.root/"output/pair/sample/TCR.tsv"
                self.assertTrue(aggregate.is_file())
                unrelated = fixture.root/"output/unrelated.tsv"
                unrelated.write_text("keep", encoding="utf-8")
                fixture.index("TRA", "V").write_bytes(b"new-index")
                fixture.failed = {"TRA"}
                self.assertEqual(fixture.run(), 1)
                self.assertFalse(aggregate.exists())
                self.assertEqual(unrelated.read_text(), "keep")
                row = fixture.summary()[0]
                self.assertEqual(row["status"], "ERROR")
                self.assertEqual(row["output_path"], "")
                fixture.failed.clear()
                self.assertEqual(fixture.run(), 0)
                self.assertTrue(aggregate.is_file())

    def test_partial_group_contains_only_current_successful_chains(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode, ("TRA", "TRB"))
                self.assertEqual(fixture.run(), 0)
                fixture.aux().write_bytes(b"new-aux")
                fixture.failed = {"TRB"}
                self.assertEqual(fixture.run(), 1)
                text = (fixture.root/"output/pair/sample/TCR.tsv").read_text(encoding="utf-8")
                self.assertIn("read-TRA", text)
                self.assertNotIn("read-TRB", text)
                self.assertEqual([row["status"] for row in fixture.summary()], ["OK", "ERROR"])
                self.assertFalse(list((fixture.root/"output").rglob("*.tmp")))

    def test_marker_publication_failure_never_reports_a_successful_task(self):
        for mode in ("base", "pig"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                fixture = StageFixture(folder, mode)
                replace = os.replace
                def fail_marker(source, destination):
                    if str(destination).endswith(".DONE"):
                        raise OSError("simulated marker write failure")
                    return replace(source, destination)
                with patch.object(os, "replace", side_effect=fail_marker):
                    self.assertEqual(fixture.run(), 1)
                self.assertFalse((fixture.root/"output/pair/sample/TCR.tsv").exists())
                self.assertEqual(fixture.summary()[0]["status"], "ERROR")
                self.assertFalse(list((fixture.root/"output").rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
