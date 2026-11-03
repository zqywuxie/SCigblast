"""Repair verified pig J auxiliary annotations; stage, but do not install, C candidates.

Run against the actual IgBLAST root. Official responses, hashes, previous auxiliary
data and decisions are preserved in a new evidence directory. No C index is changed.
"""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.request import urlopen


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(text):
    text = html.unescape(re.sub("<[^>]*>", "", re.sub(r"<br\s*/?>", "\n", text, flags=re.I)))
    records = []
    for block in text.split(">")[1:]:
        lines = block.splitlines()
        header = lines[0].strip()
        seq = "".join(line.strip() for line in lines[1:]
                      if re.fullmatch("[ACGTURYSWKMBDHVNacgturyswkmbdhvn. -]+", line.strip()))
        seq = seq.replace(".", "").replace("-", "").replace(" ", "").upper()
        if "|" in header and seq:
            records.append({"header": header, "fields": header.split("|"), "sequence": seq})
    return records


def database(root, chain, region):
    prefix = root / "database_251117/pig_chain" / chain / f"pig_gl_{chain}_{region}"
    text = subprocess.check_output([str(root / "bin/blastdbcmd"), "-db", str(prefix),
                                    "-entry", "all", "-outfmt", "%i %s"], text=True)
    return {line.split()[0].removeprefix("lcl|"): line.split()[1].upper()
            for line in text.splitlines()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--apply-j", action="store_true")
    args = parser.parse_args()
    root, evidence = args.root.resolve(), args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    manifest = []

    def fetch(name, url):
        data = urlopen(url, timeout=60).read()
        (evidence / name).write_bytes(data)
        manifest.append({"file": name, "url": url, "sha256": sha(data)})
        return parse(data.decode())

    original = (root / "optional_file/pig.aux").read_bytes()
    rows = {p[0]: p[1:] for line in original.decode().splitlines()
            if (p := line.split()) and not line.startswith("#")}
    codons = dict(zip([a+b+c for a in "TCAG" for b in "TCAG" for c in "TCAG"],
                      "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"))
    changes, verified = [], []
    replacements = {}
    for chain in ["IGH", "IGK", "IGL", "TRB", "TRG"]:
        group = "IG" if chain.startswith("IG") else "TR"
        official = fetch(chain + "J.official.fasta",
                         "https://www.imgt.org/download/V-QUEST/IMGT_V-QUEST_reference_directory/"
                         f"Sus_scrofa/{group}/{chain}J.fasta")
        indexed = database(root, chain, "J")
        by_name = {r["fields"][1]: r for r in official}
        assert set(indexed) == set(by_name), (chain, "reference set changed")
        for allele, sequence in indexed.items():
            source = by_name[allele]
            assert sequence == source["sequence"], (allele, "sequence differs")
            frame = int(source["fields"][7]) - 1
            aa = "".join(codons.get(sequence[k:k+3], "X")
                         for k in range(frame, len(sequence)-2, 3))
            motif = re.search("WG.G" if chain == "IGH" else "FG.G", aa)
            if not motif and allele in {"TRGJ3-1*01", "TRGJ4-1*01"}:
                assert source["fields"][3] == "ORF"
                motif = re.search("F[GE]KA", aa)
            assert motif, (allele, "unresolved FWR4 anchor")
            expected = [str(frame), "J" + chain[-1], str(frame + motif.start()*3 - 1),
                        str((len(sequence)-frame) % 3)]
            record = {"allele": allele, "old": rows[allele], "new": expected,
                      "official_header": source["header"], "translation": aa,
                      "sequence_sha256": sha(sequence.encode())}
            verified.append(record)
            if rows[allele] != expected:
                assert chain in {"IGH", "IGK", "IGL"}, (chain, "unexpected TR change")
                changes.append(record)
            if chain in {"IGH", "IGK", "IGL"}:
                replacements[allele] = expected
    updated = []
    changed_ids = []
    for line in original.decode().splitlines():
        parts = line.split()
        key = parts[0].removeprefix("2026pig") if parts else ""
        if key in replacements and parts[1:] != replacements[key]:
            changed_ids.append(parts[0])
            line = "\t".join([parts[0]] + replacements[key])
        updated.append(line)
    candidate = ("\n".join(updated) + "\n").encode()
    (evidence / "pig.aux.before").write_bytes(original)
    (evidence / "pig.aux.corrected").write_bytes(candidate)

    # Reproduce installed official splices before constructing any new candidate.
    exons = fetch("IGHC.exons.official.html",
                  "https://www.imgt.org/genedb/GENElect?query=7.5+IGHC&species=Sus+scrofa")
    splices = fetch("IGHC.spliced.official.html",
                   "https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Sus+scrofa")
    installed_c = database(root, "IGH", "C")

    def join_parts(parts):
        seq = "".join(p["sequence"] for p in parts)
        first = parts[0]["fields"]
        # IMGT exon exports add a boundary base to complete a codon. For the
        # initial exon this base is unknown without the preceding J sequence;
        # official genomic C splices use N here. Preserve that convention.
        initial_unknown = bool(re.match(r"^[acgtn],", first[5], flags=re.I)
                               and first[8] == "+1")
        if initial_unknown:
            seq = "N" + seq[1:]
        return seq, initial_unknown

    controls = []
    for allele, installed_seq in installed_c.items():
        matches = [r for r in splices if r["fields"][1] == allele
                   and r["sequence"] == installed_seq]
        if not matches:
            continue
        source = matches[0]
        f = source["fields"]
        parts = []
        for label in f[4].split("+"):
            selected = [e for e in exons if e["fields"][0] == f[0]
                        and e["fields"][1] == allele and e["fields"][4] == label]
            assert len(selected) == 1, (allele, label, "ambiguous source")
            parts.append(selected[0])
        seq, _ = join_parts(parts)
        assert seq == installed_seq, (allele, "official splice reproduction failed")
        controls.append(allele)
    assert len(controls) == 22, ("unexpected control count", len(controls))

    targets = {
        "IGHD*04": ("AY228505", ["CH1", "H1", "H2", "CH2", "CH3"]),
        "IGHG4*01": ("EU372654", ["CH1", "H", "CH2", "CH3-CHS"]),
        "IGHG4*02": ("U03782", ["CH1", "H", "CH2", "CH3-CHS"]),
        "IGHG5-1*03": ("EU372656", ["CH1", "H", "CH2", "CH3-CHS"]),
        "IGHG5-2*01": ("AB699686", ["CH1", "H", "CH2", "CH3-CHS"]),
        "IGHG5-2*02": ("EU372657", ["CH1", "H", "CH2", "CH3-CHS"]),
        "IGHG6-2*03": ("EU372653", ["CH1", "H", "CH2", "CH3-CHS"]),
    }
    proposals, fasta = [], []
    for allele, (accession, labels) in targets.items():
        parts = []
        for label in labels:
            selected = [e for e in exons if e["fields"][0] == accession
                        and e["fields"][1] == allele and e["fields"][4] == label]
            assert len(selected) == 1, (allele, label)
            parts.append(selected[0])
        seq, initial_unknown = join_parts(parts)
        partial5 = any("partial in 5'" in p["header"] for p in parts)
        partial3 = any("partial in 3'" in p["header"] for p in parts)
        codon_start = int(parts[0]["fields"][7]) - 1
        aa = "".join(codons.get(seq[k:k+3], "X") for k in range(codon_start, len(seq)-2, 3))
        assert "*" not in aa, (allele, "unexpected stop codon")
        proposals.append({"allele": allele, "accession": accession, "exons": labels,
                          "old_length": len(installed_c[allele]), "candidate_length": len(seq),
                          "partial_5prime": partial5, "partial_3prime": partial3,
                          "initial_boundary_unknown": initial_unknown,
                          "complete_secreted_exon_set": not partial5 and not partial3 and allele != "IGHD*04",
                          "status": "candidate_not_installed",
                          "method": "locally_spliced_from_same_accession_IMGT_exons",
                          "source_headers": [p["header"] for p in parts],
                          "sequence_sha256": sha(seq.encode())})
        fasta.append(">" + allele + "\n" + "\n".join(seq[i:i+80] for i in range(0, len(seq), 80)))
    (evidence / "IGHC.seven_expanded_candidates.fasta").write_text("\n".join(fasta)+"\n")
    (evidence / "C_candidate_review.json").write_text(json.dumps({"official_splice_controls": controls,
                                                                 "records": proposals}, indent=2)+"\n")
    (evidence / "official_sources.json").write_text(json.dumps(manifest, indent=2)+"\n")
    audit = {"before_sha256": sha(original), "after_sha256": sha(candidate),
             "verified_J_records": verified, "changed_canonical_records": changes,
             "changed_ids_including_legacy_aliases": changed_ids, "applied": False}
    if args.apply_j:
        path = root / "optional_file/pig.aux"
        assert path.read_bytes() == original, "auxiliary file changed during audit"
        shutil.copy2(path, evidence / "pig.aux.backup")
        fd, temporary = tempfile.mkstemp(prefix=".pig.aux.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(candidate)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        assert path.read_bytes() == candidate
        audit["applied"] = True
    (evidence / "J_repair_audit.json").write_text(json.dumps(audit, indent=2)+"\n")
    print(json.dumps({"evidence": str(evidence), "applied": audit["applied"],
                      "changed_J_alleles": len(changes), "changed_aux_rows": len(changed_ids),
                      "aux_sha256": sha(candidate), "C_controls_passed": len(controls),
                      "C_candidates": [{k: r[k] for k in ["allele", "old_length", "candidate_length",
                                         "partial_5prime", "partial_3prime", "initial_boundary_unknown"]}
                                       for r in proposals]}, indent=2))


if __name__ == "__main__":
    main()
