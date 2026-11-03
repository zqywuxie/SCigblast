"""Validate packaged stages without launching pipelines or requiring databases."""
import ast
import json
from pathlib import Path
import subprocess
import zipfile


def check_bundle(root=Path('/opt/scigblast'), registry_path=Path('/app/pipeline_registry.json')):
    demo_dir = registry_path.parent / 'static' / 'demos'
    if not (demo_dir / 'README.txt').is_file():
        raise RuntimeError('Teaching guide was not packaged')
    with zipfile.ZipFile(demo_dir / 'scigblast-teaching-v1.zip') as archive:
        if archive.testzip() is not None or 'scigblast-teaching-v1/README.txt' not in archive.namelist():
            raise RuntimeError('Teaching demo archive is incomplete or corrupt')
    for filename in ('pipeline_igblast_cache.py', 'pipeline_parallel.py', 'pipeline_progress.py', 'pipeline_locus_split.py', 'pipeline_trace.py', 'pipeline_primer.py', 'pipeline_stage_metrics.py', 'pipeline_input_manifest.py', 'pipeline_airr_preprocessing.py', 'pipeline_resource_lock.sh'):
        path = root / filename
        if not path.is_file():
            raise RuntimeError(f'Missing shared runner dependency: {path}')
        if filename.endswith('.py'):
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        else:
            subprocess.run(['bash', '-n', str(path)], check=True)
    for filename in ('preflight.py', 'deployment.py', 'table_preview.py', 'process_identity.py', 'artifact_download.py', 'mapping_results.py', 'input_manifest.py', 'notifications.py', 'postanalysis.py', 'derived_store.py', 'repertoire_analysis.py'):
        path = registry_path.parent / filename
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    for filename in ('__init__.py','sources.py','aggregate.py','render.py','run.py','clone_table.py'):
        path = root / 'analysis' / 'treemap' / filename
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    for filename in ('__init__.py','io.py','sources.py','catalog.py','matching.py','alternatives.py','aggregate.py','primer_metrics.py','output_metrics.py','postprocessed_usage.py','repertoire_metrics.py','render_metrics.py','usage.py','gene_cdr3.py','isotype.py','palette.py','render_usage.py','render_stages.py','render_matching.py','workbook.py','workbook_overview.py','report_html.py','export_html.py','report_rules.py','render.py','run.py','chart_data.py','figure_jobs.py','scenario_analysis.py','batch.py','compute_jobs.py','performance.py','private_cache.py','html_cache.py'):
        path = root / 'analysis' / 'repertoire' / filename
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    for filename in ('charts.min.js','charts.js','report.js','report.css','ECHARTS-LICENSE.txt'):
        path=root/'analysis/repertoire/assets'/filename
        if not path.is_file() or path.stat().st_size==0:raise RuntimeError('Missing report viewer asset: '+str(path))
    api_summary=root/'analysis/repertoire/api_summary.py'
    ast.parse(api_summary.read_text(encoding='utf-8-sig'),filename=str(api_summary))
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    for pipeline in registry.values():
        runner = root / pipeline['runner']
        if not runner.is_file():
            raise RuntimeError(f'Missing packaged runner: {runner}')
        folder = runner.parent
        required_files = ('00.pipeline_config.env', 'tools/pipeline_config.py', 'stop.sh')
        if pipeline['runner'] == 'mapping/pipeline/run_mapping_pipeline.sh':
            required_files = ('00.pipeline_config.env', 'run_mapping.py', 'umi_count.py', 'resources.py', '../fasta.py')
        for required in required_files:
            if not (folder / required).is_file():
                raise RuntimeError(f'Missing packaged file: {folder / required}')
            if required.endswith('.py'):
                ast.parse((folder / required).read_text(encoding='utf-8-sig'), filename=str(folder / required))
        for path in folder.rglob('*.py'):
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        for path in [*folder.rglob('*.sh'), folder / '00.pipeline_config.env']:
            subprocess.run(['bash', '-n', str(path)], check=True)
    if not (root / 'IR_split/pipeline/models/preprocessing.py').is_file():
        raise RuntimeError('IR preprocessing models were not packaged')
    if not (root / 'reference/8bp_barcodes.csv').is_file():
        raise RuntimeError('Default Barcode CSV was not packaged')
    print(f'Packaged pipelines: {len(registry)}, source validation OK', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/scigblast'))
    parser.add_argument('--registry', type=Path, default=Path('/app/pipeline_registry.json'))
    args = parser.parse_args()
    check_bundle(args.root, args.registry)
