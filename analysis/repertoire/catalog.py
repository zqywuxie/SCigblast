"""Explicit workbook columns, sample/group joins, and strict IUPAC matching."""
from __future__ import annotations

import re
import hashlib
import zipfile
from pathlib import Path

from .io import digest,version

IUPAC = dict(A='A', C='C', G='G', T='T', R='AG', Y='CT', S='GC', W='AT', K='GT',
             M='AC', B='CGT', D='AGT', H='ACT', V='ACG', N='ACGT')


def column(value):
    value = str(value).strip().upper()
    if not re.fullmatch('[A-Z]{1,3}', value):
        raise ValueError('列须用 Excel 字母表示，例如 B、I')
    result = 0
    for letter in value:
        result = result * 26 + ord(letter) - 64
    return result - 1


def workbook(path, sheet=None):
    """Bounded parsing; inherit only actual merged ranges, never arbitrary blanks."""
    path = Path(path)
    if path.stat().st_size > 20 * 1024**2:
        raise ValueError('工作簿不能超过 20 MiB')
    if path.suffix.lower() == '.xls':
        import xlrd
        book = xlrd.open_workbook(path, formatting_info=True, on_demand=True)
        try:
            names = book.sheet_names()
            selected = book.sheet_by_name(sheet or names[0])
            if selected.nrows * selected.ncols > 500000:
                raise ValueError('工作表超过 500000 个单元格')
            rows = [[str(selected.cell_value(r, c)).strip() for c in range(selected.ncols)] for r in range(selected.nrows)]
            merged = [(r0, r1, c0, c1) for r0, r1, c0, c1 in selected.merged_cells]
            name = selected.name
        finally:
            book.release_resources()
    elif path.suffix.lower() == '.xlsx':
        import openpyxl
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 100 * 1024**2:
                raise ValueError('工作簿展开后超过 100 MiB')
        # Read Excel's saved values, including cached formula results. openpyxl
        # never evaluates formulas; unrelated formula cells must not reject a table.
        book = openpyxl.load_workbook(path, read_only=False, data_only=True, keep_links=False)
        try:
            names = book.sheetnames
            selected = book[sheet or names[0]]
            if selected.max_row * selected.max_column > 500000:
                raise ValueError('工作表超过 500000 个单元格')
            rows = []
            for cells in selected:
                rows.append(['' if cell.value is None else str(cell.value).strip() for cell in cells])
            merged = [(r.min_row-1, r.max_row, r.min_col-1, r.max_col) for r in selected.merged_cells.ranges]
            name = selected.title
        finally:
            book.close()
    else:
        raise ValueError('仅支持 XLS/XLSX 工作簿')
    return dict(sheets=names, sheet=name, rows=rows, merged=merged)


def value(book, row, col, *, inherit=False):
    if inherit:
        for r0, r1, c0, c1 in book['merged']:
            if r0 <= row < r1 and c0 <= col < c1:
                row, col = r0, c0
                break
    return book['rows'][row][col] if row < len(book['rows']) and col < len(book['rows'][row]) else ''


def workbook_preview(book, header_row=1):
    """Small raw preview with the merged values needed to check group columns."""
    start=header_row-1
    stop=min(len(book['rows']),start+11)
    merged=[dict(rows=[r0,r1],columns=[c0,c1],value=value(book,r0,c0))
            for r0,r1,c0,c1 in book['merged'] if r0<stop and r1>start]
    return dict(sheets=book['sheets'],sheet=book['sheet'],preview=book['rows'][start:stop],
                preview_start=start,row_count=len(book['rows']),
                column_count=max((len(row) for row in book['rows']),default=0),merged=merged)


