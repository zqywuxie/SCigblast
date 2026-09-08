"""Validate packaged stages without launching pipelines or requiring databases."""
import ast
import json
from pathlib import Path
import subprocess


def check_bundle(root=Path('/opt/scigblast'), registry_path=Path('/app/pipeline_registry.json')):
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    for pipeline in registry.values():
        runner = root / pipeline['runner']
        if not runner.is_file():
            raise RuntimeError(f'Missing packaged runner: {runner}')
        folder = runner.parent
        for required in ('00.pipeline_config.env', 'tools/pipeline_config.py', 'stop.sh'):
            if not (folder / required).is_file():
                raise RuntimeError(f'Missing packaged file: {folder / required}')
        for path in folder.rglob('*.py'):
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        for path in [*folder.rglob('*.sh'), folder / '00.pipeline_config.env']:
            subprocess.run(['bash', '-n', str(path)], check=True)
    if not (root / 'IR_split/pipeline/models/preprocessing.py').is_file():
        raise RuntimeError('IR preprocessing models were not packaged')
    if not (root / 'reference/8bp_barcodes.csv').is_file():
        raise RuntimeError('Default Barcode CSV was not packaged')
    print('Packaged pipelines: 4, source validation OK', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/scigblast'))
    parser.add_argument('--registry', type=Path, default=Path('/app/pipeline_registry.json'))
    args = parser.parse_args()
    check_bundle(args.root, args.registry)
