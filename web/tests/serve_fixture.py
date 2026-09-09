"""Isolated browser-test server. Never invokes bioinformatics tools."""
import json
import uvicorn
from test_workflow import WorkflowTests, web

fixture = WorkflowTests()
fixture.setUp()
# Keep browser-created jobs separate from the fixture's intentionally shared root.
web.DEFAULT_OUTPUT_ROOT = fixture.root / 'runs'
job_id = fixture.create()
fixture.match(job_id)
folder = fixture.out / '05.igblastn_out' / 'batch'
folder.mkdir(parents=True)
(folder / 'chain_summary.csv').write_text('sample,chain,input_sequences,mapped_seqs,mapping_percent,filtered_seqs\nA,TRA,100,80,80,40\n', encoding='utf-8')
ir_id = fixture.create('ir_test', pipeline='ir_split')
fixture.match(ir_id, 2)
for stage, filename, content in (
    ('03.IR_split_output', 'ir_split_summary.csv', 'sample_id,total_reads,matched_reads,matched_pct,status\nA,100,80,80.00,OK\nB,100,0,0.00,OK\n'),
    ('05.pandaseq', 'pandaseq_summary.csv', 'Sample,Total_Reads,OK_Reads,Merged_Percent\nA,80,70,87.50\n')):
    report = fixture.out / stage / 'ir_test' / filename
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(content, encoding='utf-8')
web.add_action(ir_id, '测试人员', 'confirm-match', '1:' + 'a'*64)

@web.app.get('/fixture')
def fixture_info():
    return {'job_id':job_id, 'ir_id':ir_id, 'input':str(fixture.raw), 'submission':str(fixture.source), 'output':str(fixture.out)}

if __name__ == '__main__':
    try:
        uvicorn.run(web.app, host='127.0.0.1', port=8001)
    finally:
        fixture.tearDown()
