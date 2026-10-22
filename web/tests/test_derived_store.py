"""Versioned persistence invariants without rendering or external processes."""
import json
import sqlite3
import unittest

import test_workflow
import derived_store as store


class DerivedStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE derived_runs(id TEXT PRIMARY KEY,data_json TEXT,done INTEGER,total INTEGER)')
        store.init_schema(self.db)
        self.data = dict(input_root='/synthetic', metric='records', clone_table_version='v1',
            items=[dict(id=str(i), sample_key=f'sample-{i//2:05}', sample_label=f'sample-{i//2}',
                        chain=('IGH','TRB')[i%2], status='queued', valid=True,
                        sources=[dict(path=f'/synthetic/{i}.tsv',version=[1,2,3])]) for i in range(1000)], files=[])
        self.db.execute('INSERT INTO derived_runs(id,data_json,done,total) VALUES(?,?,?,?)', ('run', json.dumps(self.data), 0, 1000))

    def tearDown(self):
        self.db.close()

    def row(self):
        return self.db.execute('SELECT * FROM derived_runs WHERE id=?', ('run',)).fetchone()

    def test_v1_read_save_preserves_full_json_and_does_not_migrate(self):
        self.assertFalse(store.v2(self.row()))
        self.assertEqual(store.load(self.db,self.row(),work=True),self.data)
        self.data['items'][0]['status']='failed'
        store.save(self.db,self.row(),self.data)
        self.assertEqual(self.row()['done'],1)
        self.assertEqual(json.loads(self.row()['data_json']),self.data)
        self.assertFalse(store.v2(self.row()))

    def test_v2_work_reads_bounded_queue_and_only_writes_changed_items(self):
        store.replace(self.db,'run',self.data)
        row=self.row()
        self.assertTrue(store.v2(row))
        self.assertNotIn('items',json.loads(row['data_json']))
        self.assertLess(len(row['data_json']),200)
        work=store.load(self.db,row,work=True)
        self.assertEqual(len(work['items']),32)
        work['items'][0]['status']='succeeded'
        work['items'][1]['status']='failed'
        work['files']=[dict(id='image',kind='chain',sample_key='sample-00000',chain='IGH',relative_path='a.png',version=[1,2,3])]
        statements=[]
        self.db.set_trace_callback(statements.append)
        store.save(self.db,row,work)
        self.db.set_trace_callback(None)
        self.assertEqual(sum(s.startswith('UPDATE derived_items') for s in statements),2)
        self.assertFalse(any('DELETE FROM derived_' in s for s in statements))
        self.assertEqual(self.row()['done'],2)
        store.save(self.db,self.row(),work)
        self.assertEqual(self.row()['done'],2)  # Repeat publication cannot double count.
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM derived_artifacts').fetchone()[0],1)
        full=store.load(self.db,self.row())
        self.assertEqual([item['id'] for item in full['items']],[str(i) for i in range(1000)])
        self.assertNotIn('_partial',full)
        self.assertEqual(store.artifact(self.db,self.row(),'image'),work['files'][0])
        self.assertIsNone(store.artifact(self.db,self.row(),'unknown'))

    def test_composition_advances_once_per_sample_and_delete_needs_no_foreign_keys(self):
        for item in self.data['items']:
            item['status']='empty'
        store.replace(self.db,'run',self.data)
        work=store.load(self.db,self.row(),work=True)
        self.assertEqual({item['sample_key'] for item in work['items']},{'sample-00000'})
        work['composed_samples']=['sample-00000']
        store.save(self.db,self.row(),work)
        work=store.load(self.db,self.row(),work=True)
        self.assertEqual({item['sample_key'] for item in work['items']},{'sample-00001'})
        store.delete(self.db,['run'])
        for table in ('derived_items','derived_artifacts','derived_samples'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)

    def test_replace_rolls_back_all_children_and_metadata_on_interruption(self):
        store.replace(self.db,'run',self.data)
        self.db.commit()
        with self.assertRaises(RuntimeError),self.db:
            store.replace(self.db,'run',dict(self.data,items=self.data['items'][:2]))
            raise RuntimeError('synthetic interrupted transaction')
        self.assertEqual(len(store.load(self.db,self.row())['items']),1000)
        self.assertEqual(self.row()['total'],1000)


if __name__=='__main__':
    unittest.main()
