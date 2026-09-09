"""Regression tests for old comma-delimited .tsv.gz representative snapshots."""
import csv
import gzip
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('ir_stage8', Path(__file__).resolve().parents[1] / '08.preprocessing.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class RepresentativeStateTests(unittest.TestCase):
    def test_legacy_csv_and_real_tsv_with_empty_header(self):
        for delimiter in (',', '\t'):
            with self.subTest(delimiter=delimiter), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / '.representative_state.tsv.gz'
                with gzip.open(source, 'wt', encoding='utf-8', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=['sample_id', 'umi', 'header', 'representative_id'], delimiter=delimiter)
                    writer.writeheader()
                    writer.writerows([
                        dict(sample_id='A', umi='ACGT', header='', representative_id='read1'),
                        dict(sample_id='A', umi='', header='', representative_id='read2'),
                        dict(sample_id='A', umi='', header='read3#TGCA', representative_id='read3'),
                    ])
                database = Path(tmp) / 'index.sqlite3'
                stats = stage.build_representative_index([source], database)
                self.assertEqual(stats['state_rows'], 2)
                index = stage.RepresentativeIndex(database)
                try:
                    self.assertEqual(index.lookup_many('A', ['read1', 'read2', 'read3']), {'read1':'ACGT','read3':'TGCA'})
                finally:
                    index.close()

    def test_empty_tokens_and_bad_schema(self):
        for value in ('', None, ' ', '@', '>'):
            self.assertEqual(stage._umi_from_header(value), '')
            self.assertEqual(stage._canonical_id(value), '')
            self.assertEqual(stage._aliases(value), set())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.tsv'
            path.write_text('wrong\tcolumns\n1\t2\n')
            with self.assertRaisesRegex(ValueError, 'missing sample/read ID columns'):
                stage.build_representative_index([path], Path(tmp) / 'index.sqlite3')


if __name__ == '__main__':
    unittest.main()
