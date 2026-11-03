"""Install seven reviewed IMGT-derived pig IGH C references with an atomic directory exchange."""

import argparse
from collections import Counter
import ctypes
from datetime import datetime, timezone
import io
import csv
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess

from repair_pig_j_and_review_c import parse, sha


def fasta(path):
    result = {}
    for block in path.read_text().split(">")[1:]:
        lines = block.splitlines()
        name = lines[0].split()[0]
        assert name not in result
        result[name] = "".join(lines[1:]).upper()
    return result


def export(root, folder, region="C"):
    text = subprocess.check_output([str(root / "bin/blastdbcmd"), "-db",
                                    str(folder / f"pig_gl_IGH_{region}"),
                                    "-entry", "all", "-outfmt", "%i %s"], text=True)
    return {line.split()[0].removeprefix("lcl|"): line.split()[1].upper()
            for line in text.splitlines()}


def hashes(folder):
    return {p.name: sha(p.read_bytes()) for p in sorted(folder.iterdir()) if p.is_file()}


def no_mapping_running():
    result = subprocess.run(["pgrep", "-a", "-x", "igblastn"], capture_output=True, text=True)
    assert result.returncode == 1, "IgBLAST is running; staged database has not been installed: " + result.stdout


def sample_fasta(path, size=100):
    def records():
        with open(path) as stream:
            name, sequence = None, []
            for line in stream:
                if line.startswith(">"):
                    if name is not None:
                        yield name, "".join(sequence)
                    name, sequence = line[1:].strip().split()[0], []
                else:
                    sequence.append(line.strip())
            if name is not None:
                yield name, "".join(sequence)
    rng, selected = random.Random(20261103), []
    for n, record in enumerate(records(), 1):
        if n <= size:
            selected.append(record)
        else:
            j = rng.randrange(n)
            if j < size:
                selected[j] = record
    return "".join(">" + name + "\n" + seq + "\n" for name, seq in selected)


