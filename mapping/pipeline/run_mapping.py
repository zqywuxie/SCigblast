"""Human CSV mapping runner. Stage and file checkpoints share the Web output root."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

if __package__:
    from ..fasta import convert, select_files
    from .umi_count import organize
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fasta import convert, select_files
    from umi_count import organize

BAD_V = {'', '*', '-', 'NA', 'N/A', 'NONE', 'NULL', 'NO_HIT', 'UNMAPPED', 'NOT_FOUND'}
TCR = {'TRA', 'TRB', 'TRD', 'TRG'}
STAGES = ('01.fasta', '02.igblast', '03.umi_count')


def settings():
    values = {}
    for line in Path(__file__).with_name('00.pipeline_config.env').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = shlex.split(value, comments=True)[0]
    values.update(os.environ)
    if values.get('SCIGBLAST_RUNTIME_BIN_DIR'):
        values['SCIGBLAST_IGBLAST_BIN'] = str(Path(values['SCIGBLAST_RUNTIME_BIN_DIR']) / 'igblastn')
    return values


def metadata(path):
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


def database_args(db_root: Path, chain: str):
    base = db_root / 'database_251117/human' / chain
    args, files = [], []
    for part in ('V', 'D', 'J', 'C'):
        prefix = base / f'human_gl_{chain}_{part}'
        matches = sorted(p for p in base.glob(prefix.name + '.*') if p.is_file())
        if not matches:
            if part in ('V', 'J'):
                raise ValueError(f'缺少 {chain} {part} 数据库：{prefix}')
            continue
        args += ['-c_region_db' if part == 'C' else f'-germline_db_{part}', str(prefix)]
        if part == 'C':
            args += ['-num_alignments_C', '1']
        files.extend(matches)
    aux = db_root / 'optional_file/human_gl.aux'
    if not aux.is_file():
        raise ValueError(f'缺少辅助文件：{aux}')
    return args + ['-auxiliary_data', str(aux), '-organism', 'human', '-ig_seqtype', 'TCR' if chain in TCR else 'Ig'], files + [aux]


def write_table(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def cached(marker, signature, outputs):
    try:
        saved = json.loads(marker.read_text(encoding='utf-8'))
        if saved['signature'] == signature and saved['outputs'] == [metadata(p) for p in outputs]:
            return saved['result']
    except (OSError, ValueError, KeyError):
        pass
    return None


def save(marker, signature, outputs, result):
    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_suffix('.tmp')
    temp.write_text(json.dumps({'signature': signature, 'outputs': [metadata(p) for p in outputs], 'result': result}, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, marker)


def filter_airr(source, destination):
    counts = dict(raw_rows=0, mapped_rows=0, productive_rows=0, output_rows=0)
    temp = destination.with_suffix(destination.suffix + '.tmp')
    try:
        with source.open(encoding='utf-8-sig', newline='') as src, temp.open('w', encoding='utf-8', newline='') as dst:
            reader = csv.DictReader((line for line in src if not line.startswith('#') and line.strip()), delimiter='\t')
            fields = reader.fieldnames or []
            if not {'sequence_id', 'v_call', 'productive'}.issubset(fields):
                raise ValueError('AIRR 缺少 sequence_id/v_call/productive 表头')
            writer = csv.DictWriter(dst, fieldnames=fields, delimiter='\t')
            writer.writeheader()
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError('AIRR 行列数不一致')
                mapped = row['v_call'].strip().upper() not in BAD_V
                productive = row['productive'].strip().upper() in {'T', 'TRUE'}
                counts['raw_rows'] += 1
                counts['mapped_rows'] += int(mapped)
                counts['productive_rows'] += int(productive)
                if mapped and productive:
                    writer.writerow(row)
                    counts['output_rows'] += 1
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return counts


def run(input_dir: Path, output: Path, dataset: str, chains: list[str], config=None, samples=None):
    config = config or settings()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', dataset) or dataset in {'.', '..'}:
        raise ValueError('无效 dataset 名称')
    input_dir, output = input_dir.resolve(), output.resolve()
    if output.is_relative_to(input_dir) or input_dir.is_relative_to(output):
        raise ValueError('输出目录不得与输入目录嵌套')
    state = output / '.pipeline_state' / dataset
    state.mkdir(parents=True, exist_ok=True)
    for name in ['.pipeline.DONE', *(f'.pipeline_stage_{s}.DONE' for s in STAGES)]:
        (state / name).unlink(missing_ok=True)
    fasta_root = output / '01.fasta' / dataset
    airr_root = output / '02.igblastn_out' / dataset
    umi_root = output / '03.umi_count' / dataset
    # Old summaries must not expose stale files while this attempt is rebuilt.
    for summary in (fasta_root / 'conversion_summary.csv', airr_root / 'chain_summary.csv', umi_root / 'umi_count_summary.csv'):
        summary.unlink(missing_ok=True)
    files, skipped, ignored = select_files(input_dir, chains, samples)
    conversion, results, umi = [], [], []
    identity = ['source_file', 'sample_id', 'sample_key', 'chain']

    def done(stage, rows):
        if rows and all(row['status'] != 'ERROR' for row in rows):
            (state / f'.pipeline_stage_{stage}.DONE').write_text('status=DONE\n', encoding='utf-8')

    print('[MAPPING 1/3] CSV → FASTA', flush=True)
    for item in skipped:
        conversion.append({**item, 'status': 'SKIPPED', 'error': '未选中链或样本'})
    for relative in ignored:
        conversion.append({'source_file': relative, 'status': 'SKIPPED', 'error': '未识别样本__链.csv 命名'})
    for index, item in enumerate(files, 1):
        rel = Path(item['source_file'])
        destination = fasta_root / rel.with_suffix('.fasta')
        marker = state / 'files' / rel.with_suffix('.fasta.json')
        row = {**item, 'status': 'ERROR', 'error': ''}
        try:
            source = input_dir / rel
            signature = [metadata(source), metadata(Path(__file__).parents[1] / 'fasta.py')]
            prior = cached(marker, signature, [destination])
            marker.unlink(missing_ok=True)
            result = prior if prior is not None else convert(source, destination)
            save(marker, signature, [destination], result)
            row.update(result, status='OK')
        except (OSError, ValueError) as exc:
            row['error'] = str(exc)
        conversion.append(row)
        print(f"[MAPPING] percent={index*100//len(files)} source={rel} status={row['status']}", flush=True)
    write_table(fasta_root / 'conversion_summary.csv', conversion, identity + ['input_rows', 'input_sequences', 'skipped_rows', 'status', 'error'])
    done('01.fasta', conversion)

    print('[MAPPING 2/3] IgBLAST', flush=True)
    threads = int(config['SCIGBLAST_IGBLAST_THREADS'])
    requested = int(config['SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS'])
    budget = int(config['SCIGBLAST_PARALLEL_THREAD_BUDGET'])
    memory = min(300, int(config['SCIGBLAST_MEMORY_BUDGET_GB']))
    per_job = int(config['SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB'])
    if min(threads, requested, budget, memory, per_job) < 1 or threads > budget or per_job > memory:
        raise ValueError('Mapping 并发配置超出线程/内存预算')
    workers = min(requested, budget // threads, memory // per_job)
    binary = shutil.which(config['SCIGBLAST_IGBLAST_BIN'])

    def map_one(item):
        row = {**item, 'species': 'human', 'status': 'ERROR', 'error': ''}
        rel = Path(item['source_file'])
        fasta = fasta_root / rel.with_suffix('.fasta')
        raw = airr_root / rel.with_suffix('.raw.tsv')
        filtered = airr_root / rel.with_suffix('.filtered.tsv')
        marker = state / 'files' / rel.with_suffix('.igblast.json')
        raw.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not binary:
                raise ValueError('igblastn 不可用')
            db_args, db_files = database_args(Path(config['SCIGBLAST_IGBLAST_DB_DIR']), item['chain'])
            signature = [metadata(fasta), metadata(binary), metadata(__file__), db_args, threads, [metadata(p) for p in db_files]]
            result = cached(marker, signature, [raw, filtered])
            marker.unlink(missing_ok=True)
            if result is None:
                temp = raw.with_suffix('.tmp')
                temp.unlink(missing_ok=True)
                args = [binary, '-query', str(fasta), *db_args, '-num_threads', str(threads), '-outfmt', '19', '-out', str(temp)]
                with raw.with_suffix('.log').open('w', encoding='utf-8') as log:
                    rc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT).returncode
                if rc or not temp.is_file():
                    temp.unlink(missing_ok=True)
                    raise ValueError(f'igblast_failed exit={rc}')
                os.replace(temp, raw)
                result = filter_airr(raw, filtered)
            n = item['input_sequences']
            row.update(result, status='OK', mapping_percent=f"{result['mapped_rows']*100/n:.2f}", retained_percent=f"{result['output_rows']*100/n:.2f}")
            save(marker, signature, [raw, filtered], result)
        except (OSError, ValueError) as exc:
            row['error'] = str(exc)
        return row

    usable = [r for r in conversion if r['status'] == 'OK']
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, row in enumerate(executor.map(map_one, usable), 1):
            results.append(row)
            print(f"[MAPPING] percent={index*100//len(usable)} source={row['source_file']} status={row['status']}", flush=True)
    write_table(airr_root / 'chain_summary.csv', results, identity + ['species', 'input_sequences', 'raw_rows', 'mapped_rows', 'productive_rows', 'output_rows', 'mapping_percent', 'retained_percent', 'status', 'error'])
    done('02.igblast', results)

    print('[MAPPING 3/3] UMI count 整理', flush=True)
    for item in results:
        if item['status'] != 'OK':
            continue
        rel = Path(item['source_file'])
        for kind in ('raw', 'filtered'):
            source = airr_root / rel.with_suffix(f'.{kind}.tsv')
            destination = umi_root / rel.with_suffix(f'.{kind}.tsv')
            marker = state / 'files' / rel.with_suffix(f'.{kind}.umi.json')
            row = {**{key: item[key] for key in identity}, 'kind': kind, 'status': 'ERROR', 'error': ''}
            try:
                signature = [metadata(source), metadata(Path(__file__).with_name('umi_count.py'))]
                result = cached(marker, signature, [destination])
                marker.unlink(missing_ok=True)
                if result is None:
                    result = organize(source, destination)
                save(marker, signature, [destination], result)
                row.update(result, status='OK')
            except (OSError, ValueError, csv.Error) as exc:
                row['error'] = str(exc)
            umi.append(row)
    write_table(umi_root / 'umi_count_summary.csv', umi, identity + ['kind', 'id_column', 'input_rows', 'output_rows', 'status', 'error'])
    done('03.umi_count', umi)
    failed = sum(r['status'] == 'ERROR' for r in [*conversion, *results, *umi])
    if not failed and all((state / f'.pipeline_stage_{stage}.DONE').is_file() for stage in STAGES):
        (state / '.pipeline.DONE').write_text('status=DONE\n', encoding='utf-8')
        print('[MAPPING] percent=100 completed', flush=True)
        return 0
    print(f'[MAPPING] failed={failed}', flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=os.environ.get('SCIGBLAST_RUN_RAW_INPUT_DIR'))
    parser.add_argument('--output-dir', type=Path, default=os.environ.get('SCIGBLAST_OUTPUT_ROOT'))
    parser.add_argument('--dataset', default=os.environ.get('SCIGBLAST_DATASET_LABEL', 'mapping'))
    parser.add_argument('--chains', nargs='+', default=os.environ.get('SCIGBLAST_MAPPING_CHAINS', '').split(','))
    parser.add_argument('--samples', nargs='+', default=json.loads(os.environ.get('SCIGBLAST_MAPPING_SAMPLES', 'null')),
                        help='样本键（相对目录/样本名），默认全部')
    args = parser.parse_args()
    if args.input_dir is None or args.output_dir is None:
        parser.error('input-dir and output-dir are required')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.dataset) or args.dataset in {'.', '..'}:
        parser.error('invalid dataset')
    if args.output_dir.resolve().is_relative_to(args.input_dir.resolve()) or args.input_dir.resolve().is_relative_to(args.output_dir.resolve()):
        parser.error('output and input directories must not overlap')
    log = args.output_dir / 'logs' / args.dataset / 'pipeline.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a', encoding='utf-8', buffering=1) as handle, redirect_stdout(handle), redirect_stderr(handle):
        try:
            return run(args.input_dir, args.output_dir, args.dataset, args.chains, samples=args.samples)
        except Exception as exc:
            print(f'[MAPPING] ERROR {exc}', flush=True)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
