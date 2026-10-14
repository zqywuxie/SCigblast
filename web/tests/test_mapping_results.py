"""Inventory status, authorization and bounded I/O contracts (no pipeline run)."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mapping_results as results


class MappingResultIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.row = {'id': 'job', 'output_root': str(self.root), 'dataset': 'data',
                    'attempt_no': 1, 'execution_json': '{}', 'status': 'SUCCEEDED'}
        self.select(['A__IGH.csv'])
        results._cache.clear()

    def tearDown(self):
        results._cache.clear()
        self.temp.cleanup()

    def select(self, sources, chains=('IGH',), samples=None):
        identities = [results._identity(source, {}) for source in sources]
        self.row['options_json'] = json.dumps({'mapping_inputs': identities,
            'mapping_chains': list(chains),
            'mapping_samples': samples if samples is not None else sorted({item['sample_key'] for item in identities})})

    def summary(self, stage, records):
        folder, name = results.STAGES[stage]
        path = self.root / folder / 'data' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ['source_file', 'status', 'error', 'input_rows', 'input_sequences', 'output_rows']
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fields)
            writer.writeheader()
            writer.writerows(records)
        return path

    def output(self, source='A__IGH.csv', stage=2, legacy=False):
        path = self.root / results.STAGES[stage][0] / 'data' / Path(source).with_suffix('.filtered.tsv' if legacy else '.tsv')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'sequence_id\tumi_count\r\n0_C_11\t11\r\n')
        return path

    def kind(self, source='A__IGH.csv', stage=2, legacy=False):
        return 'mapping:' + (Path(results.STAGES[stage][0]) / 'data' / Path(source).with_suffix('.filtered.tsv' if legacy else '.tsv')).as_posix()

    def test_statuses_are_complete_and_filters_keep_global_counts(self):
        self.select([f'{name}__IGH.csv' for name in 'ABCDEFGHI'])
        self.summary(0, [{'source_file': 'D__IGH.csv', 'status': 'ERROR', 'error': '缺少 copy'},
                         {'source_file': 'I__IGH.csv', 'status': 'SKIPPED'},
                         {'source_file': 'unselected__IGH.csv', 'status': 'OK'},
                         {'source_file': 'A__TRB.csv', 'status': 'SKIPPED'}])
        self.summary(1, [{'source_file': 'F__IGH.csv', 'status': 'OK', 'output_rows': 2}])
        self.output('F__IGH.csv', stage=1)
        self.summary(2, [{'source_file': 'A__IGH.csv', 'status': 'OK', 'output_rows': 3},
                         {'source_file': 'B__IGH.csv', 'status': 'OK', 'output_rows': 0},
                         {'source_file': 'C__IGH.csv', 'status': 'OK'},
                         {'source_file': 'E__IGH.csv', 'status': 'ERROR', 'error': 'ID 无效'},
                         {'source_file': 'H__IGH.csv', 'status': 'OK', 'output_rows': 2},
                         {'source_file': 'unselected__IGH.csv', 'status': 'OK'}])
        for source in ('A__IGH.csv', 'B__IGH.csv', 'C__IGH.csv'):
            self.output(source)
        page = results.page(self.row)
        self.assertTrue(page['inventory_complete'])
        self.assertEqual(page['counts'], dict(total=8, success=3, empty=1, failed=2, pending=2, archived=0))
        by_sample = {item['sample_id']: item for item in page['rows']}
        self.assertEqual(by_sample['D']['error'], '缺少 copy')
        self.assertEqual(by_sample['E']['error'], 'ID 无效')
        self.assertIsNone(by_sample['C']['output_rows'])  # Legacy OK is not an invented zero.
        self.assertEqual(by_sample['H']['availability'], 'missing')
        self.assertIsNone(by_sample['H']['kind'])
        self.assertEqual(by_sample['F']['intermediate']['stage'], '02.igblastn_out')
        self.assertEqual(by_sample['F']['availability'], 'not_ready')
        filtered = results.page(self.row, state='failed', query='copy', limit=1)
        self.assertEqual(filtered['counts'], page['counts'])
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['rows'][0]['sample_id'], 'D')

    def test_legacy_inventory_uses_only_actual_summaries_and_scoped_new_inputs_are_kept(self):
        self.row['options_json'] = json.dumps({'mapping_chains': ['IGH'], 'mapping_samples': None})
        self.summary(0, [{'source_file': 'batch/A__IGH.csv', 'status': 'OK'},
                         {'source_file': 'B__IGH.csv', 'status': 'ERROR', 'error': 'bad'},
                         {'source_file': 'C__TRB.csv', 'status': 'OK'}])
        (self.root / 'not_in_summary__IGH.csv').write_text('unused')
        page = results.page(self.row)
        self.assertFalse(page['inventory_complete'])
        self.assertEqual({item['source_file'] for item in page['rows']}, {'batch/A__IGH.csv', 'B__IGH.csv'})
        self.select(['A__IGH.csv'], chains=('IGH', 'TRB'), samples=['A'])
        self.summary(2, [{'source_file': 'A__TRB.csv', 'status': 'OK', 'output_rows': 1}])
        self.output('A__TRB.csv')
        page = results.page(self.row)
        self.assertEqual({item['source_file'] for item in page['rows']}, {'A__IGH.csv', 'A__TRB.csv'})
        self.assertTrue(page['inventory_complete'])

    def test_cached_summaries_invalidate_on_update_delete_replacement_and_attempt(self):
        for stage in range(3):
            self.summary(stage, [{'source_file': 'A__IGH.csv', 'status': 'OK'}])
        self.output()
        with patch.object(results, '_read_summary', wraps=results._read_summary) as read:
            self.assertIsNotNone(results.lookup(self.row, self.kind()))
            self.assertEqual(read.call_count, 3)
            results.page(self.row)
            results.lookup(self.row, self.kind())
            self.assertEqual(read.call_count, 3)
            self.row['attempt_no'] = 2
            results.lookup(self.row, self.kind())
            self.assertEqual(read.call_count, 6)
            self.summary(2, [{'source_file': 'A__IGH.csv', 'status': 'ERROR', 'error': 'bad'}])
            self.assertIsNone(results.lookup(self.row, self.kind()))
            self.assertEqual(read.call_count, 9)
            summary = self.summary(2, [{'source_file': 'A__IGH.csv', 'status': 'OK'}])
            results.lookup(self.row, self.kind())
            before = read.call_count
            replacement = summary.with_suffix('.replacement')
            replacement.write_bytes(summary.read_bytes())
            replacement.replace(summary)
            self.assertIsNotNone(results.lookup(self.row, self.kind()))
            self.assertEqual(read.call_count, before + 3)
            summary.unlink()
            self.assertIsNone(results.lookup(self.row, self.kind()))

    def test_upstream_failure_revokes_stale_final_authorization(self):
        self.summary(2, [{'source_file': 'A__IGH.csv', 'status': 'OK'}])
        self.output()
        self.assertIsNotNone(results.lookup(self.row, self.kind()))
        self.summary(0, [{'source_file': 'A__IGH.csv', 'status': 'ERROR', 'error': 'conversion failed'}])
        self.assertIsNone(results.lookup(self.row, self.kind()))
        self.assertEqual(results.page(self.row)['rows'][0]['state'], 'failed')

    def test_cached_lookup_checks_only_target_and_page_checks_only_visible_files(self):
        sources = [f'{i:04}__IGH.csv' for i in range(200)]
        self.select(sources)
        self.summary(2, [{'source_file': source, 'status': 'OK', 'output_rows': 1} for source in sources])
        for source in sources:
            self.output(source)
        results.inventory(self.row)
        real_stat = Path.stat
        inspected = []
        def tracked(path, *args, **kwargs):
            if path.suffix == '.tsv':
                inspected.append(path.name)
            return real_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', tracked), patch.object(results, '_read_summary', side_effect=AssertionError('cached summaries must not reopen')):
            results.lookup(self.row, self.kind(sources[-1]))
            # Path.resolve() may stat the same target on Linux. The invariant
            # is which files are inspected, independent of platform internals.
            self.assertEqual(list(dict.fromkeys(inspected)), ['0199__IGH.tsv'])
            inspected.clear()
            page = results.page(self.row, offset=60, limit=5)
            self.assertEqual(page['total'], 200)
            self.assertEqual(list(dict.fromkeys(inspected)), [f'{i:04}__IGH.tsv' for i in range(60, 65)])

    def test_target_replacement_deletion_legacy_and_path_safety(self):
        self.summary(2, [{'source_file': 'A__IGH.csv', 'status': 'OK'},
                         {'source_file': '../../escape__IGH.csv', 'status': 'OK'},
                         {'source_file': '/absolute__IGH.csv', 'status': 'OK'}])
        target = self.output()
        before = results.lookup(self.row, self.kind())
        replacement = target.with_suffix('.new')
        replacement.write_bytes(b'replaced')
        replacement.replace(target)
        self.assertNotEqual(before['file_version'], results.lookup(self.row, self.kind())['file_version'])
        target.unlink()
        self.assertIsNone(results.lookup(self.row, self.kind()))
        self.output(legacy=True)
        available = results.files(self.row)
        self.assertEqual(len(available), 1)
        self.assertEqual(available[0]['kind'], self.kind(legacy=True))
        for kind in ('mapping:../../escape.tsv', self.kind().replace('.tsv', '.raw.tsv'),
                     'mapping:01.fasta/data/A__IGH.fasta'):
            self.assertIsNone(results.lookup(self.row, kind))
        # Simulate a directory/file link escaping the task root on all platforms.
        resolve = Path.resolve
        legacy = self.root / self.kind(legacy=True).removeprefix('mapping:')
        def escaped(path, *args, **kwargs):
            return self.root.parent / 'outside.tsv' if path == legacy else resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', escaped):
            self.assertIsNone(results.lookup(self.row, self.kind(legacy=True)))

    def test_same_names_in_different_batches_are_distinct_and_archived_has_no_single_download(self):
        sources = ['batch1/A__IGH.csv', 'batch2/A__IGH.csv']
        self.select(sources)
        self.summary(2, [{'source_file': source, 'status': 'OK'} for source in sources])
        for source in sources:
            self.output(source)
        page = results.page(self.row)
        self.assertEqual({item['sample_key'] for item in page['rows']}, {'batch1/A', 'batch2/A'})
        self.assertEqual(len({item['kind'] for item in page['rows']}), 2)
        self.row['status'] = 'ARCHIVED'
        page = results.page(self.row)
        self.assertEqual(page['counts']['archived'], 2)
        self.assertTrue(all(item['availability'] == 'archived' and item['kind'] is None for item in page['rows']))
        self.assertIsNone(results.lookup(self.row, self.kind(sources[0])))

    def test_cache_limits_jobs_and_record_weight(self):
        self.summary(2, [{'source_file': 'A__IGH.csv', 'status': 'OK'}])
        with patch.object(results, 'MAX_RECORDS', 1):
            results.inventory(self.row)
            self.assertEqual(len(results._cache), 0)
        with patch.object(results, 'MAX_JOBS', 1):
            results.inventory(self.row)
            other = {**self.row, 'dataset': 'other'}
            results.inventory(other)
            self.assertEqual(len(results._cache), 1)
            self.assertEqual(next(iter(results._cache))[1], 'other')


if __name__ == '__main__':
    unittest.main()
