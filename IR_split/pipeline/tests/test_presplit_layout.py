import contextlib
import gzip
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('ir_split_layout', Path(__file__).resolve().parents[1] / '03.split_barcode.py')
split = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = split
spec.loader.exec_module(split)


class PresplitLayoutTests(unittest.TestCase):
    def test_single_sample_directory_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / 'input', root / 'output'
            folder = source / '10_LF'
            folder.mkdir(parents=True)
            reads = []
            for mate in (1, 2):
                path = folder / f'10_LF_R{mate}.fq.gz'
                with gzip.open(path, 'wt') as handle:
                    handle.write(f'@read/{mate}#ACGT\nACGT\n+\nIIII\n')
                reads.append(path)
            pair = split.FastqPair(split.SubmissionRecord('10_LF', '', '', ''), '10_LF', *reads)
            with contextlib.redirect_stdout(io.StringIO()):
                result = split._prepare_presplit_pair(pair, output, source, 4, True, root/'progress.csv', root/'state')
                self.assertEqual(result.status, 'OK', result.error)
                resumed = split._prepare_presplit_pair(pair, output, source, 4, True, root/'progress.csv', root/'state')
            self.assertEqual(resumed.status, 'SKIPPED', resumed.error)
            self.assertEqual(Path(result.output_r1), output/'10_LF/10_LF_R1.fq.gz')
            self.assertFalse((output/'10_LF/10_LF').exists())
            with gzip.open(result.umi_sidecar, 'rt') as handle:
                self.assertEqual(handle.read(), 'read_id\tumi\nread\tACGT\n')

    def test_distinct_batches_and_barcode_prefixes_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for batch in ('batchA', 'batchB'):
                for prefix in ('10_LF', '10_LF__BC_B1'):
                    pair = split.FastqPair(split.SubmissionRecord('10_LF', '', '', ''), '10_LF',
                        root/'input'/batch/'10_LF/10_LF_R1.fq.gz',
                        root/'input'/batch/'10_LF/10_LF_R2.fq.gz', prefix)
                    folder, _, _ = split._presplit_paths(pair, root/'output', root/'input')
                    expected = root/'output'/batch/'10_LF'
                    if prefix != '10_LF':
                        expected /= prefix
                    self.assertEqual(folder, expected)
