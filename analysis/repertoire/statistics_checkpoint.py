"""One verified private statistics checkpoint per report, consumed on publish."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import os
import shutil

from .io import digest, safe_file, version, write_json
from .private_cache import locked

SCHEMA = 'report-statistics-checkpoint-v1'
SCIENTIFIC_FILES = ('aggregate.py','alternatives.py','batch.py','catalog.py','compute_jobs.py',
    'gene_cdr3.py','io.py','isotype.py','matching.py','output_metrics.py','postprocessed_usage.py',
    'primer_metrics.py','repertoire_metrics.py','scenario_analysis.py','sources.py','usage.py','run.py','presentation.py')


def signature(request, items, root):
    from . import VERSION
    from .sources import files_under
    receipts = {str(p):version(p) for p in files_under(root) if p.name in {
        'fastp_summary.csv','clean_summary.csv','clean_counts.csv','pandaseq_summary.csv',
        'processing_manifest.csv','sample_manifest.csv','Datapoint.csv'} or p.suffix == '.json'}
    raw_scopes=[]
    for item in items:
        from .primer_metrics import raw_source
        meta=json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else {}
        path,state=raw_source(item,root,meta,request.get('allowed_input_roots',[str(root)]))
        raw_scopes.append([item['id'],str(path) if path else None,state])
        if path:receipts[str(path)]=version(path)
        parent=Path(item.get('summary_path','')).parent
        folder=(parent.parent.parent/'06.preprocessing'/parent.name if parent.parent.name=='05.igblastn_out'
                else parent.parent/'06.preprocessing' if parent.name=='05.igblastn_out' else None)
        if folder:
            for name in ('run.json','base_completion.json'):
                path=folder/'.preprocessing_state'/name
                if path.is_file():receipts[str(path)]=version(path)
    primer = json.loads(json.dumps(request.get('primer')))
    if primer:
        for name in ('submission_path','primer_path'):
            path=Path(primer[name]).resolve()
            receipts[str(path)]=version(path)
        # This parameter selects PNGs, never the scientific population.
        primer.get('columns',{}).pop('figure_primers',None)
    comparison=request.get('comparison_reference')
    if comparison:
        from scigblast.igblast_cache import reference
        name,_=reference(comparison['species'])
        for chain in {item['chain'] for item in items}:
            folder=Path(comparison['root'])/'database_251117'/name/chain
            for segment in ('V','J','C'):
                for path in folder.glob(f"{comparison['species']}_gl_{chain}_{segment}.n*"):
                    if path.is_file():receipts[str(path.resolve())]=version(path)
    code=hashlib.sha256()
    for name in SCIENTIFIC_FILES:
        code.update(Path(__file__).with_name(name).read_bytes())
    code.update((Path(__file__).parents[2]/'scigblast'/'stage_metrics.py').read_bytes())
    code.update((Path(__file__).parents[2]/'scigblast'/'igblast_cache.py').read_bytes())
    return digest([SCHEMA,VERSION,code.hexdigest(),str(root),items,primer,
                   request.get('comparison_reference'),request.get('allowed_input_roots'),
                   request.get('statistics_source'),raw_scopes,receipts]),receipts


class Checkpoint:
    def __init__(self, root, key, receipts):
        self.root=Path(root).resolve();self.folder=self.root/'statistics'
        self.key=key;self.receipts=receipts

    def load(self):
        try:
            marker=json.loads(safe_file(self.folder/'.checkpoint.json',self.folder).read_text(encoding='utf-8'))
            if marker.get('schema')!=SCHEMA or marker.get('key')!=self.key:return None
            path=safe_file(self.folder/'.statistics.json',self.folder)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=marker['data_sha256']:return None
            for name,expected in marker['indexes'].items():
                if version(safe_file(self.folder/name,self.folder))!=expected:return None
            if any(version(path)!=expected for path,expected in self.receipts.items()):return None
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,KeyError,TypeError):return None

    def clear(self):
        if not self.folder.exists():return
        if self.folder.is_symlink() or self.folder.resolve().parent!=self.root:
            raise ValueError('Unsafe statistics checkpoint directory')
        marker=self.folder/'.checkpoint.json'
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('schema')!=SCHEMA:
            raise ValueError('Unrecognized statistics checkpoint; preserve for inspection')
        if any(p.is_symlink() for p in self.folder.rglob('*')):
            raise ValueError('Statistics checkpoint contains a symbolic link')
        shutil.rmtree(self.folder)

    def seal(self, staging, data):
        staging=Path(staging)
        indexes={name:version(staging/name) for name in ('details.sqlite','scenario_details.sqlite') if (staging/name).is_file()}
        if 'details.sqlite' not in indexes:raise ValueError('Statistics index missing at checkpoint')
        if any(version(path)!=expected for path,expected in self.receipts.items()):
            raise ValueError('Source changed before statistics checkpoint')
        write_json(staging/'.statistics.json',data)
        write_json(staging/'.checkpoint.json',dict(schema=SCHEMA,key=self.key,indexes=indexes,
            data_sha256=hashlib.sha256((staging/'.statistics.json').read_bytes()).hexdigest()))
        self.clear()
        os.replace(staging,self.folder)
        return self.folder

    def retained(self, staging):
        return Path(staging)==self.folder and (self.folder/'.checkpoint.json').is_file()

    def prepare_retry(self):
        # Remove only generated images from this owned unpublished directory.
        # CSVs are atomically overwritten. Statistical databases stay immutable.
        for path in self.folder.rglob('*.png'):
            safe_file(path,self.folder).unlink()


@contextmanager
def checkpoint_for(request, output, items, root):
    key,receipts=signature(request,items,root)
    location=request.get('checkpoint_root') or Path(output).parent/'.report_checkpoints'/digest(
        request.get('run_id') or str(Path(output).absolute()))[:24]
    with locked(location) as owned:
        parent=Path(output).absolute().parent;parent.mkdir(parents=True,exist_ok=True)
        if parent.stat().st_dev!=owned.stat().st_dev:
            raise ValueError('Statistics checkpoint must be on the output filesystem')
        yield Checkpoint(owned,key,receipts)
