from __future__ import annotations

import csv
import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image
from pandas.testing import assert_frame_equal

from analysis.treemap.aggregate import load_clones
from analysis.treemap.render import save_combined_treemap, save_treemap
from analysis.treemap.run import run_request
from analysis.treemap.clone_table import FIELDS, write_clone_table, merge_clone_tables

SOURCE_TREEMAP = Path("E:/Desktop/treemap_code/treemap_g.py")


def test_load_clones_filters_locus_and_exact_productive_and_counts_rows(tmp_path: Path) -> None:
    source = tmp_path / "mixed.csv"
    source.write_text(
        "locus,productive,v_call,j_call,junction_aa,cdr3_aa,copy\n"
        "TRA,T,TRAV1,TRAJ1,CASSA,ignored,900\n"
        "TRA,T,TRAV1,TRAJ1,CASSA,ignored,2\n"
        "TRB,T,TRBV1,TRBJ1,CASSB,ignored,4\n"
        "TRA,t,TRAV1,TRAJ1,CASSC,ignored,8\n"
        "TRA,F,TRAV1,TRAJ1,CASSD,ignored,10\n"
        "TRA,T,TRAV1,TRAJ1,,ignored,1\n",
        encoding="utf-8",
    )

    clones, productive_count = load_clones([{"path": str(source)}], "TRA")

    assert productive_count == 2
    assert clones.to_dict("records") == [
        {"V": "TRAV1", "J": "TRAJ1", "CDR3(pep)": "CASSA", "copy": 2}
    ]


@pytest.mark.skipif(not SOURCE_TREEMAP.is_file(), reason="reference source tree is unavailable")
def test_aggregate_matches_original_across_chunks_and_preserves_raw_text(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("reference_treemap_g", SOURCE_TREEMAP)
    assert spec is not None and spec.loader is not None
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    reference.READ_CHUNK_SIZE = 2
    reference.MERGE_EVERY_N_CHUNKS = 2

    rows = [
        {"locus": "TRA", "productive": "T", "v_call": " TRAV1 ", "j_call": "TRAJ1", "junction_aa": " CASS ", "copy": "900"},
        {"locus": "TRA", "productive": "T", "v_call": " TRAV1 ", "j_call": "TRAJ1", "junction_aa": " CASS ", "copy": "1"},
        {"locus": "TRA", "productive": "T", "v_call": "TRAV2", "j_call": "TRAJ2", "junction_aa": "nan", "copy": "0"},
        {"locus": "TRA", "productive": "T", "v_call": "TRAV3", "j_call": "TRAJ3", "junction_aa": " ", "copy": "-3"},
        {"locus": "TRA", "productive": "T", "v_call": "TRAV4", "j_call": "TRAJ4", "junction_aa": "", "copy": "4"},
        {"locus": "TRA", "productive": "t", "v_call": "TRAV5", "j_call": "TRAJ5", "junction_aa": "bad", "copy": "10"},
    ]
    rows.extend(
        {
            "locus": "TRA",
            "productive": "T",
            "v_call": f"TRAV{index % 3}",
            "j_call": f"TRAJ{index % 2}",
            "junction_aa": f"CDR{index % 4}",
            "copy": str(index * 100),
        }
        for index in range(20)
    )
    source = tmp_path / "parity.csv"
    pd.DataFrame(rows).to_csv(source, index=False)

    expected, expected_count = reference.load_chain_clones(source)
    actual, actual_count = load_clones([{"path": str(source)}], "TRA")

    expected = expected.rename(columns={"v_call": "V", "j_call": "J", "junction_aa": "CDR3(pep)"})
    assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True), check_dtype=False)
    assert actual_count == expected_count
    assert " CASS " in set(actual["CDR3(pep)"])
    assert "nan" in set(actual["CDR3(pep)"])
    assert " " in set(actual["CDR3(pep)"])


