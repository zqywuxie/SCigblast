"""Cache hits retain authorization, freshness, bounds and copy isolation."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import test_workflow
import input_manifest as scans


class ScanCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rows = [dict(id=str(i), versions={'r1': [10, 1]}, valid=True) for i in range(401)]
        self.token = scans.save_scan(self.root, 'owner', 'igblast_base', '/input', self.rows)
        self.path = self.root / 'input_scans' / f'{self.token}.json'
        scans._cache.clear()

    def tearDown(self):
        scans._cache.clear()
        self.temp.cleanup()

    def page(self, **kwargs):
        return scans.scan_page(self.root, self.token, 'owner', 'igblast_base', '/input', **kwargs)

    def test_pages_parse_once_and_cannot_mutate_private_snapshot(self):
        with patch.object(scans.json, 'loads', wraps=json.loads) as parse:
            first = self.page(limit=200)
            first['rows'][0]['versions']['r1'][0] = 999
            self.assertEqual(len(self.page(offset=200)['rows']), 50)
            self.assertEqual(self.page(offset=400)['rows'][0]['id'], '400')
            whole = scans.get_scan(self.root, self.token, 'owner', 'igblast_base', '/input')
            whole['rows'][0]['id'] = 'changed'
            self.assertEqual(self.page()['rows'][0], self.rows[0])
            self.assertEqual(parse.call_count, 1)

    def test_hits_still_check_owner_pipeline_path_expiry_and_deletion(self):
        self.page()
        for owner, pipeline, path in [('other', 'igblast_base', '/input'), ('owner', 'pig_igblast', '/input'), ('owner', 'igblast_base', '/other')]:
            with self.assertRaises(ValueError):
                scans.get_scan(self.root, self.token, owner, pipeline, path)
        with patch.object(scans.time, 'time', return_value=time.time()+90000):
            with self.assertRaises(ValueError):
                self.page()
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.page()
        self.assertFalse(scans._cache)

    def test_same_size_atomic_replacement_and_budgets(self):
        self.page()
        value = json.loads(self.path.read_text(encoding='utf-8'))
        value['rows'][0]['id'] = 'X'
        replacement = self.path.with_suffix('.tmp')
        replacement.write_text(json.dumps(value), encoding='utf-8')
        replacement.replace(self.path)
        self.assertEqual(self.page()['rows'][0]['id'], 'X')
        for setting in ('MAX_ROWS', 'MAX_BYTES', 'MAX_SCANS'):
            scans._cache.clear()
            with patch.object(scans, setting, 0):
                self.assertEqual(self.page()['total'], 401)
                self.assertFalse(scans._cache)

    def test_replaced_during_read_is_not_published_under_old_identity(self):
        real_read = Path.read_text
        replaced = False
        def changing(path, *args, **kwargs):
            nonlocal replaced
            raw = real_read(path, *args, **kwargs)
            if path == self.path and not replaced:
                replaced = True
                value = json.loads(raw)
                value['rows'][0]['id'] = 'replacement'
                path.write_text(json.dumps(value), encoding='utf-8')
            return raw
        with patch.object(Path, 'read_text', changing):
            self.assertEqual(self.page()['rows'][0]['id'], 'replacement')


if __name__ == '__main__':
    unittest.main()
