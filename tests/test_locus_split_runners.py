"""Exercise the actual Bash stage functions with tiny AIRR files, no analyzers."""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'requires Linux Bash')
class LocusStageRunnerTests(unittest.TestCase):
    def test_base_and_pig_stage_markers_resume_and_repair(self):
        for branch, filename, label in [('Igblast_base', 'run_igblast_base.sh', '06.locus_split'),
                                         ('PigIgblast', 'run_pig_pipeline.sh', 'locus_split')]:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                runner = ROOT/branch/'pipeline'/filename
                source = runner.read_text(encoding='utf-8')
                if branch == 'Igblast_base':
                    functions = source[source.index('stage_script(){'):source.index('echo "[BASE 1/6]')]
                    block = source[source.index('echo "[BASE 6/6]'):source.index('echo "[BASE] completed:')]
                else:
                    functions = source[source.index('stage_script(){'):source.index('# Matching is cheap')]
                    block = source[source.index('split_args=()'):source.index('echo "[PIG] pipeline completed"')]
                stage = root/'output/05.igblastn_out/set'
                stage.mkdir(parents=True)
                incoming = stage/'TRA.tsv'
                incoming.write_text('sequence_id\tlocus\tproductive\nmatch\tTRA\tT\nother\tTRB\tT\n',encoding='utf-8')
                with (stage/'chain_summary.csv').open('w',newline='') as handle:
                    writer = csv.writer(handle)
                    writer.writerow(['sample_id','pair_id','chain','status','output_path'])
                    writer.writerow(['A','P','TRA','OK',incoming])
                manifest = root/'output/01.match/set/sample_manifest.csv'
                manifest.parent.mkdir(parents=True)
                manifest.write_text('sample_id,pair_id,igblast_chains,status\nA,P,TRA,OK\n',encoding='utf-8')
                script = '''set -euo pipefail
SCRIPT_DIR="$1"
OUTPUT_ROOT="$2/output"
PYTHON_BIN="$3"
DATASET_LABEL=set
STATE_DIR="$2/state"
DIRECTORY_MANIFEST=''
CONFIG_SHA256=config
MAPPING_SHA256=mapping
SUMMARY="$OUTPUT_ROOT/01.match/set/sample_manifest.csv"
IGBLAST_DIR="$OUTPUT_ROOT/05.igblastn_out/set"
stage_root(){ printf '%s/%s/set\n' "$OUTPUT_ROOT" "$1"; }
hash_file(){ sha256sum "$1" | awk '{print $1}'; }
run_monitored_stage(){ shift 2; "$@"; }
mkdir -p "$STATE_DIR"
''' + functions + f'''
if stage_done {label}; then exit 10; fi
''' + block + f'''
stage_done {label}
''' + block + f'''
rm "$OUTPUT_ROOT/06.locus_split/set/A/TRA.tsv"
if stage_done {label}; then exit 11; fi
''' + block + f'''
stage_done {label}
'''
                result = subprocess.run(['bash','-c',script,'test',str(runner.parent),str(root),sys.executable],
                                        capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('reusing',result.stdout)
                self.assertEqual((root/'output/06.locus_split/set/A/TRA.tsv').read_text(),
                                 'sequence_id\tlocus\tproductive\nmatch\tTRA\tT\n')
                self.assertIn('status=DONE',(root/'state'/f'.pipeline_stage_{label}.DONE').read_text())
                self.assertFalse(list(root.rglob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