@pytest.mark.skipif(not SOURCE_TREEMAP.is_file(), reason="reference source tree is unavailable")
def test_qr_pixels_match_original_reference_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("reference_treemap_g_pixels", SOURCE_TREEMAP)
    assert spec is not None and spec.loader is not None
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    reference.TREEMAP_SIZE = 64
    reference.TREEMAP_DPI = (72, 72)
    reference.V_COL = "V"
    reference.J_COL = "J"
    reference.CDR3_COL = "CDR3(pep)"
    reference.COPY_COL = "copy"
    monkeypatch.setattr("analysis.treemap.render.TREEMAP_SIZE", 64)
    monkeypatch.setattr("analysis.treemap.render.TREEMAP_DPI", (72, 72))
    clones = pd.DataFrame(
        [
            {"V": "TRAV1", "J": "TRAJ1", "CDR3(pep)": "CASS", "copy": 8},
            {"V": "TRAV1", "J": "TRAJ1", "CDR3(pep)": "CATS", "copy": 3},
            {"V": "TRAV2", "J": "TRAJ2", "CDR3(pep)": "CAAS", "copy": 2},
            {"V": "TRAV2", "J": "TRAJ1", "CDR3(pep)": "CAGS", "copy": 1},
        ]
    )
    expected_path, actual_path = tmp_path / "reference.png", tmp_path / "actual.png"
    reference.generate_qr_treemap(clones, expected_path)
    save_treemap(clones, actual_path)
    with Image.open(expected_path) as expected, Image.open(actual_path) as actual:
        assert expected.size == actual.size == (64, 64)
        assert list(expected.getdata()) == list(actual.getdata())


def test_load_clones_uses_cdr3_aa_and_skips_known_other_chain(tmp_path: Path) -> None:
    source = tmp_path / "chain.tsv"
    source.write_text(
        "productive\tv_call\tj_call\tcdr3_aa\nT\tTRBV1\tTRBJ1\tCASSB\n",
        encoding="utf-8",
    )
    clones, count = load_clones([{"path": str(source), "chain": "TRB"}], "TRA")
    assert clones.empty
    assert count == 0

    clones, count = load_clones([{"path": str(source), "chain": "TRB"}], "TRB")
    assert count == 1
    assert clones.iloc[0]["CDR3(pep)"] == "CASSB"
    assert clones.iloc[0]["copy"] == 1


