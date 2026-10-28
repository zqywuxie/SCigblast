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
    for filename in ('pipeline_igblast_cache.py', 'pipeline_parallel.py', 'pipeline_progress.py', 'pipeline_locus_split.py', 'pipeline_input_manifest.py', 'pipeline_resource_lock.sh'):
        path = root / filename
        if not path.is_file():
            raise RuntimeError(f'Missing shared runner dependency: {path}')
        if filename.endswith('.py'):
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        else:
            subprocess.run(['bash', '-n', str(path)], check=True)
    for filename in ('preflight.py', 'table_preview.py', 'process_identity.py', 'artifact_download.py', 'mapping_results.py', 'input_manifest.py', 'notifications.py', 'postanalysis.py', 'derived_store.py'):
        path = registry_path.parent / filename
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    for filename in ('__init__.py','sources.py','aggregate.py','render.py','run.py','clone_table.py'):
        path = root / 'analysis' / 'treemap' / filename
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
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
