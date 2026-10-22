from __future__ import annotations

import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from analysis.treemap.sources import discover_directory, discover_job


AIRR_HEADER = "productive,v_call,j_call,junction_aa,cdr3_aa,umi_counts,locus\n"


def write_table(path: Path, header: str = AIRR_HEADER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.lower().endswith((".tsv", ".tsv.gz")) and header == AIRR_HEADER:
        header = header.replace(",", "\t")
    path.write_text(header, encoding="utf-8")
    return path


def write_summary(path: Path, fields: list[str], rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


class SourceDiscoveryTests(unittest.TestCase):
    def test_mapping_uses_successful_selected_umi_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = write_table(root / "03.umi_count" / "batch" / "Batch" / "S1__IGH.tsv")
            write_table(root / "03.umi_count" / "batch" / "Batch" / "S2__TRB.tsv")
            write_summary(
                root / "03.umi_count" / "batch" / "umi_count_summary.csv",
                ["source_file", "sample_id", "sample_key", "chain", "status"],
                [
                    {"source_file": "Batch/S1__IGH.csv", "sample_id": "S1", "sample_key": "Batch/S1", "chain": "IGH", "status": "OK"},
                    {"source_file": "Batch/S2__TRB.csv", "sample_id": "S2", "sample_key": "Batch/S2", "chain": "TRB", "status": "ERROR"},
                ],
            )

            entries = discover_job({"output_root": str(root), "dataset": "batch", "pipeline": "mapping"})

            self.assertEqual(len(entries), 2)
            ok, failed = entries
            self.assertEqual((ok["sample_key"], ok["chain"], ok["valid"]), ("Batch/S1", "IGH", True))
            self.assertEqual(ok["sources"][0]["path"], str(good.resolve()))
            self.assertEqual(failed["chain"], "TRB")
            self.assertFalse(failed["valid"])

    def test_mapping_honors_saved_sample_and_chain_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_table(root / "03.umi_count" / "batch" / "S1__IGH.tsv")
            write_table(root / "03.umi_count" / "batch" / "S1__TRB.tsv")
            write_table(root / "03.umi_count" / "batch" / "S2__IGH.tsv")
            write_summary(
                root / "03.umi_count" / "batch" / "umi_count_summary.csv",
                ["source_file", "sample_id", "sample_key", "chain", "status"],
                [
                    {"source_file": f"{sample}__{chain}.csv", "sample_id": sample, "sample_key": sample, "chain": chain, "status": "OK"}
                    for sample, chain in (("S1", "IGH"), ("S1", "TRB"), ("S2", "IGH"))
                ],
            )

            entries = discover_job({
                "output_root": str(root), "dataset": "batch", "pipeline": "mapping",
                "options_json": '{"mapping_samples":["S1"],"mapping_chains":["TRB"]}',
            })

            self.assertEqual([(item["sample_key"], item["chain"]) for item in entries], [("S1", "TRB")])

    def test_base_and_pig_use_authoritative_task_output_path(self):
        for pipeline in ("igblast_base", "pig_igblast"):
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                actual = write_table(root / "05.igblastn_out" / "set" / ".tasks" / "p1" / "IGH.tsv")
                # An aggregate is present but is not the source selected by the summary.
                write_table(root / "05.igblastn_out" / "set" / "TCR.tsv")
                write_summary(
                    root / "05.igblastn_out" / "set" / "chain_summary.csv",
                    ["sample_id", "pair_id", "chain", "status", "output_path"],
                    [{"sample_id": "S1", "pair_id": "P1", "chain": "IGH", "status": "OK", "output_path": str(actual)}],
                )

                entries = discover_job({"output_root": str(root), "dataset": "set", "pipeline": pipeline})

                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["chain"], "IGH")
                self.assertEqual(entries[0]["sources"], [{"path": str(actual.resolve()), "chain": "IGH"}])

    def test_duplicate_summary_rows_merge_paths_and_dedupe_exact_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            one = write_table(root / "05.igblastn_out" / "set" / "part1.tsv")
            two = write_table(root / "05.igblastn_out" / "set" / "part2.tsv")
            write_summary(
                root / "05.igblastn_out" / "set" / "chain_summary.csv",
                ["sample_id", "chain", "status", "output_path"],
                [{"sample_id": "S1", "chain": "IGH", "status": "OK", "output_path": str(path)}
                 for path in (one, one, two)],
            )

            entries = discover_job({"output_root": str(root), "dataset": "set", "pipeline": "igblast_base"})

            self.assertEqual(len(entries), 1)
            self.assertEqual({Path(source["path"]).name for source in entries[0]["sources"]}, {"part1.tsv", "part2.tsv"})
            self.assertEqual(len(entries[0]["sources"]), 2)

    def test_ir_uses_manifest_labeled_junction_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_table(root / "08.preprocessing" / "set" / "junction_peps" / "07.igblastn_out" / "Batch" / "S1" / "TCR" / "TRA.csv")
            write_summary(
                root / "08.preprocessing" / "set" / "processing_manifest.csv",
                ["input_root", "relative_sample", "sample_id", "available_receptors", "status"],
                [{"input_root": "/data/07.igblastn_out", "relative_sample": "Batch/S1", "sample_id": "S1", "available_receptors": "TCR", "status": "OK"}],
            )

            entries = discover_job({"output_root": str(root), "dataset": "set", "pipeline": "ir_split"})

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["sample_key"], "07.igblastn_out/Batch/S1")
            self.assertEqual(entries[0]["chain"], "TRA")
            self.assertEqual(entries[0]["sources"][0]["path"], str(source.resolve()))

    def test_10x_loci_come_from_per_file_summary_and_shared_table_is_unmodified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "08.cluster" / "set"
            source = write_table(stage / "Batch" / "S1" / "TCR.tsv")
            write_summary(
                stage / "Batch" / "S1" / "TCR.stage8_summary.csv",
                ["locus", "retained"],
                [{"locus": "TRA", "retained": "T"}, {"locus": "TRB", "retained": "F"}],
            )
            write_summary(stage / "stage8_summary.csv", ["input_rows"], [{"input_rows": "1"}])

            entries = discover_job({"output_root": str(root), "dataset": "set", "pipeline": "10x_split"})

            self.assertEqual([entry["chain"] for entry in entries], ["TRA"])
            self.assertTrue(entries[0]["valid"])
            self.assertEqual(entries[0]["sources"], [{"path": str(source.resolve())}])

    def test_job_rejects_symlinked_or_out_of_root_sources(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root, external = Path(temporary), Path(outside)
            actual = write_table(external / "S1.tsv")
            write_summary(
                root / "05.igblastn_out" / "set" / "chain_summary.csv",
                ["sample_id", "chain", "status", "output_path"],
                [{"sample_id": "S1", "chain": "IGH", "status": "OK", "output_path": str(actual)}],
            )

            entries = discover_job({"output_root": str(root), "dataset": "set", "pipeline": "igblast_base"})

            self.assertEqual(len(entries), 1)
            self.assertFalse(entries[0]["valid"])
            self.assertIn("outside", entries[0]["error"])

    def test_directory_scan_accepts_named_chains_and_reports_ambiguous_combined_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_table(root / "Batch" / "S1__IGH.csv")
            compressed = root / "Batch" / "S1__TRB.tsv.gz"
            compressed.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(compressed, "wt", encoding="utf-8") as handle:
                handle.write(AIRR_HEADER.replace(",", "\t"))
            write_table(root / "Batch" / "TCR.tsv")
            write_table(root / "reports" / "not_airr.csv", "name,value\n")

            entries = discover_directory(root)

            self.assertEqual({entry["chain"] for entry in entries if entry["valid"]}, {"IGH", "TRB"})
            combined = next(entry for entry in entries if entry["sample_label"] == "TCR")
            self.assertFalse(combined["valid"])
            self.assertIn("manifest or chain-named files", combined["error"])

    def test_header_validation_reads_only_the_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "S1__IGH.tsv"
            path.write_text(AIRR_HEADER.replace(",", "\t") + ("x\t" * 2_000_000), encoding="utf-8")

            entries = discover_directory(root)

            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]["valid"])

    def test_directory_summary_discovery_includes_all_datasets_with_namespaced_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for dataset in ("first", "second"):
                source = write_table(root / "05.igblastn_out" / dataset / f"{dataset}.tsv")
                write_summary(
                    root / "05.igblastn_out" / dataset / "chain_summary.csv",
                    ["sample_id", "chain", "status", "output_path"],
                    [{"sample_id": "same", "chain": "IGH", "status": "OK", "output_path": str(source)}],
                )

            entries = discover_directory(root)

            self.assertEqual({entry["sample_key"] for entry in entries}, {"first/same", "second/same"})
            self.assertEqual({entry["source_files"][0] for entry in entries}, {
                "05.igblastn_out/first/first.tsv", "05.igblastn_out/second/second.tsv",
            })

    def test_directory_10x_discovery_uses_per_file_summaries_without_aggregate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "08.cluster" / "dataset"
            source = write_table(stage / "S1" / "TCR.tsv")
            write_summary(stage / "S1" / "TCR.stage8_summary.csv", ["locus"], [{"locus": "TRB"}])

            entries = discover_directory(root)

            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]["valid"])
            self.assertEqual(entries[0]["chain"], "TRB")
            self.assertEqual(entries[0]["sample_key"], "dataset/S1")
            self.assertEqual(entries[0]["source_files"], ["08.cluster/dataset/S1/TCR.tsv"])

    def test_ir_stage_does_not_fall_back_to_intermediate_airr_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_table(root / "08.preprocessing" / "set" / "07.igblastn_out" / "S1__IGH.tsv")

            entries = discover_directory(root)

            self.assertEqual(len(entries), 1)
            self.assertFalse(entries[0]["valid"])
            self.assertIn("authoritative summary", entries[0]["error"])

    def test_mis_cased_airr_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_table(root / "S1__IGH.tsv", "Productive\tv_call\tj_call\tjunction_aa\n")

            entries = discover_directory(root)

            self.assertEqual(len(entries), 1)
            self.assertFalse(entries[0]["valid"])
            self.assertIn("missing required", entries[0]["error"])


if __name__ == "__main__":
    unittest.main()