def test_combined_sources_check_locus_and_merge_known_chain_sources(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text(
        "locus,productive,v_call,j_call,junction_aa\nTRA,T,TRAV1,TRAJ1,CASS\nTRB,T,TRBV1,TRBJ1,CASB\n",
        encoding="utf-8",
    )
    second.write_text(
        "productive,v_call,j_call,junction_aa\nT,TRAV1,TRAJ1,CASS\n",
        encoding="utf-8",
    )
    clones, count = load_clones(
        [{"path": str(first)}, {"path": str(second), "chain": "TRA"}],
        "TRA",
    )
    assert count == 2
    assert clones.iloc[0]["copy"] == 2
    known, count = load_clones([{"path": str(first), "chain": "TRA"}], "TRA")
    assert count == 2
    assert known["copy"].sum() == 2


def test_save_treemap_writes_png_and_copy_weights_area(tmp_path: Path) -> None:
    clones = pd.DataFrame(
        [
            {"V": "TRAV1", "J": "TRAJ1", "CDR3(pep)": "CASSA", "copy": 3},
            {"V": "TRAV1", "J": "TRAJ1", "CDR3(pep)": "CASSB", "copy": 1},
        ]
    )
    output = tmp_path / "nested" / "treemap.png"
    save_treemap(clones, output)
    with Image.open(output) as image:
        assert image.size == (3000, 3000)
        assert image.info.get("dpi", (0, 0))[0] >= 299
        assert len(image.getcolors(maxcolors=3000 * 3000) or []) > 2


def test_save_combined_treemap_uses_black_gaps_and_chain_panels(tmp_path: Path) -> None:
    chain_image = tmp_path / "TRA.png"
    Image.new("RGB", (30, 20), "red").save(chain_image)
    output = tmp_path / "combined.png"
    save_combined_treemap({"TRA": chain_image}, output)
    with Image.open(output) as image:
        assert image.size == (3000, 3000)
        assert image.getpixel((0, 0)) == (0, 0, 0)
        assert image.getpixel((20, 20)) == (255, 0, 0)


def test_run_request_cli_writes_atomic_result_json(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text(
        "locus,productive,v_call,j_call,junction_aa\nTRA,T,TRAV1,TRAJ1,CASSA\n",
        encoding="utf-8",
    )
    target = tmp_path / "out.png"
    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(
        json.dumps({"kind": "chain", "sources": [{"path": str(source)}], "chain": "TRA", "target": str(target)}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "analysis.treemap.run", "--request", str(request), "--output", str(result)],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["productive_count"] == 1
    assert payload["clone_count"] == 1
    assert payload["empty"] is False
    assert Path(payload["path"]).is_file()


def test_empty_chain_request_returns_successful_empty_result(tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_text(
        "locus,productive,v_call,j_call,junction_aa\nTRA,F,TRAV1,TRAJ1,CASSA\n",
        encoding="utf-8",
    )
    result = run_request(
        {"kind": "chain", "sources": [{"path": str(source)}], "chain": "TRA", "target": str(tmp_path / "unused.png")}
    )
    assert result == {"empty": True, "productive_count": 0, "clone_count": 0, "path": None}


def test_parallel_workers_match_serial_images_and_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        monkeypatch.setenv(key, '1')
    source = tmp_path / 'source.tsv'
    source.write_text('productive\tv_call\tj_call\tjunction_aa\nT\tV1\tJ1\tCAR\nT\tV2\tJ1\tCAT\n', encoding='utf-8')
    units = [dict(kind='chain', sources=[dict(path=str(source), chain=chain)], chain=chain,
                  target=str(tmp_path / f'{chain}.png'),clone_table_target=str(tmp_path/f'{chain}.csv'),
                  sample_key='batch/sample',sample_label='sample') for chain in ('IGH', 'TRB')]
    parallel = run_request(dict(kind='batch', units=units, work_dir=str(tmp_path),
                                worker_rss_mb=1024, timeout_seconds=60))
    assert len({entry['pid'] for entry in parallel['results']}) == 2
    for unit, entry in zip(units, parallel['results']):
        serial_target = tmp_path / f"serial-{unit['chain']}.png"
        serial_table = tmp_path/f"serial-{unit['chain']}.csv"
        serial = run_request(dict(unit, target=str(serial_target),clone_table_target=str(serial_table)))
        assert entry['result']['productive_count'] == serial['productive_count'] == 2
        assert entry['result']['clone_count'] == serial['clone_count'] == 2
        assert Path(unit['target']).read_bytes() == serial_target.read_bytes()
        assert Path(unit['clone_table_target']).read_bytes() == serial_table.read_bytes()
    assert not list(tmp_path.glob('batch-*'))


def test_clone_table_ranking_percentages_ties_and_raw_identities(tmp_path: Path) -> None:
    clones=pd.DataFrame([
        {'V':'V2','J':'J1','CDR3(pep)':'CATS','copy':1},
        {'V':'V1','J':'J1','CDR3(pep)':'CAR,\nX','copy':3},
        {'V':' V1 ','J':'J1','CDR3(pep)':'CAT','copy':1},
    ])
    original=clones.copy(deep=True)
    target=tmp_path/'clones.csv'
    write_clone_table(clones,5,target,sample_key='batch/样本',sample_label='样本',chain='TRB')
    with target.open(encoding='utf-8-sig',newline='') as source:
        rows=list(csv.DictReader(source))
    assert [row['rank'] for row in rows]==['1','2','3']
    assert [row['record_count'] for row in rows]==['3','1','1']
    assert [row['v_call'] for row in rows]==['V1',' V1 ','V2']
    assert rows[0]['cdr3_aa']=='CAR,\nX'
    assert [float(row['frequency_percent']) for row in rows]==[60,20,20]
    assert [float(row['cumulative_frequency_percent']) for row in rows]==[60,80,100]
    assert rows[0]['sample_key']=='batch/样本'
    assert_frame_equal(clones,original)
    # Combining is streamed and preserves quoted/multiline values and one header.
    merged=tmp_path/'clone_top.csv'
    merge_clone_tables([target,target],merged)
    with merged.open(encoding='utf-8-sig',newline='') as source:
        combined=list(csv.DictReader(source))
    assert combined==rows+rows
    assert merged.read_bytes().count(b'\xef\xbb\xbf')==1


def test_empty_clone_table_and_failed_merge_preserve_published_file(tmp_path: Path) -> None:
    empty=pd.DataFrame(columns=['V','J','CDR3(pep)','copy'])
    target=tmp_path/'empty.csv'
    write_clone_table(empty,0,target,chain='IGH')
    with target.open(encoding='utf-8-sig',newline='') as source:
        assert list(csv.reader(source))==[list(FIELDS)]
    published=tmp_path/'clone_top.csv'
    merge_clone_tables([target],published)
    before=published.read_bytes()
    bad=tmp_path/'bad.csv';bad.write_text('wrong,header\n',encoding='utf-8')
    with pytest.raises(ValueError):
        merge_clone_tables([target,bad],published)
    assert published.read_bytes()==before
    assert not list(tmp_path.glob('*.tmp'))
