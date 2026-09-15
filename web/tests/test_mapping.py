import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_workflow


class MappingWebTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.WorkflowTests()
        self.fixture.setUp()
        self.web = test_workflow.web
        self.client = self.fixture.client
        for chain in ('IGH', 'TRB'):
            (self.fixture.raw / f'A__{chain}.csv').write_text('CDR3(pep),copy,joinedSeq\nCAR,11,ACGT\n')
        self.body = {'pipeline':'mapping', 'input_path':str(self.fixture.raw), 'mapping_chains':['IGH']}

    def tearDown(self):
        self.fixture.tearDown()

    def create(self):
        response = self.client.post('/api/jobs', json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_scan_and_no_submission_create(self):
        response = self.client.post('/api/mapping/scan', json={'input_path':str(self.fixture.raw)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['chains'], [{'chain':'IGH','file_count':1},{'chain':'TRB','file_count':1}])
        self.assertEqual(response.json()['samples'], [{'sample_key':'A','sample_id':'A','chains':['IGH','TRB'],'file_count':2}])
        self.assertEqual(self.client.post('/api/validate', json=self.body).status_code, 200)
        jid = self.create(); row = self.web.get_job_row(jid)
        self.assertEqual(row['submission_path'], '')
        self.assertFalse(self.web.review_ready(row))
        self.assertEqual(json.loads(row['env_json'])['SCIGBLAST_MAPPING_CHAINS'], 'IGH')
        self.assertEqual(json.loads(json.loads(row['env_json'])['SCIGBLAST_MAPPING_SAMPLES']), ['A'])
        for key in ('SCIGBLAST_WEB_MATCH_ONLY','SCIGBLAST_MATCH_ONLY_FIRST_RUN','SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN'):
            self.assertEqual(json.loads(row['env_json'])[key], '0')
        for endpoint in ('match-preview', 'metadata-review'):
            self.assertEqual(self.client.get(f'/api/jobs/{jid}/{endpoint}').status_code, 409)
        self.assertEqual(self.client.post(f'/api/jobs/{jid}/rematch',json={}).status_code,409)
        self.assertFalse(self.web.snapshot(row)['requires_match_review'])

    def test_invalid_selection_and_changed_directory(self):
        for chains in ([], ['BAD'], ['IGK']):
            self.assertEqual(self.client.post('/api/jobs',json={**self.body,'mapping_chains':chains}).status_code,400)
        (self.fixture.raw/'A__IGH.csv').unlink()
        self.assertEqual(self.client.post('/api/jobs',json=self.body).status_code,400)
        self.assertEqual(self.client.post('/api/mapping/scan',json={'input_path':str(self.fixture.root.parent)}).status_code,400)

    def test_sample_subset_is_saved_and_invalid_samples_rejected(self):
        (self.fixture.raw/'B__IGH.csv').write_text('CDR3(pep),copy,joinedSeq\nCAR,3,ACGT\n')
        self.body['mapping_samples'] = ['B']
        jid = self.create(); row = self.web.get_job_row(jid)
        self.assertEqual(self.web.snapshot(row)['mapping_samples'], ['B'])
        self.assertEqual(json.loads(json.loads(row['env_json'])['SCIGBLAST_MAPPING_SAMPLES']), ['B'])
        for samples in ([], ['missing']):
            self.assertEqual(self.client.post('/api/jobs',json={**self.body,'mapping_samples':samples}).status_code,400)
        (self.fixture.raw/'B__IGH.csv').unlink()
        self.assertEqual(self.client.post('/api/jobs',json=self.body).status_code,400)

    def test_three_stage_completion_and_file_access(self):
        jid = self.create(); row = self.web.get_job_row(jid)
        state = self.web.state_dir(row); state.mkdir(parents=True)
        for stage in ('01.fasta','02.igblast'):
            (state/f'.pipeline_stage_{stage}.DONE').write_text('status=DONE\n')
        self.assertFalse(self.web.pipeline_done(row))
        self.assertEqual(self.web.snapshot(row)['progress'],66)
        (state/'.pipeline_stage_03.umi_count.DONE').write_text('status=DONE\n')
        (state/'.pipeline.DONE').write_text('status=DONE\n')
        self.assertTrue(self.web.pipeline_done(row))
        root = Path(row['output_root'])/'03.umi_count'/row['dataset']; root.mkdir(parents=True)
        (root/'umi_count_summary.csv').write_text('source_file,status\nA__IGH.csv,OK\n')
        (root/'A__IGH.tsv').write_text('sequence_id\tumi_count\n0_C_11\t11\n')
        fasta = Path(row['output_root'])/'01.fasta'/row['dataset']; fasta.mkdir(parents=True)
        (fasta/'conversion_summary.csv').write_text('source_file,status\nA__IGH.csv,OK\n')
        (fasta/'A__IGH.fasta').write_text('>0_C_11\nACGT\n')
        airr = Path(row['output_root'])/'02.igblastn_out'/row['dataset']; airr.mkdir(parents=True)
        (airr/'chain_summary.csv').write_text('source_file,status\nA__IGH.csv,OK\n')
        (airr/'A__IGH.tsv').write_text('sequence_id\n0_C_11\n')
        files = self.client.get(f'/api/jobs/{jid}/artifacts').json()['mapping_files']
        self.assertEqual([f['stage'] for f in files],['03.umi_count','02.igblastn_out'])
        fasta_kind = f'mapping:01.fasta/{row["dataset"]}/A__IGH.fasta'
        self.assertEqual(self.client.get(f'/api/jobs/{jid}/download',params={'kind':fasta_kind}).status_code,404)
        response = self.client.get(f'/api/jobs/{jid}/stage-summary',params={'kind':files[0]['kind']})
        self.assertEqual(response.json()['rows'][0]['umi_count'],'11')
        self.assertEqual(self.client.get(f'/api/jobs/{jid}/download',params={'kind':files[0]['kind']}).status_code,200)
        self.assertEqual(self.client.get(f'/api/jobs/{jid}/download',params={'kind':'mapping:../../secret'}).status_code,404)
        log = self.web.log_path(row); log.parent.mkdir(parents=True)
        log.write_text('[MAPPING 3/3] UMI count\npercent=80\n')
        self.assertEqual(self.web.parse_progress(row),('03.umi_count',80))

    def test_preflight_only_selected_chains(self):
        from mapping.pipeline import run_mapping
        with patch.object(run_mapping, 'database_args', return_value=([],[])) as db:
            response = self.client.get('/api/preflight',params={'pipeline':'mapping','mapping_chains':'IGH'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(response.json()['commands']),{'python','igblastn'})
        self.assertEqual(db.call_args.args[1],'IGH')


if __name__ == '__main__': unittest.main()
