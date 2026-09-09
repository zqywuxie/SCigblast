import importlib.util
from pathlib import Path
import unittest
import pandas as pd

spec = importlib.util.spec_from_file_location('csr_model', Path(__file__).resolve().parents[1] / 'models/csr_calculate.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class CSRTests(unittest.TestCase):
    def frame(self, rows):
        return pd.DataFrame(rows, columns=['locus','cdr3_aa','v_call','j_call','c_call','umi_counts'])

    def test_no_pairs_keeps_class_percentages(self):
        result = model.calculate_csr(self.frame([
            ['IGH','CDR_A','V','J','IGHM*01',3],
            ['IGH','CDR_B','V','J','IGHG*01',1],
        ]))
        self.assertEqual(result.shape, (1,2))
        self.assertEqual(result.iloc[0]['class_unswitched_percent_by_reads'], .75)
        self.assertEqual(result.iloc[0]['class_switched_percent_by_reads'], .25)

    def test_supported_pair_formula_unchanged(self):
        frame = self.frame([['IGH','SAME','V','J','IGHM*01',3], ['IGH','SAME','V','J','IGHG*01',1]])
        result = model.calculate_csr(frame)
        self.assertEqual(result.iloc[0]['IGHG-IGHM_CSR_ratio'], 1)
        self.assertEqual(result.iloc[0]['class_unswitched_percent_by_reads'], .75)

    def test_no_igh_remains_undefined_not_zero(self):
        result = model.calculate_csr(self.frame([['IGK','A','V','J','IGKC*01',1]]))
        self.assertEqual(result.shape, (1,2))
        self.assertTrue(result.isna().all().all())
