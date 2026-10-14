import json
import io
import zipfile
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
        self.assertEqual(response.json()['samples'], [{'sample_key':'A','sample_id':'A','chains':['IGH','TRB'],'chain_counts':{'IGH':1,'TRB':1},'file_count':2}])
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

    def test_inventory_records_exact_inputs_without_changing_runner_env(self):
        jid = self.create()
        row = self.web.get_job_row(jid)
        options = json.loads(row['options_json'])
        self.assertEqual([item['source_file'] for item in options['mapping_inputs']], ['A__IGH.csv'])
        self.assertFalse(any('MAPPING_INPUT' in key for key in json.loads(row['env_json'])))
        self.assertNotIn('mapping_inputs', self.client.get(f'/api/jobs/{jid}').json()['options'])
        page = self.client.get(f'/api/jobs/{jid}/mapping-results').json()
        self.assertTrue(page['inventory_complete'])
        self.assertEqual(page['counts']['pending'], 1)
        self.assertIsNone(page['rows'][0]['kind'])
        self.assertEqual(page['rows'][0]['source_file'], 'A__IGH.csv')
        self.assertEqual(self.client.get(f'/api/jobs/{jid}/mapping-results', params={'state':'invalid'}).status_code, 400)

    def test_artifacts_can_omit_legacy_all_files_lookup(self):
        jid = self.create()
        with patch.object(self.web, 'mapping_files', side_effect=AssertionError('full file lookup')):
            response = self.client.get(f'/api/jobs/{jid}/artifacts', params={'include_mapping_files':'false'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['mapping_files'], [])

    def test_legacy_mapping_exposes_filtered_but_not_raw_output(self):
        jid = self.create(); row = self.web.get_job_row(jid)
        root = Path(row['output_root'])/'03.umi_count'/row['dataset']; root.mkdir(parents=True)
        (root/'umi_count_summary.csv').write_text('source_file,status\nA__IGH.csv,OK\n')
        (root/'A__IGH.filtered.tsv').write_text('sequence_id\tumi_count\n0_C_11\t11\n')
        (root/'A__IGH.raw.tsv').write_text('sequence_id\tumi_count\n0_C_11\t12\n')
        files = self.web.mapping_files(row)
        self.assertEqual(len(files), 1)
        self.assertEqual(Path(files[0]['path']).name, 'A__IGH.filtered.tsv')

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
        with patch.object(self.web.runtime_preflight, 'check_dependencies', side_effect=test_workflow.available_dependencies) as check:
            response = self.client.get('/api/preflight',params={'pipeline':'mapping','mapping_chains':'IGH'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['stage'],'analysis')
        self.assertEqual(check.call_args.args[2], {'IGH'})

    def final_results(self):
        (self.fixture.raw / 'batch').mkdir()
        (self.fixture.raw / 'batch/A__IGH.csv').write_text('CDR3(pep),copy,joinedSeq\nCAR,11,ACGT\n')
        (self.fixture.raw / 'B__IGH.csv').write_text('CDR3(pep),copy,joinedSeq\nCAR,11,ACGT\n')
        jid = self.create()
        row = self.web.get_job_row(jid)
        output = Path(row['output_root'])
        root = output / '03.umi_count' / row['dataset']
        (root / 'batch').mkdir(parents=True)
        (root / 'umi_count_summary.csv').write_text(
            'source_file,sample_id,chain,output_rows,status\nbatch/A__IGH.csv,A,IGH,1,OK\n'
            'B__IGH.csv,B,IGH,0,OK\n', encoding='utf-8')
        (root / 'batch/A__IGH.tsv').write_bytes(b'sequence_id\tumi_count\r\n0_C_11\t11\r\n')
        (root / 'B__IGH.tsv').write_bytes(b'sequence_id\tumi_count\r\n')
        converted = output / '01.fasta' / row['dataset']
        converted.mkdir(parents=True)
        (converted / 'conversion_summary.csv').write_text(
            'source_file,input_rows,input_sequences,status\nbatch/A__IGH.csv,4,2,OK\n'
            'B__IGH.csv,0,0,OK\n', encoding='utf-8')
        return jid, root

    def test_result_metadata_distinguishes_sequences_and_copy(self):
        jid, root = self.final_results()
        files = self.client.get(f'/api/jobs/{jid}/artifacts').json()['mapping_files']
        first, empty = files
        self.assertEqual((first['sample_key'], first['chain']), ('batch/A', 'IGH'))
        self.assertEqual((first['input_rows'], first['input_sequences'], first['output_rows']), (4, 2, 1))
        self.assertEqual(first['retained_percent'], 50)
        self.assertIsNone(empty['retained_percent'])
        self.assertEqual(empty['output_rows'], 0)
        before = first['file_version']
        with (root / 'batch/A__IGH.tsv').open('ab') as handle:
            handle.write(b'1_C_5\t5\r\n')
        after = self.client.get(f'/api/jobs/{jid}/artifacts').json()['mapping_files'][0]
        self.assertNotEqual(before, after['file_version'])

    def test_selected_download_preserves_bytes_and_relative_names(self):
        jid, root = self.final_results()
        files = self.client.get(f'/api/jobs/{jid}/artifacts').json()['mapping_files']
        response = self.client.post(f'/api/jobs/{jid}/mapping-download', json={'kinds':[files[0]['kind']] * 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['file_count'], 1)
        archive = self.client.get(response.json()['url'])
        self.assertEqual(archive.status_code, 200, archive.text if archive.status_code != 200 else '')
        self.assertEqual(archive.headers['content-type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            self.assertEqual(zipped.namelist(), ['batch/A__IGH.tsv'])
            self.assertEqual(zipped.read('batch/A__IGH.tsv'), (root/'batch/A__IGH.tsv').read_bytes())

    def test_download_rejects_nonfinal_files_expiry_and_changed_results(self):
        jid, root = self.final_results()
        endpoint = f'/api/jobs/{jid}/mapping-download'
        for kinds in ([], ['mapping:../../secret'], ['mapping:01.fasta/A.fasta'], ['mapping:02.igblastn_out/A.tsv']):
            self.assertIn(self.client.post(endpoint, json={'kinds':kinds}).status_code, (400, 404))
        files = self.client.get(f'/api/jobs/{jid}/artifacts').json()['mapping_files']
        link = self.client.post(endpoint, json={'kinds':[files[0]['kind']]}).json()['url']
        with patch.object(self.web.time, 'monotonic', return_value=float('inf')):
            self.assertEqual(self.client.get(link).status_code, 404)
        link = self.client.post(endpoint, json={'kinds':[files[0]['kind']]}).json()['url']
        (root / 'batch/A__IGH.tsv').write_bytes(b'changed')
        self.assertEqual(self.client.get(link).status_code, 409)


if __name__ == '__main__': unittest.main()
