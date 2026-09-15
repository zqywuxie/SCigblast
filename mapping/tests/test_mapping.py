import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mapping import fasta
from mapping.pipeline import run_mapping as runner
from mapping.pipeline.umi_count import organize


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / 'input'
        self.input.mkdir()
        self.output = self.root / 'output'
        self.db = self.root / 'db'
        (self.db / 'optional_file').mkdir(parents=True)
        (self.db / 'optional_file/human_gl.aux').write_text('fixture')
        self.config = runner.settings()
        self.config.update(SCIGBLAST_IGBLAST_BIN=sys.executable, SCIGBLAST_IGBLAST_DB_DIR=str(self.db))
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def source(self, relative='A__IGH.csv', content='CDR3(pep),copy,joinedSeq\nCAR_A,11,ACGT\nCAR_B,002,ACGT\nCAR_C,3,ACGT\n'):
        path = self.input / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def database(self, chain):
        folder = self.db / 'database_251117/human' / chain
        folder.mkdir(parents=True, exist_ok=True)
        for part in ('V', 'D', 'J', 'C'):
            (folder / f'human_gl_{chain}_{part}.nin').write_text('fixture')

    def fake_igblast(self, args, **kwargs):
        self.calls.append(args)
        ids = [s[1:] for s in Path(args[args.index('-query') + 1]).read_text().splitlines() if s.startswith('>')]
        out = Path(args[args.index('-out') + 1])
        with out.open('w', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerow(['sequence_id', 'v_call', 'productive', 'junction'])
            for index, identifier in enumerate(ids):
                writer.writerow([identifier, 'IGHV1' if index != 2 else '', 'F' if index == 1 else 'T', 'NA'])
        return type('Result', (), {'returncode': 0})()

    def execute(self, chains=None):
        with patch.object(runner.subprocess, 'run', side_effect=self.fake_igblast), redirect_stdout(io.StringIO()):
            return runner.run(self.input, self.output, 'batch', chains or ['IGH'], self.config)

    def test_discovery_selection_nested_names_and_unknown(self):
        self.source('one/A__IGH.csv'); self.source('two/A__IGH.CSV'); self.source('B__trb.csv'); self.source('unknown.csv')
        selected, skipped, ignored = fasta.select_files(self.input, ['igh'])
        self.assertEqual(len(selected), 2); self.assertEqual(len(skipped), 1); self.assertEqual(ignored, ['unknown.csv'])
        for chains in ([], ['BAD'], ['IGK']):
            with self.assertRaises(ValueError): fasta.select_files(self.input, chains)

    def test_conversion_preserves_copy_index_and_skips_empty_sequence(self):
        source = self.source(content='CDR3(pep),copy,joinedSeq\nC_A,002,ACGT\nX,7,\nC,3,ACGN\n')
        target = self.root / 'test.fasta'
        stats = fasta.convert(source, target)
        self.assertEqual(stats, {'input_rows': 3, 'input_sequences': 2, 'skipped_rows': 1})
        self.assertEqual(target.read_text(), '>0_C_A_002\nACGT\n>2_C_3\nACGN\n')

    def test_sample_selection_groups_chains_and_separates_sources(self):
        self.source('one/A__IGH.csv'); self.source('one/A__TRB.csv'); self.source('two/A__IGH.csv')
        chosen, skipped, _ = fasta.select_files(self.input, ['IGH'], ['one/A'])
        self.assertEqual([r['source_file'] for r in chosen], ['one/A__IGH.csv'])
        self.assertEqual(len(skipped), 2)
        self.assertEqual(len(fasta.select_files(self.input, ['IGH'])[0]), 2)
        for samples in ([], ['missing'], ['../one/A']):
            with self.assertRaises(ValueError): fasta.select_files(self.input, ['IGH'], samples)
        self.database('IGH')
        with patch.object(runner.subprocess, 'run', side_effect=self.fake_igblast), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run(self.input, self.output, 'batch', ['IGH'], self.config, ['one/A']), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.output/'03.umi_count/batch/one/A__IGH.raw.tsv').exists())
        self.assertFalse((self.output/'01.fasta/batch/two/A__IGH.fasta').exists())

    def test_bad_csv_and_zero_sequence(self):
        for content in ('copy,joinedSeq\n1,ACGT\n', 'CDR3(pep),copy,joinedSeq\nC,1,\n',
                        'CDR3(pep),copy,joinedSeq\nC,1,A!\n', 'CDR3(pep),copy,joinedSeq\n,1,ACGT\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                fasta.convert(self.source(content=content), self.root / 'test.fasta')

    def test_real_csv_conversion(self):
        source = Path(__file__).resolve().parents[1] / 'AL_s003_v8__IGH.csv'
        if not source.is_file(): self.skipTest('Local example CSV is not packaged')
        target = self.root / 'sample.fasta'
        self.assertEqual(fasta.csv_to_fasta(source, target), 43)
        self.assertTrue(target.read_text().startswith('>0_CARALSSAWKGVFDSW_11\n'))

    def test_all_chain_database_arguments(self):
        for chain in fasta.SUPPORTED:
            self.database(chain)
            args, _ = runner.database_args(self.db, chain)
            self.assertEqual(args[args.index('-ig_seqtype')+1], 'TCR' if chain.startswith('TR') else 'Ig')
            for part in ('V','D','J'):
                self.assertTrue(args[args.index('-germline_db_'+part)+1].endswith(f'human_gl_{chain}_{part}'))
            self.assertIn('-c_region_db', args)

    def test_three_stages_selected_chains_statistics_and_resume(self):
        self.source(); self.source('nested/A__IGH.csv'); self.source('unused__TRB.csv')
        self.database('IGH')  # Unselected TRB intentionally has no database.
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.calls), 2)
        with (self.output/'02.igblastn_out/batch/chain_summary.csv').open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual((rows[0]['mapped_rows'],rows[0]['productive_rows'],rows[0]['output_rows']), ('2','2','1'))
        self.assertEqual(rows[0]['mapping_percent'], '66.67')
        with (self.output/'03.umi_count/batch/A__IGH.raw.tsv').open() as handle:
            data = list(csv.DictReader(handle, delimiter='\t'))
        self.assertEqual([r['umi_count'] for r in data], ['11','002','3'])
        self.assertEqual(data[0]['junction'], 'NA')
        self.assertTrue((self.output/'.pipeline_state/batch/.pipeline.DONE').exists())
        self.assertFalse((self.output/'01.fasta/batch/unused__TRB.fasta').exists())
        self.calls.clear(); self.assertEqual(self.execute(), 0); self.assertEqual(self.calls, [])
        self.source(content='CDR3(pep),copy,joinedSeq\nCAR,9,ACGTACGT\n')
        self.assertEqual(self.execute(), 0); self.assertEqual(len(self.calls), 1)

    def test_umi_failure_resumes_without_mapping(self):
        self.source(); self.database('IGH')
        with patch.object(runner, 'organize', side_effect=ValueError('interrupted')):
            self.assertEqual(self.execute(), 1)
        self.assertFalse((self.output/'.pipeline_state/batch/.pipeline.DONE').exists())
        self.calls.clear()
        self.assertEqual(self.execute(), 0); self.assertEqual(self.calls, [])

    def test_missing_database_and_malformed_airr_fail(self):
        self.source()
        self.assertEqual(self.execute(), 1)
        source = self.root/'bad.tsv'; source.write_text('sequence_id\n0_C_1\n')
        with self.assertRaises(ValueError): runner.filter_airr(source, self.root/'filtered.tsv')

    def test_umi_legacy_and_empty_and_invalid(self):
        source, target = self.root/'old.tsv', self.root/'new.tsv'
        source.write_text('barcode\tvalue\n0_C_A_11\tNA\n')
        self.assertEqual(organize(source, target)['id_column'], 'barcode')
        self.assertIn('0_C_A_11\tNA\t11', target.read_text())
        source.write_text('sequence_id\tv_call\tproductive\n')
        self.assertEqual(organize(source, target)['output_rows'], 0)
        self.assertIn('umi_count', target.read_text())
        source.write_text('sequence_id\tbarcode\ninvalid\t0_C_1\n')
        with self.assertRaises(ValueError): organize(source, target)


if __name__ == '__main__': unittest.main()