def build_catalog(submission, primers, config, samples):
    from .matching import review_settings
    enabled, threshold = review_settings(config)
    config = dict(config, try_alternative_groups=enabled, primer_match_threshold=threshold)
    source_versions={name:version(path) for name,path in (('submission',submission),('primers',primers))}
    sub = workbook(submission, config.get('submission_sheet'))
    book = workbook(primers, config.get('primer_sheet'))
    si, sj, pj = (column(config.get(key, default)) for key, default in (
        ('sample_column', 'B'), ('submission_join_column', 'I'), ('primer_join_column', 'B')))
    pairs = config.get('primer_columns', [['C', 'D'], ['E', 'F']])
    if not pairs or len(pairs) > 12:
        raise ValueError('请选择 1–12 对引物名称/序列列')
    pairs = [(column(a), column(b)) for a, b in pairs]
    start_s, start_p = int(config.get('submission_header_row', 1)), int(config.get('primer_header_row', 1))
    if min(start_s, start_p) < 1:
        raise ValueError('表头行从 1 开始')
    adapter = config.get('adapter', 'ACACTCTTTCCCTACACGACGCTCTTCCGATCT').upper()
    umi, barcode = int(config.get('umi_length', 10)), int(config.get('barcode_length', 8))
    if not 0 <= umi <= 100 or not 0 <= barcode <= 100 or set(adapter) - set('ACGT'):
        raise ValueError('Adapter/UMI/Barcode 结构参数无效')
    grouped, names = {}, {}
    for r in range(start_p, len(book['rows'])):
        group = value(book, r, pj, inherit=True)
        for ni, qi in pairs:
            name, full = value(book, r, ni), re.sub(r'\s+', '', value(book, r, qi)).upper()
            if not name and not full:
                continue
            if not group or not name or not full:
                raise ValueError(f'引物表第 {r+1} 行缺少组、名称或序列')
            seq = full
            if config.get('sequence_mode', 'oligo') == 'oligo':
                prefix = adapter + 'N' * umi
                if not full.startswith(prefix) or len(full) <= len(prefix) + barcode:
                    raise ValueError(f'{name} 不符合指定 adapter + UMI + barcode 结构')
                seq = full[len(prefix)+barcode:]
            if not seq or set(seq) - IUPAC.keys():
                raise ValueError(f'{name} 含有非 IUPAC 碱基')
            if name in names and names[name] != seq:
                raise ValueError(f'同名引物有不同序列：{name}；请先区分变体名称')
            names[name] = seq
            item = dict(name=name, sequence=seq, group=group, row=r+1, full_sequence=full)
            if not any(x['name'] == name and x['sequence'] == seq for x in grouped.setdefault(group, [])):
                grouped[group].append(item)
    assignments = {}
    aliases = config.get('sample_aliases', {})
    wanted = {aliases.get(sample, sample) for sample in samples}
    for r in range(start_s, len(sub['rows'])):
        sample, group = value(sub, r, si), value(sub, r, sj, inherit=True)
        if not sample and not group:
            continue
        if not sample:
            raise ValueError(f'Submission 第 {r+1} 行缺少样本身份')
        if sample not in wanted:
            continue
        if not group:
            continue
        sep = config.get('group_separator', '')
        groups = [s.strip() for s in group.split(sep)] if sep else [group]
        for g in groups:
            candidates = grouped.get(g)
            if candidates is None and config.get('allow_primer_name_join'):
                candidates = [item for values in grouped.values() for item in values if item['name'] == g]
            if not candidates:
                raise ValueError(f'{sample} 对应的引物组不存在：{g}')
            existing = assignments.setdefault(sample, {})
            for item in candidates:
                existing[item['name']] = item
    result, missing = {}, []
    for sample in samples:
        lookup = aliases.get(sample, sample)
        if lookup not in assignments:
            missing.append(sample)
        else:
            result[sample] = sorted(assignments[lookup].values(), key=lambda row: row['name'])
    if missing:
        raise ValueError('以下样本未连接到引物组，请检查样本列或设置样本对应：' + '、'.join(missing[:20]))
    evidence={}
    for name,path in (('submission',submission),('primers',primers)):
        with Path(path).open('rb') as incoming:sha=hashlib.file_digest(incoming,'sha256').hexdigest()
        if version(path)!=source_versions[name]:raise ValueError('工作簿在解析期间发生变化，请重新导入')
        evidence[name]=dict(sha256=sha,source_version=source_versions[name],filename=Path(path).name)
    return dict(assignments=result, groups=grouped, config=config,
                version=digest([result, grouped, config]), workbooks=evidence)


class Matcher:
    def __init__(self, primers):
        grouped = {}
        for row in primers:
            grouped.setdefault(row['sequence'], set()).add(row['name'])
        self.patterns = [(seq, sorted(names), re.compile('^' + ''.join('[' + IUPAC[b] + ']' for b in seq))) for seq, names in grouped.items()]

    def match(self, sequence):
        names, families = [], []
        sequence = sequence.upper()
        for sequence_key, aliases, pattern in self.patterns:
            if pattern.match(sequence):
                names.extend(aliases)
                families.append(sequence_key)
        names = sorted(set(names))
        return names, families
