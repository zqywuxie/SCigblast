"""Exercise the runner's actual marker functions under Bash nounset."""
import os
import csv
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def has_usable_bash():
    # Windows' bash.exe is a WSL launcher, not a Bash process that can consume
    # the Windows temporary paths passed by this test.
    if os.name != 'posix' or not shutil.which('bash'):
        return False
    try:
        result = subprocess.run(
            ['bash', '--version'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=5,
        )
        return result.returncode == 0 and result.stdout.startswith('GNU bash')
    except (OSError, subprocess.SubprocessError):
        return False


class StageMarkersTest(unittest.TestCase):
    @unittest.skipUnless(has_usable_bash(), 'requires a usable GNU Bash installation')
    def test_preflight_uses_manifest_chains_and_stage_binary_priority(self):
        runner = Path(__file__).resolve().parents[1] / 'run_pig_pipeline.sh'
        source = runner.read_text(encoding='utf-8')
        function = source[source.index('preflight_db(){'):source.index('echo "$$" > "$PID_FILE"')]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root/'db/database_251117/pig_chain/IGH'
            database.mkdir(parents=True)
            for part in ('V', 'D', 'J'):
                (database/f'pig_gl_IGH_{part}.nin').write_text('fixture', encoding='utf-8')
            aux = root/'db/optional_file/pig.aux'
            aux.parent.mkdir()
            aux.write_text('fixture', encoding='utf-8')
            manifest = root/'sample_manifest.csv'
            with manifest.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['status', 'igblast_chains'])
                writer.writeheader()
                writer.writerows([{'status': 'OK', 'igblast_chains': 'IGH'},
                                  {'status': 'ERROR', 'igblast_chains': 'TRA,TRB'}])
            script = '''set -euo pipefail
DB_ROOT="$1/db"
PIG_DB_VERSION=pig_chain
PYTHON_BIN="$2"
SCIGBLAST_IGBLAST_BIN="$2"
IGBLAST_BIN="$1/does-not-exist"
stage_root(){ printf '%s' "$TEST_ROOT"; }
TEST_ROOT="$1"
''' + function + '\npreflight_db\n'
            def check():
                return subprocess.run(['bash', '-c', script, 'test', str(root), sys.executable],
                                      capture_output=True, text=True)
            result = check()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('database preflight OK', result.stdout)
            # The usable chain still requires D, including a VJ placeholder.
            (database/'pig_gl_IGH_D.nin').unlink()
            result = check()
            self.assertEqual(result.returncode, 2)
            self.assertIn('missing pig IGH D', result.stderr)

    @unittest.skipUnless(has_usable_bash(), 'requires a usable GNU Bash installation')
    def test_markers_are_stage_scoped_and_resume_checks_work(self):
        runner = Path(__file__).resolve().parents[1] / 'run_pig_pipeline.sh'
        source = runner.read_text(encoding='utf-8')
        functions = source[source.index('stage_done(){'):source.index('run_stage(){')]
        with tempfile.TemporaryDirectory() as folder:
            script = '''set -euo pipefail
STATE_DIR="$1"
CONFIG_SHA256=config
MAPPING_SHA256=mapping
stage_fingerprint(){ printf '%s' "$1"; }
stage_output_ready(){ return 0; }
''' + functions + '''
if stage_done fastp; then exit 10; fi
mark_stage fastp
stage_done fastp
if stage_done clean; then exit 11; fi
mark_stage clean
stage_done clean
stage_done fastp
SCIGBLAST_FORCE_RERUN=1
if stage_done fastp; then exit 12; fi
SCIGBLAST_FORCE_RERUN=0
MAPPING_SHA256=changed
if stage_done fastp; then exit 13; fi
'''
            result = subprocess.run(['bash', '-c', script, 'test', folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list(Path(folder).glob('*.tmp.*'))), 0)
            self.assertIn('stage=fastp', (Path(folder) / '.pipeline_stage_fastp.DONE').read_text())
            self.assertIn('stage=clean', (Path(folder) / '.pipeline_stage_clean.DONE').read_text())


if __name__ == '__main__':
    unittest.main()
