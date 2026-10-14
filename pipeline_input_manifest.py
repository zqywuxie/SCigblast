"""Explicit directory assignments; Match adapters retain their existing CSV schema."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shlex

CHAINS = ('IGH', 'IGK', 'IGL', 'TRA', 'TRB', 'TRD', 'TRG')
MAX_FILES = 20000
MAX_PAIRS = 5000


def read_scan_config(path, config):
    config = dict(config)
    text = Path(path).read_text(encoding='utf-8-sig')
    for key in ('SCIGBLAST_RAW_FASTQ_EXTENSIONS', 'SCIGBLAST_RAW_READ_SUFFIX_PAIRS'):
        match = re.search(r'^' + key + r'=\((.*?)\)', text, re.M | re.S)
        if match:
            config[key] = ','.join(shlex.split(match[1], comments=True))
    return config


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def fingerprint(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def safe_name(value):
    # Match/fastp scripts exchange TSV lines and normalize filesystem components.
    value = str(value).strip()
    if not value or value in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9_.-]+', value) or len(value) > 160:
        raise ValueError('样本名仅支持字母、数字、下划线、点和短横线（最多 160 字符）')
    return value


def scan(root, config):
    root = Path(root).resolve()
    extensions = re.split(r'[,;\s]+', config.get('SCIGBLAST_RAW_FASTQ_EXTENSIONS', '.fq.gz,.fastq.gz,.fq,.fastq'))
    extensions = sorted({'.' + e.strip().lstrip('.') for e in extensions if e.strip()}, key=len, reverse=True)
    suffixes = re.split(r'[,;\s]+', config.get('SCIGBLAST_RAW_READ_SUFFIX_PAIRS', '_R1|_R2,_1|_2,_j|_v'))
    suffixes = [tuple(s.strip() for s in pair.split('|')) for pair in suffixes if pair.strip()]
    if any(len(pair) != 2 or not all(pair) for pair in suffixes):
        raise ValueError('流程读端后缀配置无效')
    groups, invalid, total = {}, [], 0
    seen = set()
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
        for name in sorted(files):
            total += 1
            if total > MAX_FILES:
                raise ValueError('目录超过 20000 个文件，请选择更小的输入目录')
            ext = next((e for e in extensions if name.casefold().endswith(e.casefold())), None)
            if ext is None:
                continue
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                invalid.append({'pair_id': relative, 'error': '文件链接越界或目标不存在', 'valid': False})
                continue
            if resolved in seen:
                invalid.append({'pair_id': relative, 'error': '文件通过多个路径重复引用', 'valid': False})
                continue
            seen.add(resolved)
            stem, parsed = name[:-len(ext)], None
            for pair in suffixes:
                for index, suffix in enumerate(pair):
                    match = re.search(re.escape(suffix) + r'([._-]\d+)?$', stem, re.I)
                    if match:
                        parsed = (stem[:match.start()] + (match.group(1) or ''), 'r1' if index == 0 else 'r2')
                        break
                if parsed:
                    break
            if not parsed:
                invalid.append({'pair_id': relative, 'error': '无法识别 R1/R2 文件名', 'valid': False})
                continue
            pair_stem, side = parsed
            pair_id = (path.parent.relative_to(root) / pair_stem).as_posix()
            item = groups.setdefault(pair_id, {'pair_id': pair_id, 'stem': pair_stem, 'r1': [], 'r2': []})
            item[side].append(str(path))
            if len(groups) + len(invalid) > MAX_PAIRS:
                raise ValueError('候选配对超过 5000，请选择更小的输入目录')
    rows = []
    for pair_id, item in sorted(groups.items()):
        errors = []
        if any(len(item[side]) != 1 for side in ('r1', 'r2')):
            errors.append('R1/R2 缺失或读端重复')
        paths = {side: item[side][0] if len(item[side]) == 1 else '' for side in ('r1', 'r2')}
        versions = {side: fingerprint(Path(path)) for side, path in paths.items() if path}
        if any(v[0] == 0 for v in versions.values()):
            errors.append('FASTQ 文件为空')
        # Allow readable candidates but never pass arbitrary relative names to shell TSVs.
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part in {'.', '..'} for part in Path(pair_id).parts):
            errors.append('配对路径含不支持的字符，请规范文件名/目录名')
        default = re.sub(r'[^A-Za-z0-9_.-]', '_', item['stem'])[:140] or 'sample'
        rows.append({'id': digest(pair_id)[:24], 'pair_id': pair_id, 'sample_id': default,
                     'r1': paths['r1'], 'r2': paths['r2'], 'versions': versions,
                     'valid': not errors, 'error': '；'.join(errors)})
    # Distinguish repeated basenames across folders without merging samples.
    counts = {}
    for row in rows:
        counts[row['sample_id']] = counts.get(row['sample_id'], 0) + 1
    for row in rows:
        if counts[row['sample_id']] > 1:
            row['sample_id'] += '_' + row['id'][:8]
    for item in invalid:
        item.update(id=digest(item['pair_id'])[:24], sample_id='', r1='', r2='', versions={})
    if len(rows) + len(invalid) > MAX_PAIRS:
        raise ValueError('候选配对超过 5000，请选择更小的输入目录')
    return rows + invalid


def verify(value):
    root = Path(value['input_path']).resolve()
    for row in value['rows']:
        for side in ('r1', 'r2'):
            path = Path(row[side])
            if not path.resolve().is_relative_to(root) or not path.is_file() or fingerprint(path) != row['versions'][side]:
                raise ValueError(f"输入文件已改变或不可用，请重新扫描：{row['pair_id']} / {side.upper()}")


def assign(snapshot, selections, pipeline, species='human'):
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_PAIRS:
        raise ValueError('请选择至少一个有效样本')
    by_id = {row['id']: row for row in snapshot['rows']}
    rows, ids, outputs, names = [], set(), set(), set()
    for selection in selections:
        if not isinstance(selection, dict) or not isinstance(selection.get('id'), str):
            raise ValueError('样本选择 ID 无效')
        row = by_id.get(selection.get('id'))
        if not row or not row['valid'] or row['id'] in ids:
            raise ValueError('样本选择无效或重复，请重新扫描')
        ids.add(row['id'])
        name = safe_name(selection.get('sample_id', row['sample_id']))
        if name.casefold() in names:
            raise ValueError('所选样本名称重复，请使用不同名称；多个 lane 不自动合并')
        names.add(name.casefold())
        requested = selection.get('chains', [])
        if not isinstance(requested, list) or any(not isinstance(c, str) for c in requested):
            raise ValueError('Chain 必须是链名称列表')
        chains = [c for c in CHAINS if c in {c.upper() for c in requested}]
        if not chains or set(c.upper() for c in requested) - set(CHAINS):
            raise ValueError(f'{name}：请选择有效 Chain')
        # Pig retains its existing bare pair_id output; reject rather than overwrite.
        output_pair = Path(row['pair_id']).name if pipeline == 'pig_igblast' else row['pair_id']
        if output_pair in outputs:
            raise ValueError('配对名称冲突：Pig 不支持不同目录的同名配对，请分别提交或重命名')
        outputs.add(output_pair)
        rows.append({**row, 'sample_id': name, 'chains': chains, 'output_pair': output_pair})
    value = {'schema_version': 1, 'pipeline': pipeline, 'input_path': snapshot['input_path'],
             'species': 'pig' if pipeline == 'pig_igblast' else species, 'rows': sorted(rows, key=lambda r: r['pair_id'])}
    verify(value)
    return value


def match_rows(path, pipeline, root):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('schema_version') != 1 or value.get('pipeline') != pipeline or Path(value['input_path']).resolve() != Path(root).resolve():
        raise ValueError('目录清单与当前流程或输入根不匹配')
    expected = os.environ.get('SCIGBLAST_INPUT_MANIFEST_HASH')
    if expected and digest(value) != expected:
        raise ValueError('目录清单内容发生变化')
    verify(value)
    return [{'note': str(Path(row['r1']).parent), 'pair_id': row['output_pair'], 'sample_id': row['sample_id'],
             'species': value['species'], 'chain_raw': ','.join(row['chains']), 'igblast_chains': ','.join(row['chains']),
             'barcode_candidate': '', 'barcode_name': '', 'barcode_sequence': '', 'dual_index': '',
             'match_method': 'directory', 'status': 'OK', 'error': '', 'r1_path': row['r1'], 'r2_path': row['r2']} for row in value['rows']]
