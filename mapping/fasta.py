#!/usr/bin/env python3
"""Convert <sample>__<chain>.csv to FASTA, preserving copy in the query ID."""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

INPUT_DIR = 'artificial_peps'
OUTPUT_DIR = './fasta'
CHAINS = ('IGH',)
HEADER_FIELDS = ('CDR3(pep)', 'copy')
SEQUENCE_FIELD = 'joinedSeq'
SUPPORTED = ('IGH', 'IGK', 'IGL', 'TRA', 'TRB', 'TRD', 'TRG')


def chain_of(filename: str) -> str | None:
    sample, sep, chain = Path(filename).stem.rpartition('__')
    return chain.upper() if sep and sample.strip() and chain.upper() in SUPPORTED else None


def discover(input_dir: Path) -> tuple[list[dict], list[str]]:
    """Shared discovery; directory links are not followed, file links stay inside root."""
    root = input_dir.resolve()
    if not root.is_dir():
        raise ValueError(f'输入目录不存在：{root}')
    files, ignored, destinations = [], [], set()
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(parent) / d).is_symlink())
        for name in sorted(names):
            if not name.lower().endswith('.csv'):
                continue
            path = Path(parent) / name
            if not path.resolve().is_relative_to(root):
                raise ValueError(f'CSV 指向输入目录以外：{path}')
            relative = path.relative_to(root).as_posix()
            chain = chain_of(name)
            if chain:
                destination = str(Path(relative).with_suffix('.fasta'))
                if destination in destinations:
                    raise ValueError(f'CSV 输出文件名冲突：{relative}')
                destinations.add(destination)
                sample = path.stem.rsplit('__', 1)[0]
                files.append({'source_file': relative, 'sample_id': sample,
                              'sample_key': (Path(relative).parent / sample).as_posix(), 'chain': chain})
            else:
                ignored.append(relative)
    return files, ignored


def select_files(input_dir: Path, chains: list[str], samples: list[str] | None = None) -> tuple[list[dict], list[dict], list[str]]:
    selected = {c.upper() for c in chains}
    if not selected or not selected.issubset(SUPPORTED):
        raise ValueError('请选择至少一条有效链：' + ', '.join(SUPPORTED))
    files, ignored = discover(input_dir)
    missing = selected - {f['chain'] for f in files}
    if missing:
        raise ValueError('输入目录中没有所选链：' + ', '.join(sorted(missing)))
    available = {f['sample_key'] for f in files}
    sample_keys = available if samples is None else set(samples)
    if not sample_keys or not sample_keys.issubset(available):
        raise ValueError('请选择至少一个当前目录中存在的样本')
    chosen = [f for f in files if f['chain'] in selected and f['sample_key'] in sample_keys]
    if not chosen:
        raise ValueError('所选样本中没有所选链的 CSV 文件')
    return chosen, [f for f in files if f['chain'] not in selected or f['sample_key'] not in sample_keys], ignored


def convert(csv_path: Path, fasta_path: Path, header_fields=HEADER_FIELDS, sequence_field=SEQUENCE_FIELD) -> dict:
    import pandas as pd
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    missing = [c for c in [*header_fields, sequence_field] if c not in df.columns]
    if missing:
        raise ValueError(f'缺少列：{missing}')
    fasta_path.parent.mkdir(parents=True, exist_ok=True)
    temp = fasta_path.with_suffix(fasta_path.suffix + '.tmp')
    written = skipped = 0
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as handle:
            for index, row in df.iterrows():
                sequence = row[sequence_field].strip()
                if not sequence or sequence.lower() == 'nan':
                    skipped += 1
                    continue
                first, count = (row[c] for c in header_fields)
                if not first or not count or re.search(r'\s|[>]', first + count) or '_' in count:
                    raise ValueError(f'数据行 {index}：无效的 CDR3/copy header 字段')
                if not re.fullmatch(r'[ACGTRYSWKMBDHVNUacgtryswkmbdhvnu]+', sequence):
                    raise ValueError(f'数据行 {index}：序列含非核酸 IUPAC 字符')
                handle.write(f'>{index}_{first}_{count}\n{sequence}\n')
                written += 1
        if not written:
            raise ValueError('没有有效序列')
        os.replace(temp, fasta_path)
    finally:
        temp.unlink(missing_ok=True)
    return {'input_rows': len(df), 'input_sequences': written, 'skipped_rows': skipped}


def csv_to_fasta(csv_path: Path, fasta_path: Path, header_fields=HEADER_FIELDS, sequence_field=SEQUENCE_FIELD) -> int:
    return convert(csv_path, fasta_path, header_fields, sequence_field)['input_sequences']


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path(INPUT_DIR))
    parser.add_argument('--output-dir', type=Path, default=Path(OUTPUT_DIR))
    parser.add_argument('--chains', nargs='+', default=list(CHAINS))
    parser.add_argument('--samples', nargs='+', help='样本键（相对目录/样本名），默认全部')
    args = parser.parse_args()
    files, _, _ = select_files(args.input_dir, args.chains, args.samples)
    failed = 0
    for item in files:
        relative = Path(item['source_file'])
        try:
            print(relative, convert(args.input_dir / relative, args.output_dir / relative.with_suffix('.fasta')))
        except (ValueError, OSError) as exc:
            failed += 1
            print(f'ERROR {relative}: {exc}')
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
