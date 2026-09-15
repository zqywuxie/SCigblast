"""The umi_count.ipynb transformation, adapted to IgBLAST AIRR sequence_id."""
import argparse
import csv
import os
from pathlib import Path


def organize(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + '.tmp')
    rows = 0
    try:
        with source.open(encoding='utf-8-sig', newline='') as src, temp.open('w', encoding='utf-8', newline='') as dst:
            reader = csv.DictReader(src, delimiter='\t')
            fields = reader.fieldnames or []
            key = 'sequence_id' if 'sequence_id' in fields else 'barcode'
            if key not in fields:
                raise ValueError('缺少 sequence_id 或 barcode 列')
            writer = csv.DictWriter(dst, fieldnames=[*fields, *([] if 'umi_count' in fields else ['umi_count'])], delimiter='\t')
            writer.writeheader()
            for index, row in enumerate(reader):
                value = row.get(key) or ''
                number, sep, rest = value.partition('_')
                cdr3, end, count = rest.rpartition('_')
                if not number.isdecimal() or not sep or not end or not cdr3 or not count or any(c.isspace() for c in value):
                    raise ValueError(f'数据行 {index}：无效的 {key}: {value}')
                if 'umi_count' in fields and row['umi_count'] != count:
                    raise ValueError(f'数据行 {index}：已有 umi_count 与 header 不一致')
                row['umi_count'] = count
                writer.writerow(row)
                rows += 1
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return {'id_column': key, 'input_rows': rows, 'output_rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(organize(args.source, args.destination))