def smoke(root, live, staged):
    run_root = Path("/colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/20261102_042057")
    reports = []
    for path in run_root.glob(".analysis/trace/**/IGH.json"):
        trace = json.loads(path.read_text())
        if trace["sample"] not in {"H-K93-B", "N2-K270-med-B"}:
            continue
        query = sample_fasta(trace["mapping_source"])
        cmd = [str(root / "bin/igblastn"), "-query", "-", "-organism", "pig", "-ig_seqtype", "Ig",
               "-outfmt", "19", "-num_threads", "1", "-auxiliary_data", str(root / "optional_file/pig.aux")]
        for region in "VDJ":
            cmd += ["-germline_db_" + region, str(live / ("pig_gl_IGH_" + region))]
        for region in "VDJC":
            cmd += ["-num_alignments_" + region, "1"]
        results = []
        for folder in [live, staged]:
            proc = subprocess.run(cmd + ["-c_region_db", str(folder / "pig_gl_IGH_C")],
                                  input=query, text=True, capture_output=True, cwd=root,
                                  env=dict(os.environ, IGDATA=str(root), BLAST_USAGE_REPORT="false"), timeout=120)
            assert proc.returncode == 0, proc.stderr
            results.append({r["sequence_id"]: r for r in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t")})
        old, new = results
        assert set(old) == set(new) and len(new) == 100
        fields = ["v_call", "d_call", "j_call", "productive", "cdr3_aa", "junction_aa"]
        changes = {field: sum(old[k].get(field) != new[k].get(field) for k in new) for field in fields}
        assert not any(changes.values()), (trace["sample"], changes)
        reports.append({"sample": trace["sample"], "sample_size": len(new),
                        "query_sha256": sha(query.encode()), "non_C_changes": changes,
                        "C_call_changes": sum(old[k].get("c_call") != new[k].get("c_call") for k in new),
                        "C_before": dict(Counter(r.get("c_call", "") for r in old.values())),
                        "C_after": dict(Counter(r.get("c_call", "") for r in new.values()))})
    assert len(reports) == 2, "Both real input samples are required for this maintenance operation"
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root, review, evidence = args.root.resolve(), args.review.resolve(), args.evidence.resolve()
    live = root / "database_251117/pig_chain/IGH"
    evidence.mkdir(parents=True, exist_ok=False)
    for name in ["C_candidate_review.json", "IGHC.seven_expanded_candidates.fasta",
                 "IGHC.exons.official.html", "IGHC.spliced.official.html"]:
        shutil.copy2(review / name, evidence / name)
    manifest = json.loads((review / "official_sources.json").read_text())
    for source in manifest:
        assert sha((review / source["file"]).read_bytes()) == source["sha256"], source["file"]
    (evidence / "official_sources.json").write_text(json.dumps(
        [source for source in manifest if source["file"].startswith("IGHC.")], indent=2) + "\n")
    candidates = fasta(review / "IGHC.seven_expanded_candidates.fasta")
    assessment = json.loads((review / "C_candidate_review.json").read_text())
    assert len(assessment["official_splice_controls"]) == 22
    exons = parse((review / "IGHC.exons.official.html").read_text())
    old = export(root, live)
    assert old == fasta(live / "IGHC.fasta") and len(old) == 29 and len(candidates) == 7
    proposals = {r["allele"]: r for r in assessment["records"]}
    assert set(proposals) == set(candidates)
    for name, seq in candidates.items():
        r = proposals[name]
        assert r["status"] == "candidate_not_installed"
        assert len(old[name]) == r["old_length"] and len(seq) == r["candidate_length"]
        assert sha(seq.encode()) == r["sequence_sha256"]
        selected = []
        for header in r["source_headers"]:
            matches = [e for e in exons if e["header"] == header]
            assert len(matches) == 1
            assert matches[0]["fields"][0] == r["accession"] and matches[0]["fields"][1] == name
            selected.append(matches[0]["sequence"])
        derived = "".join(selected)
        if r["initial_boundary_unknown"]:
            derived = "N" + derived[1:]
        assert derived == seq, name
        assert re.fullmatch("[ACGTN]+", seq)

    before = hashes(live)
    no_mapping_running()
    backup = evidence / "backup_IGH"
    staged = evidence / "staged_IGH"
    shutil.copytree(live, backup)
    shutil.copytree(live, staged)
    assert hashes(backup) == before
    aux_path = root / "optional_file/pig.aux"
    aux_sha = sha(aux_path.read_bytes())
    shutil.copy2(aux_path, evidence / "pig.aux.at_C_install")
    new = dict(old)
    new.update(candidates)
    text = "".join(">" + allele + "\n" + "\n".join(seq[i:i+80] for i in range(0, len(seq), 80)) + "\n"
                   for allele, seq in new.items())
    (staged / "IGHC.fasta").write_text(text)
    provenance = json.loads((live / "pig_gl_IGH_C.provenance.json").read_text())
    provenance["version"] = "imgt-sus-scrofa-C-20261103-expanded-exons"
    provenance["policy"] = "Preserve 22 official spliced references; expand seven exon-only references using reviewed same-accession IMGT exons; retain partial ends and unknown initial boundary N."
    provenance["fasta_sha256"] = sha(text.encode())
    provenance["maintenance_evidence"] = str(evidence)
    provenance["source_bundles"] = [provenance["source_bundle"], str(evidence)]
    provenance["sources"] += [dict(source, file=str(evidence / source["file"]))
                              for source in manifest if source["file"].startswith("IGHC.")]
    for record in provenance["records"]:
        allele = record["allele"]
        if allele not in candidates:
            continue
        r = proposals[allele]
        previous = dict(record)
        record.update({"previous_reference": previous,
                       "source_header": "Locally spliced from same-accession IMGT exons; see source_headers",
                       "source_headers": r["source_headers"], "exons": "+".join(r["exons"]),
                       "source_file": str(evidence / "IGHC.exons.official.html"),
                       "selection": "locally_spliced_from_official_exons",
                       "annotation_partial": r["partial_5prime"] or r["partial_3prime"],
                       "partial_constant_region": not r["complete_secreted_exon_set"],
                       "partial_5prime": r["partial_5prime"], "partial_3prime": r["partial_3prime"],
                       "complete_secreted_exon_set": r["complete_secreted_exon_set"],
                       "initial_boundary_unknown": r["initial_boundary_unknown"],
                       "length": len(candidates[allele]), "sequence_sha256": r["sequence_sha256"],
                       "ambiguous_bases": sum(b not in "ACGT" for b in candidates[allele])})
    (staged / "pig_gl_IGH_C.provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    extensions = {".ndb", ".nhr", ".nin", ".njs", ".nog", ".nos", ".not", ".nsq", ".ntf", ".nto"}
    for path in staged.glob("pig_gl_IGH_C.*"):
        if path.suffix in extensions:
            path.unlink()
    build = subprocess.run([str(root / "bin/makeblastdb"), "-in", "IGHC.fasta", "-out", "pig_gl_IGH_C",
                            "-dbtype", "nucl", "-parse_seqids", "-title", "Sus scrofa IGH C IMGT-derived 2026-11-03"],
                           cwd=staged, text=True, capture_output=True)
    (evidence / "makeblastdb.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0, build.stderr
    assert export(root, staged) == new
    assert sum(old[a] != new[a] for a in old) == 7
    unchanged = [name for name in before if not (name == "IGHC.fasta" or name.startswith("pig_gl_IGH_C."))]
    assert all(sha((staged / name).read_bytes()) == before[name] for name in unchanged)
    verification = smoke(root, live, staged)
    (evidence / "real_input_validation.json").write_text(json.dumps(verification, indent=2) + "\n")

    no_mapping_running()
    assert hashes(live) == before, "Live database changed during preparation"
    assert sha(aux_path.read_bytes()) == aux_sha, "J annotations changed during preparation"
    assert live.stat().st_dev == staged.stat().st_dev
    libc = ctypes.CDLL(None, use_errno=True)
    exchange = libc.renameat2
    exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    exchange.restype = ctypes.c_int

    def swap():
        if exchange(-100, os.fsencode(live), -100, os.fsencode(staged), 2) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))

    swap()  # A single atomic exchange publishes the whole consistent directory.
    try:
        assert export(root, live) == new
        assert fasta(live / "IGHC.fasta") == new
        for region in "VDJ":
            assert export(root, live, region) == export(root, backup, region)
        assert sha(aux_path.read_bytes()) == aux_sha
    except BaseException:
        swap()
        raise
    os.rename(staged, evidence / "previous_IGH_after_atomic_exchange")
    audit = {"installed_at_utc": datetime.now(timezone.utc).isoformat(), "live_directory": str(live),
             "backup_directory": str(backup), "reference_count": len(new), "changed_references": list(candidates),
             "unchanged_references": [name for name in old if name not in candidates],
             "partial_references": [r["allele"] for r in provenance["records"] if r["partial_constant_region"]],
             "before_file_sha256": before, "after_file_sha256": hashes(live),
             "aux_sha256_unchanged": aux_sha, "V_D_J_unchanged": True,
             "all_indexed_sequences_equal_fasta": True, "real_input_checks": verification}
    (evidence / "installation_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps({k: audit[k] for k in ["installed_at_utc", "live_directory", "backup_directory",
                                         "reference_count", "changed_references", "partial_references",
                                         "V_D_J_unchanged", "all_indexed_sequences_equal_fasta"]}, indent=2))


if __name__ == "__main__":
    main()
