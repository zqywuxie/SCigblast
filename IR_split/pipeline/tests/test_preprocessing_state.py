"""Regression tests for old comma-delimited .tsv.gz representative snapshots."""
import csv
import gzip
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('ir_stage8', Path(__file__).resolve().parents[1] / '08.preprocessing.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class RepresentativeStateTests(unittest.TestCase):
    def test_source_namespaces_and_batch_labels(self):
        self.assertEqual(stage.sample_and_batch(('/output/07.igblastn_out/dataset','batchA/pair/S')),('S','dataset/batchA/pair'))
        self.assertEqual(stage.sample_and_batch(('/output/07.igblastn_out/dataset','S')),('S','dataset'))
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'state.csv'
            source.write_text('sample_id,sample_key,umi,header\nS,batchA/pair/S,AAAA,read1\nS,batchB/pair/S,CCCC,read1\n')
            database=Path(tmp)/'index.sqlite3'
            stage.build_representative_index([source],database)
            index=stage.RepresentativeIndex(database)
            self.assertEqual(index.lookup_many('batchA/pair/S',['read1']),{'read1':'AAAA'})
            self.assertEqual(index.lookup_many('batchB/pair/S',['read1']),{'read1':'CCCC'})
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
                    # Share the index exactly as the TCR/BCR read workers do.
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(index.lookup_many, 'A', ['read1', 'read3']) for _ in range(8)]
                        for future in futures:
                            self.assertEqual(future.result(), {'read1':'ACGT','read3':'TGCA'})
                    tasks = []
                    for receptor, locus in [('TCR', 'TRA'), ('BCR', 'IGH')]:
                        path = Path(tmp) / f'{receptor}.tsv'
                        stage.pd.DataFrame([dict(sequence_id='read1', sequence='ACGT',
                            locus=locus, productive='T', v_score=200, v_identity=100,
                            v_call='V1', j_call='J1')]).to_csv(path, sep='\t', index=False)
                        tasks.append({'source':str(path), 'sample':'A', 'receptor':receptor})
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(stage.read_one, task, index, 'state') for task in tasks]
                        for future in futures:
                            _, frame, stats, error = future.result()
                            self.assertEqual(error, '')
                            self.assertEqual(stats['state_matched_rows'], 1)
                            self.assertEqual(frame['umi_counts'].tolist(), [1])
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


class SampleLifetimeTests(unittest.TestCase):
    def test_completed_sample_released_before_next_memory_check(self):
        for failure in ('none', 'read', 'junction', 'empty'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp) / '07.igblastn_out' / 'dataset'
                output = Path(tmp) / '08.preprocessing'
                for sample in ('A', 'B'):
                    directory = root / sample
                    directory.mkdir(parents=True)
                    rows = [dict(sequence_id=f'{sample}_{i}', sequence=sequence,
                                 cdr3_aa=peptide, locus='TRA', productive='T',
                                 v_score=200, v_identity=100, v_call='V1', j_call='J1')
                            for i, (sequence, peptide) in enumerate((('ACGT', 'CAA'), ('CAGT', 'CAB'), ('ACGT', 'CAA')))]
                    if failure == 'empty' and sample == 'A':
                        for row in rows:
                            row['productive'] = 'F'
                    stage.pd.DataFrame(rows).to_csv(directory / 'TCR.tsv', sep='\t', index=False)
                if failure == 'read':
                    (root / 'A' / 'BCR.tsv').write_text('invalid\tcolumns\n1\t2\n')
                for name, value in {
                    'INPUT_DIRS': [root], 'OUTPUT_DIR': output,
                    'STATE_DIR': output / '.preprocessing_state',
                    'SAMPLE_STATE_DIR': output / '.preprocessing_state' / 'samples',
                    'UMI_SOURCE': 'row_contract', 'ALLOW_ROW_CONTRACT': True,
                    'FILTER_WORKERS': 2, 'SAMPLES_PER_WINDOW': 1,
                }.items():
                    stack.enter_context(patch.object(stage, name, value))

                references = {'A': [], 'B': []}
                original_read = stage.read_one
                original_write = stage.write_junctions

                def tracked_read(task, *args):
                    value = original_read(task, *args)
                    if value[1] is not None:
                        references[task['sample']].append(weakref.ref(value[1]))
                    return value

                def checked_memory():
                    if memory.call_count == 2:
                        self.assertTrue(references['A'])
                        # Do not collect here: the previous sample must have
                        # released its frames before the next memory guard.
                        self.assertTrue(all(reference() is None for reference in references['A']))
                    return True, 1024.0

                def write_junctions(items, *args):
                    if failure == 'junction' and next(iter(items.values()))[0]['sample'] == 'A':
                        raise OSError('simulated junction write failure')
                    return original_write(items, *args)

                stack.enter_context(patch.object(stage, 'read_one', side_effect=tracked_read))
                stack.enter_context(patch.object(stage, 'write_junctions', new=write_junctions))
                memory = stack.enter_context(patch.object(stage, 'memory_ok', side_effect=checked_memory))
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(redirect_stderr(io.StringIO()))
                self.assertEqual(stage.main(), 0 if failure == 'none' else 1)
                self.assertEqual(memory.call_count, 2)
                self.assertTrue(all(reference() is None for values in references.values() for reference in values))
                manifest = stage.pd.read_csv(output / 'processing_manifest.csv')
                self.assertEqual(manifest['sample'].tolist(), ['A', 'B'])
                self.assertEqual(manifest['status'].tolist(), ['OK' if failure == 'none' else 'ERROR', 'OK'])
                checkpoints = list((output / '.preprocessing_state' / 'samples').glob('*/sample.DONE.json'))
                self.assertEqual(len(checkpoints), 2 if failure == 'none' else 1)
                if failure == 'none':
                    before = {name: (output / name).read_bytes() for name in ('Datapoint.csv', 'processing_manifest.csv')}
                    self.assertEqual(stage.main(), 0)
                    self.assertEqual(memory.call_count, 2)  # Both samples resumed from checkpoints.
                    for name, contents in before.items():
                        self.assertEqual((output / name).read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
