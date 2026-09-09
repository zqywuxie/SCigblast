"""Exercise the runner's actual marker functions under Bash nounset."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class StageMarkersTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('bash'), 'requires Bash')
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
