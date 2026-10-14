"""Required stage dependencies are checked in the runner's effective environment."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        runtime = patch.dict(os.environ, {'SCIGBLAST_RUNTIME_BIN_DIR': ''})
        runtime.start()
        self.addCleanup(runtime.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'db'
        self.calls = []
        for name in preflight.FOLDERS.values():
            folder = self.root / name / 'pipeline'
            folder.mkdir(parents=True)
            (folder / '00.pipeline_config.env').write_text(
                f'PYTHON_BIN="configured-python"\nSCIGBLAST_IGBLAST_BIN="configured-blast"\n'
                f'SCIGBLAST_IGBLAST_DB_DIR="{self.db.as_posix()}"\n'
                f'DB_ROOT="{self.db.as_posix()}"\nPIG_DB_VERSION="pig_chain"\n')

    def database(self, chain='IGH', species='human', version='human', parts=('V', 'J')):
        base = self.db / 'database_251117' / version / chain
        base.mkdir(parents=True, exist_ok=True)
        for part in parts:
            (base / f'{species}_gl_{chain}_{part}.nin').write_text('fixture')
        aux = self.db / 'optional_file' / ('pig.aux' if species == 'pig' else f'{species}_gl.aux')
        aux.parent.mkdir(parents=True, exist_ok=True)
        aux.write_text('fixture')

    def check(self, pipeline, env=None, **kwargs):
        def run(args, **options):
            self.calls.append((args, options))
            return type('Run', (), {'returncode': 0, 'stdout': '[]'})()
        with patch.object(preflight.shutil, 'which', side_effect=lambda cmd, **kw: cmd), \
                patch.object(preflight.subprocess, 'run', side_effect=run):
            return preflight.check_dependencies(pipeline, env or {}, pipeline_root=self.root, **kwargs)

    def test_match_requires_neither_analysis_tools_nor_database(self):
        for pipeline in ('ir_split', '10x_split', 'igblast_base', 'pig_igblast'):
            result = self.check(pipeline, {'SCIGBLAST_WEB_MATCH_ONLY': '1'})
            self.assertTrue(result['valid'], result)
            self.assertEqual(set(result['commands']), {'bash', 'flock', 'python'})
            self.assertEqual(result['missing_databases'], [])
            self.assertTrue(result['warnings'])

    def test_mapping_checks_selected_chain_and_has_no_pandas_dependency(self):
        self.database()
        result = self.check('mapping', chains=['IGH'])
        self.assertTrue(result['valid'], result)
        self.assertNotIn('pandas', self.calls[-1][0][-1])
        result = self.check('mapping', chains=['IGH', 'TRB'])
        self.assertFalse(result['valid'])
        self.assertTrue(all('TRB' in e for e in result['missing_databases']))

    def test_pig_requires_its_version_d_database_and_aux(self):
        self.database(species='pig', version='pig_chain')
        result = self.check('pig_igblast', chains=['IGH'])
        self.assertFalse(result['valid'])
        self.assertIn('IGH D', result['missing_databases'][0])
        self.database(species='pig', version='pig_chain', parts=('D',))
        self.assertTrue(self.check('pig_igblast', chains=['IGH'])['valid'])

    def test_runtime_override_and_config_precedence_match_runner(self):
        self.database()
        self.check('igblast_base', {'PYTHON_BIN': 'old-python'}, chains=['IGH'])
        self.assertEqual(self.calls[-1][0][0], 'configured-python')
        self.check('mapping', {'PYTHON_BIN': 'chosen-python'}, chains=['IGH'])
        self.assertEqual(self.calls[-1][0][0], 'chosen-python')
        runtime = self.root / 'runtime'
        self.check('mapping', {'PYTHON_BIN': 'old-python', 'SCIGBLAST_RUNTIME_BIN_DIR': str(runtime)}, chains=['IGH'])
        self.assertEqual(self.calls[-1][0][0], str(runtime / 'python3'))

    def test_missing_tool_and_python_error_are_blocking(self):
        with patch.object(preflight.shutil, 'which', return_value=None):
            result = preflight.check_dependencies('mapping', {}, ['IGH'], pipeline_root=self.root)
        self.assertFalse(result['valid'])
        self.assertTrue(any(e['code'] == 'missing_command' and e['blocking'] for e in result['errors']))
        with patch.object(preflight.shutil, 'which', return_value=sys.executable), \
                patch.object(preflight.subprocess, 'run', side_effect=OSError('not runnable')):
            result = preflight.check_dependencies('igblast_base', {}, stage='match', pipeline_root=self.root)
        self.assertFalse(result['valid'])
        self.assertEqual(result['errors'][0]['code'], 'missing_module')

    def test_ir_and_10x_require_the_c_database_passed_to_igblast(self):
        self.database('TRA', parts=('V', 'D', 'J'))
        for pipeline in ('ir_split', '10x_split'):
            result = self.check(pipeline, chains=['TRA'])
            self.assertFalse(result['valid'])
            self.assertIn('TRA C', result['missing_databases'][0])
        self.database('TRA', parts=('C',))
        self.assertTrue(self.check('ir_split', chains=['TRA'])['valid'])

    def test_base_accepts_executable_in_database_bin(self):
        self.database()
        binary = self.db / 'bin/igblastn'
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        with patch.object(preflight.shutil, 'which', side_effect=lambda cmd, **kw: None if cmd == 'configured-blast' else cmd), \
                patch.object(preflight.subprocess, 'run', return_value=type('Run', (), {'returncode': 0, 'stdout': '[]'})()):
            result = preflight.check_dependencies('igblast_base', {}, ['IGH'], pipeline_root=self.root)
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['commands']['igblastn'], str(binary))


if __name__ == '__main__':
    unittest.main()
