"""Build-time smoke check; does not read samples or change model definitions."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    import fastapi, uvicorn, jinja2, openpyxl
    import numpy, pandas, parmap
    from Bio import SeqIO, pairwise2
    from skbio.diversity.alpha import shannon, pielou_e, gini_index, simpson, simpson_e

    commands = {
        "fastp": ["--version"],
        "igblastn": ["-version"], "blastdbcmd": ["-version"],
        "makeblastdb": ["-version"],
    }
    for name, args in commands.items():
        executable = shutil.which(name)
        if not executable:
            raise RuntimeError(f"Missing tool: {name}")
        subprocess.run([executable, *args], check=True, timeout=30)
    # PANDAseq has no consistent version-only CLI; assemble one synthetic pair.
    pandaseq = shutil.which('pandaseq')
    if not pandaseq:
        raise RuntimeError('Missing tool: pandaseq')
    with tempfile.TemporaryDirectory(prefix='scigblast-runtime-') as folder:
        folder = Path(folder)
        seq = 'ACGTGCTAGCATCGATGACCTGATCGTACGATGCTAGTCAGATCGTAGCTACGATCGTCAGTAC'
        rev = seq.translate(str.maketrans('ACGT', 'TGCA'))[::-1]
        for read, sequence in ((1, seq), (2, rev)):
            (folder / f'R{read}.fq').write_text(
                f'@TEST:1:FLOW:1:1101:100:100/{read}\n{sequence}\n+\n' + 'I' * len(sequence) + '\n',
                encoding='ascii')
        out = folder / 'merged.fasta'
        subprocess.run([pandaseq, '-B', '-f', str(folder / 'R1.fq'), '-r', str(folder / 'R2.fq'),
                        '-w', str(out), '-T', '1'], check=True, timeout=30)
        if not out.is_file() or not out.read_text().startswith('>'):
            raise RuntimeError('PANDAseq synthetic pair did not produce FASTA')
    for name in ("bash", "awk", "find", "gzip", "pigz", "ps", "pgrep", "setsid", "flock", "time"):
        if not shutil.which(name):
            raise RuntimeError(f"Missing system tool: {name}")
    print(f"Analysis runtime OK: {sys.executable}", flush=True)


if __name__ == "__main__":
    main()
