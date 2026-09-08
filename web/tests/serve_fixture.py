"""Isolated browser-test server. Never invokes bioinformatics tools."""
import json
import uvicorn
from test_workflow import WorkflowTests, web

fixture = WorkflowTests()
fixture.setUp()
job_id = fixture.create()
fixture.match(job_id)
folder = fixture.out / '05.igblastn_out' / 'batch'
folder.mkdir(parents=True)
(folder / 'chain_summary.csv').write_text('sample,chain,input_sequences,mapped_seqs,mapping_percent,filtered_seqs\nA,TRA,100,80,80,40\n', encoding='utf-8')

@web.app.get('/fixture')
def fixture_info():
    return {'job_id':job_id, 'input':str(fixture.raw), 'submission':str(fixture.source), 'output':str(fixture.out)}

if __name__ == '__main__':
    try:
        uvicorn.run(web.app, host='127.0.0.1', port=8001)
    finally:
        fixture.tearDown()
