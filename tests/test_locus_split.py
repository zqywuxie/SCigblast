"""Real streaming split, selection, atomic publication and resume contracts."""
import csv
from pathlib import Path
from unittest.mock import patch

import pytest

import pipeline_locus_split as split


HEADER = ['sequence_id', 'locus', 'productive', 'v_call', 'j_call', 'junction_aa', 'chain', 'copy']


def table(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t')
        writer.writerow(HEADER)
        writer.writerows(records)
    return path


def fixture(tmp_path):
    source = tmp_path / '05.igblastn_out' / 'set'
    target = tmp_path / '06.locus_split' / 'set'
    a = table(source / '.tasks/A/TRA.tsv', [
        ['a', 'TRA', 'T', 'V1', 'J1', 'CAR', 'TRB', '900'],
        ['b', 'TRB', 'T', 'V2', 'J2', 'CBB', 'TRA', '800'],
        ['c', '', 'T', 'V3', 'J3', 'CCC', 'TRA', '700'],
    ])
    b = table(source / '.tasks/B/TRB.tsv', [['d', 'TRB', 'T', 'V4', 'J4', 'CBD', 'TRA', '600']])
    summary = source / 'chain_summary.csv'
    manifest = tmp_path / '01.match/set/sample_manifest.csv'
    manifest.parent.mkdir(parents=True)
    def write_csv(path, columns, records):
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(records)
    write_csv(summary, ['sample_id', 'pair_id', 'chain', 'status', 'output_path'],
              [['A', 'p1', 'TRA', 'OK', str(a)], ['B', 'p2', 'TRB', 'OK', str(b)],
               ['A', 'p1', 'TRB', 'OK', str(a)]])  # Unselected stale task.
    write_csv(manifest, ['sample_id', 'pair_id', 'igblast_chains', 'status'],
              [['A', 'p1', 'TRA', 'OK'], ['B', 'p2', 'TRB', 'OK']])
    return summary, manifest, target, a, b


def test_split_uses_locus_and_per_sample_selection_and_preserves_airr(tmp_path):
    summary, manifest, target, a, b = fixture(tmp_path)
    before = {path: path.read_bytes() for path in (a, b)}
    assert split.run(summary, target, manifest)
    assert split.ready(summary, target, manifest)
    reports = list(split.rows(target / split.SUMMARY))
    assert [(r['sample_id'], r['chain']) for r in reports] == [('A', 'TRA'), ('B', 'TRB')]
    assert [(r['input_rows'], r['output_rows'], r['excluded_locus_rows'], r['unknown_locus_rows']) for r in reports] == [('3','1','2','1'), ('1','1','0','0')]
    with (target/'A/TRA.tsv').open(encoding='utf-8', newline='') as handle:
        records = list(csv.reader(handle, delimiter='\t'))
    assert records == [HEADER, ['a', 'TRA', 'T', 'V1', 'J1', 'CAR', 'TRB', '900']]
    assert (target/'B/TRB.tsv').is_file()
    assert not (target/'A/TRB.tsv').exists()
    assert {path: path.read_bytes() for path in before} == before


def test_resume_reuses_valid_files_and_repairs_only_changed_or_missing(tmp_path):
    summary, manifest, target, a, b = fixture(tmp_path)
    assert split.run(summary, target, manifest)
    original = (target/'B/TRB.tsv').stat().st_mtime_ns
    with patch.object(split, 'split_task', wraps=split.split_task) as worker:
        assert split.run(summary, target, manifest)
        assert worker.call_count == 0
        (target/'A/TRA.tsv').unlink()
        assert not split.ready(summary, target, manifest)
        assert split.run(summary, target, manifest)
        assert worker.call_count == 1
    assert (target/'B/TRB.tsv').stat().st_mtime_ns == original
    with a.open('a', encoding='utf-8') as handle:
        handle.write('new\tTRA\tT\tV1\tJ1\tCAR\tTRB\t1\n')
    assert not split.ready(summary, target, manifest)
    with patch.object(split, 'split_task', wraps=split.split_task) as worker:
        assert split.run(summary, target, manifest)
        assert worker.call_count == 1
    assert (target/'B/TRB.tsv').stat().st_mtime_ns == original
    assert list(split.rows(target/split.SUMMARY))[0]['output_rows'] == '2'


def test_missing_locus_fails_and_does_not_publish_a_false_success(tmp_path):
    summary, manifest, target, a, _ = fixture(tmp_path)
    assert split.run(summary, target, manifest)
    original = (target/'A/TRA.tsv').read_bytes()
    a.write_text('productive\tv_call\tj_call\tjunction_aa\nT\tV\tJ\tCAR\n', encoding='utf-8')
    assert not split.run(summary, target, manifest)
    assert not split.ready(summary, target, manifest)
    assert (target/'A/TRA.tsv').read_bytes() == original
    report = list(split.rows(target/split.SUMMARY))[0]
    assert report['status'] == 'ERROR' and 'missing locus' in report['error']
    assert report['output_path'] == ''
    assert not list(target.rglob('*.tmp'))


def test_zero_matching_locus_keeps_header_and_no_usable_rows(tmp_path):
    summary, manifest, target, a, _ = fixture(tmp_path)
    table(a, [['x', 'TRD', 'T', 'V', 'J', 'C', 'TRA', 1]])
    assert split.run(summary, target, manifest)
    report = list(split.rows(target/split.SUMMARY))[0]
    assert (report['input_rows'], report['output_rows']) == ('1', '0')
    assert list(split.rows(target/'A/TRA.tsv')) == []
    assert split.ready(summary, target, manifest)


def test_selection_changes_invalidate_summary_without_reusing_unselected_output(tmp_path):
    summary, manifest, target, _, _ = fixture(tmp_path)
    assert split.run(summary, target, manifest)
    text = manifest.read_text(encoding='utf-8')
    manifest.write_text(text.replace('p1,TRA,OK', 'p1,TRB,OK'), encoding='utf-8')
    assert not split.ready(summary, target, manifest)
    assert split.run(summary, target, manifest)
    assert [(r['sample_id'],r['chain']) for r in split.rows(target/split.SUMMARY)] == [('A','TRB'),('B','TRB')]
    # Earlier selected outputs remain physically available but are not in the
    # authoritative summary and must never be counted by downstream discovery.
    assert (target/'A/TRA.tsv').exists()


def test_missing_selected_mapping_and_paths_outside_stage_are_rejected(tmp_path):
    summary, manifest, target, a, _ = fixture(tmp_path)
    a.unlink()
    with pytest.raises(ValueError, match='missing'):
        split.run(summary, target, manifest)
    table(a, [])
    summary.write_text(summary.read_text(encoding='utf-8').replace(str(a), str(manifest)), encoding='utf-8')
    with pytest.raises(ValueError):
        split.run(summary, target, manifest)
