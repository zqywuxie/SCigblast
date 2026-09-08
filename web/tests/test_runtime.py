"""Verify image tool routing without installing analysis dependencies locally."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class RuntimeConfigTests(unittest.TestCase):
    def test_branch_loaders(self):
        for branch in ('IR_split', '10X_split', 'Igblast_base', 'PigIgblast'):
            path = ROOT / branch / 'pipeline/tools/pipeline_config.py'
            spec = importlib.util.spec_from_file_location('runtime_config_test', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            config = path.parents[1] / '00.pipeline_config.env'
            for docker in (False, True):
                with self.subTest(branch=branch, docker=docker), patch.dict(os.environ, {}, clear=True):
                    if docker:
                        os.environ['SCIGBLAST_RUNTIME_BIN_DIR'] = '/opt/conda/bin'
                    result = module.load_config(config)
                    values = result if isinstance(result, dict) else os.environ
                    expected = str(Path('/opt/conda/bin') / 'igblastn') if docker else (
                        '/colddata/zqy/igblast/bin/igblastn' if branch == 'PigIgblast' else 'igblastn')
                    self.assertEqual(values['SCIGBLAST_IGBLAST_BIN'], expected)
                    self.assertEqual(values['PYTHON_BIN'], str(Path('/opt/conda/bin') / 'python3') if docker else 'python3')
                    if branch == 'PigIgblast':
                        self.assertEqual(values['DB_ROOT'], '/colddata/zqy/igblast')
                        self.assertEqual(values['IGBLAST_BIN'], expected)


if __name__ == '__main__':
    unittest.main()
