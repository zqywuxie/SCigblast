"""Directory selection contracts, real Match adapters, and lifecycle isolation."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workflow
web = test_workflow.web
import input_manifest
import pipeline_input_manifest as inputs

ROOT = Path(__file__).resolve().parents[2]


class DirectoryInputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.WorkflowTests()
        self.fixture.setUp()
        self.client, self.raw = self.fixture.client, self.fixture.raw
        for name in ['Sample_A_R1.fastq', 'Sample_A_R2.fastq', 'Sample_B_R1_001.fq.gz', 'Sample_B_R2_001.fq.gz', 'Missing_R1.fastq']:
            (self.raw / name).write_text('synthetic payload')

    def tearDown(self):
        self.fixture.tearDown()

    def scan(self, pipeline='igblast_base'):
        response = self.client.post('/api/input/scan', json={'pipeline': pipeline, 'input_path': str(self.raw)})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        page = self.client.get(f"/api/input/scan/{result['scan_id']}", params={'pipeline':pipeline, 'input_path':str(self.raw)}).json()
        return result, page['rows']

    def body(self, pipeline='igblast_base'):
        result, rows = self.scan(pipeline)
        selected = [{'id': r['id'], 'sample_id': r['sample_id'], 'chains': ['IGH', 'IGK']} for r in rows if r['valid']]
        return dict(pipeline=pipeline, input_path=str(self.raw), sample_source='directory', input_scan_id=result['scan_id'], input_assignments=selected)

    def test_no_submission_validate_create_and_real_match(self):
        for pipeline, folder in [('igblast_base', 'Igblast_base'), ('pig_igblast', 'PigIgblast')]:
            with self.subTest(pipeline=pipeline):
                body = self.body(pipeline)
                body['input_assignments'][1]['chains'] = ['TRB']
                (self.raw / 'Added_R1.fastq').write_text('new file')
                before = list((web.STATE_ROOT / 'input_manifests').glob('*'))
                validated = self.client.post('/api/validate', json=body)
                self.assertEqual(validated.status_code, 200, validated.text)
                self.assertEqual(before, list((web.STATE_ROOT / 'input_manifests').glob('*')))
                created = self.client.post('/api/jobs', json=body)
                self.assertEqual(created.status_code, 200, created.text)
                job = created.json()
                self.assertEqual(job['submission_path'], '')
                self.assertEqual(job['sample_source'], 'directory')
                self.assertTrue(job['requires_match_review'])
                row = web.get_job_row(job['id'])
                env = json.loads(row['env_json'])
                options = json.loads(row['options_json'])
                self.assertNotIn('directory_manifest', options)
                summary = Path(row['output_root']) / '01.match' / row['dataset'] / 'sample_manifest.csv'
                run_env = {**os.environ, **env, 'PYTHONUTF8':'1', 'SCIGBLAST_INPUT_MANIFEST':env['SCIGBLAST_RUN_INPUT_MANIFEST'], 'SCIGBLAST_INPUT_MANIFEST_HASH':env['SCIGBLAST_RUN_INPUT_MANIFEST_HASH'], 'SCIGBLAST_MATCH_INPUT':str(self.raw), 'SCIGBLAST_RAW_INPUT_DIR':str(self.raw), 'RAW_INPUT_DIR':str(self.raw), 'SCIGBLAST_MATCH_OUTPUT':str(summary)}
                run = subprocess.run([sys.executable, str(ROOT/folder/'pipeline/01.match_sample.py')], env=run_env, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                with summary.open() as handle:
                    result = list(csv.DictReader(handle))
                self.assertEqual(len(result), 2)
                self.assertTrue(all(r['status']=='OK' for r in result))
                self.assertEqual({r['igblast_chains'] for r in result}, {'IGH,IGK','TRB'})
                self.assertEqual({r['species'].lower() for r in result}, {'pig' if pipeline=='pig_igblast' else 'human'})
                self.assertTrue(all('Added' not in r['pair_id'] for r in result))
                web.update_job(job['id'], status='WAITING_REVIEW')
                match=web.match_preview(job['id'])
                response=self.client.post(f"/api/jobs/{job['id']}/confirm-match", json={'revision':match.get('revision')})
                self.assertEqual(response.status_code,409, response.text)
                self.assertIn('未标注', response.text)
                (self.raw/'Added_R1.fastq').unlink()

    def test_invalid_selection_changes_and_ownership(self):
        body = self.body()
        for change in [{'input_assignments':[]}, {'submission_path':str(self.fixture.source)}, {'pipeline':'ir_split'}, {'input_scan_id':'a'*32}, {'input_assignments':[{'id':'a'*24,'chains':['IGH']}]}, {'input_assignments':[dict(body['input_assignments'][0],chains=['INVALID'])]}]:
            response=self.client.post('/api/validate',json={**body,**change})
            self.assertEqual(response.status_code,400,response.text)
        path=web.STATE_ROOT/'input_scans'/f"{body['input_scan_id']}.json"
        value=json.loads(path.read_text(encoding='utf-8'));value['owner_id']='another-user';path.write_text(json.dumps(value),encoding='utf-8')
        self.assertEqual(self.client.post('/api/validate',json=body).status_code,400)
        body=self.body();(self.raw/'Sample_A_R2.fastq').write_text('changed size')
        response=self.client.post('/api/jobs',json=body)
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('改变',response.text)

    def test_collision_duplicates_and_custom_suffixes(self):
        for directory in ['one','two']:
            folder=self.raw/directory;folder.mkdir()
            for read in ['R1','R2']:(folder/f'same_{read}.fq').write_text('x')
        base=self.body();names=[r['sample_id'] for r in base['input_assignments']]
        self.assertEqual(len(names),len(set(names)))
        pig=self.body('pig_igblast')
        self.assertIn('冲突',self.client.post('/api/validate',json=pig).text)
        custom=inputs.scan(self.raw,{'SCIGBLAST_RAW_FASTQ_EXTENSIONS':'.fq','SCIGBLAST_RAW_READ_SUFFIX_PAIRS':'_R1|_R2'})
        self.assertEqual(sum(r['valid'] for r in custom),2)
        (self.raw/'Sample_A_R1.fq').write_text('duplicate')
        rows=inputs.scan(self.raw,{})
        self.assertFalse(next(r for r in rows if r['pair_id']=='Sample_A')['valid'])

    def test_rematch_preserves_approved_assignments_and_revalidates_resume(self):
        body=self.body();created=self.client.post('/api/jobs',json=body).json();jid=created['id']
        row=web.get_job_row(jid);options=json.loads(row['options_json']);options['approved_assignments']=['saved-assignment']
        web.update_job(jid,status='STOPPED',options_json=json.dumps(options))
        fresh=self.body();fresh['input_assignments'][0]['chains']=['IGK']
        changed=self.client.post(f'/api/jobs/{jid}/input-rematch',json=fresh)
        self.assertEqual(changed.status_code,200,changed.text)
        options=json.loads(web.get_job_row(jid)['options_json'])
        self.assertEqual(options['approved_assignments'],['saved-assignment'])
        self.assertEqual(changed.json()['status'],'QUEUED')
        web.update_job(jid,status='STOPPED')
        (self.raw/'Sample_A_R2.fastq').unlink()
        response=self.client.post(f'/api/jobs/{jid}/resume',json={})
        self.assertEqual(response.status_code,400,response.text)

    def test_changed_queued_input_never_launches_and_names_are_unique(self):
        body=self.body()
        duplicate=json.loads(json.dumps(body))
        duplicate['input_assignments'][1]['sample_id']=duplicate['input_assignments'][0]['sample_id']
        response=self.client.post('/api/validate',json=duplicate)
        self.assertEqual(response.status_code,400,response.text)
        malformed={**body,'input_assignments':[{'id':[], 'chains':['IGH']}]}
        self.assertEqual(self.client.post('/api/validate',json=malformed).status_code,400)
        created=self.client.post('/api/jobs',json=body).json()
        (self.raw/'Sample_A_R2.fastq').write_text('changed after queueing')
        with patch.object(web.subprocess,'Popen') as launch:
            self.assertFalse(web.launch_job(created['id']))
            launch.assert_not_called()
        self.assertEqual(web.get_job_row(created['id'])['status'],'FAILED')

    def test_pig_rematch_retains_task_output_layout(self):
        body=self.body('pig_igblast')
        job=self.client.post('/api/jobs',json=body).json()
        options=json.loads(web.get_job_row(job['id'])['options_json'])
        self.assertEqual(options['directory_output_layout'],'sample-v1')
        # Emulate an existing pre-layout task without rewriting its source data.
        value=json.loads(Path(options['directory_manifest_path']).read_text(encoding='utf-8'))
        value.pop('output_layout')
        path,digest=input_manifest.publish(web.STATE_ROOT,value)
        options.pop('directory_output_layout')
        options.update(directory_manifest_path=path,directory_manifest_hash=digest)
        env=json.loads(web.get_job_row(job['id'])['env_json'])
        env.update(SCIGBLAST_RUN_INPUT_MANIFEST=path,SCIGBLAST_RUN_INPUT_MANIFEST_HASH=digest)
        web.update_job(job['id'],status='STOPPED',options_json=json.dumps(options),env_json=json.dumps(env))
        response=self.client.post(f"/api/jobs/{job['id']}/input-rematch",json=self.body('pig_igblast'))
        self.assertEqual(response.status_code,200,response.text)
        after=json.loads(web.get_job_row(job['id'])['options_json'])
        self.assertEqual(after['directory_output_layout'],'legacy')
        self.assertNotIn('output_layout',input_manifest.load(web.STATE_ROOT,after['directory_manifest_path'],after['directory_manifest_hash']))


if __name__=='__main__':unittest.main()
