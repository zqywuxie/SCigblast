"""Stage-8 entry-point regression tests; no external bioinformatics tools."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

spec = importlib.util.spec_from_file_location(
    'chain_cluster', Path(__file__).resolve().parents[1] / '08.chain_cluster.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class Stage8Tests(unittest.TestCase):
    def test_serial_and_parallel_finish_and_retry_existing_outputs(self):
        for workers in (1, 2):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, output = root / '07.igblastn_out', root / '08.cluster'
                for name in ('batchA/pair/S', 'batchB/pair/S'):
                    folder = source / name
                    folder.mkdir(parents=True)
                    pd.DataFrame([dict(
                        barcode='ACGT', sequence='ACGTACGT', locus='TRA',
                        productive='T', v_call='TRAV1*01', j_call='TRAJ1*01',
                        v_score='200', v_identity='100', cdr3='ACGT',
                    )]).to_csv(folder / 'TCR.tsv', sep='\t', index=False)
                with patch.multiple(stage, INPUT_DIR_VALUE=str(source),
                                    OUTPUT_DIR_VALUE=str(output), INPUT_DIR=source,
                                    OUTPUT_DIR=output, MAX_WORKERS=workers):
                    # Second run reproduces a retry with already-written per-file tables.
                    for _ in range(2):
                        log = io.StringIO()
                        with contextlib.redirect_stdout(log):
                            self.assertEqual(stage.main(), 0)
                        self.assertIn('completed files=2', log.getvalue())
                        self.assertEqual(len(pd.read_csv(output / 'stage8_summary.csv')), 2)
                        for name in ('batchA/pair/S', 'batchB/pair/S'):
                            table = pd.read_csv(output / name / 'TCR.tsv', sep='\t')
                            self.assertEqual(table['umi_counts'].tolist(), [1])
                            self.assertTrue((output / name / 'TCR.stage8_summary.csv').is_file())


if __name__ == '__main__':
    unittest.main()
