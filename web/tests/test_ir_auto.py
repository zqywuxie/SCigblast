"""Pure pipeline-function tests, without loading production configs or tools."""
import ast
from collections import Counter, defaultdict
import gzip
import hashlib
import os
from pathlib import Path
import re
import tempfile
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import unicodedata

ROOT = Path(__file__).resolve().parents[2]


def functions(path, names):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(selected) == len(names)
    module = ast.Module(body=ast.parse('from __future__ import annotations').body + selected, type_ignores=[])
    ns = dict(Counter=Counter, defaultdict=defaultdict, gzip=gzip, hashlib=hashlib, re=re,
              UMI_HEADER_RE=re.compile(r'#(?:UMI:)?([ACGTN]+)$', re.I))
    exec(compile(module, str(path), 'exec'), ns)
    return ns


class PipelinePolicyTests(unittest.TestCase):
    def test_presplit_metadata_can_omit_barcode_but_raw_cannot(self):
        ns = functions('IR_split/pipeline/01.match_sample.py', ['load_metadata',
                       'normalize_text', 'display_text', 'normalize_barcode_name', 'resolve_optional_column'])
        ns.update(os=os, unicodedata=unicodedata, NO_BARCODE=False,
                  MetadataRecord=SimpleNamespace,
                  SubmissionRecord=lambda *values: SimpleNamespace(**dict(zip(
                      ['sample_id', 'barcode_candidate', 'barcode_name', 'barcode_sequence',
                       'chain', 'igblast_chain', 'chain_expanded'], values))),
                  normalize_chain=lambda raw: (raw, 'TRA,TRB', 'TRA,TRB'),
                  read_xlsx_rows=lambda *_: [['Sample', 'Dual Index', 'Chain', 'Note'],
                                             ['A', 'A01', 'T', '/raw']])
        args = (Path('metadata.xlsx'), None, 'Sample', 'Barcode', 'Dual Index', 'Note', 'Chain', {})
        with patch.dict(os.environ, SCIGBLAST_IR_INPUT_MODE='auto'):
            result = ns['load_metadata'](*args)
            self.assertEqual(result[0].sample.barcode_sequence, '')
            self.assertEqual(result[0].sample.sample_id, 'A')
        with patch.dict(os.environ, SCIGBLAST_IR_INPUT_MODE='raw'):
            with self.assertRaises(ValueError):
                ns['load_metadata'](*args)

    def test_auto_pair_detection(self):
        ns = functions('IR_split/pipeline/03.split_barcode.py',
                       ['open_fastq', 'read_fastq_record', 'header_umi', 'detect_pair_mode'])
        with tempfile.TemporaryDirectory() as tmp:
            pair = SimpleNamespace(r1=Path(tmp)/'r1.fq.gz', r2=Path(tmp)/'r2.fq.gz')
            def check(tags1, tags2):
                for path, tags in [(pair.r1, tags1), (pair.r2, tags2)]:
                    path.write_text(''.join(f'@read{i}{tag} 1:N:0:INDEX\nACGT\n+\nIIII\n'
                                            for i, tag in enumerate(tags)))
                return ns['detect_pair_mode'](pair, 10)
            self.assertEqual(check([''], ['']), 'raw')
            self.assertEqual(check(['#TGAATACAAT'], ['#TGAATACAAT']), 'presplit')
            self.assertEqual(check(['#UMI:TGAATACAAT'], ['#UMI:TGAATACAAT']), 'presplit')
            for left, right in [(['#TGAATACAAT'], ['']), (['#ACGT'], ['#ACGT']),
                                (['#TGAATACAAT'], ['#AAAAAAAAAA']),
                                (['', '#TGAATACAAT'], ['', '#TGAATACAAT']), ([], [])]:
                with self.assertRaises(ValueError):
                    check(left, right)

    def test_runner_forces_full_flow_and_requires_preprocessing(self):
        git = shutil.which('git')
        git_bash = Path(git).resolve().parents[1] / 'bin/bash.exe' if git else Path('missing')
        bash = str(git_bash) if git_bash.is_file() else shutil.which('bash')
        if not bash:
            self.skipTest('Bash is unavailable')
        script = (ROOT / 'IR_split/pipeline/run_ir_pipeline.sh').read_text(encoding='utf-8')
        policy = script[script.index('# The IR pipeline always'):script.index('resolve_output_root()')]
        result = subprocess.run([bash, '-c', policy + '\nprintf "%s/%s/%s" "$INPUT_MODE" "$SCIGBLAST_IR_PIPELINE_VARIANT" "$RUN_PREPROCESSING"'],
                                env={**os.environ, 'SCIGBLAST_IR_PIPELINE_VARIANT': 'merged',
                                     'SCIGBLAST_IR_INPUT_MODE': 'raw', 'SCIGBLAST_RUN_INPUT_MODE': 'presplit',
                                     'SCIGBLAST_IR_RUN_PREPROCESSING': '0', 'SCIGBLAST_RUN_PREPROCESSING': '0'},
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, 'auto/representative/1')
        start = script.index('final_outputs_ready() {')
        function = script[start:script.index('\n}', start) + 2]
        # IgBLAST artifacts exist, but a missing preprocessing checkpoint must
        # still prevent the runner from publishing its overall DONE marker.
        harness = function + '\nfind() { echo TCR.tsv; }\nstage_done() { test "$1" = 08.preprocessing; return "$READY"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'chain_summary.csv').write_text('summary')
            for ready in ('0', '1'):
                result = subprocess.run([bash, '-c', harness + 'final_outputs_ready'],
                                    env={**os.environ, 'IGBLAST_OUTPUT_DIR': '.', 'READY': ready},
                                    capture_output=True, text=True, cwd=tmp)
                self.assertEqual(result.returncode, int(ready), result.stderr)

    def test_both_representatives_count_first_then_quality(self):
        ir = functions('IR_split/pipeline/06.representative.py', ['representative_rows'])['representative_rows']
        ten = functions('10X_split/pipeline/06.split_and_represent.py', ['process_umi_group'])['process_umi_group']
        for records, expected, status in [
            ([('r1','AAAA','20','1'), ('r2','AAAA','20','1'), ('r3','CCCC','40','.01')], 'AAAA', 'CLEAR'),
            ([('r1','AAAA','20','1'), ('r2','CCCC','40','.01')], 'CCCC', 'RESOLVED_BY_QUALITY'),
            ([('r1','AAAA','',''), ('r2','CCCC','','')], 'AAAA', 'TIED_NO_QUALITY'),
            ([('r1','AAAA','40','.01'), ('r2','CCCC','40','.01')], 'AAAA', 'TIED_AFTER_QUALITY'),
        ]:
            counts, quality, headers = Counter(), {}, {}
            for header, seq, mean, errors in records:
                counts[seq] += 1
                headers[('UMI', seq)] = header
                if mean:
                    q = quality.setdefault(('UMI', seq), [0, 0, 0])
                    q[0] += float(mean); q[1] += float(errors); q[2] += 1
            ir_rows = ir('sample', {'UMI': counts}, headers, quality, set())
            ten_rows = ten('UMI', records)['sequences']
            for rows in [ir_rows, ten_rows]:
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['sequence'], expected)
                self.assertEqual(rows[0]['ambiguity_status'], status)
