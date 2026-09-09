"""Test task argument construction without loading user configuration."""
import ast
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DatabaseArgsTest(unittest.TestCase):
    def test_tra_uses_explicit_d_and_completed_task_is_reused(self):
        source = Path(__file__).resolve().parents[1] / '05.work_igblastn.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        functions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ns = dict(Path=Path, re=re, os=os, tempfile=tempfile, hashlib=hashlib,
                      subprocess=subprocess, INPUT=root/'input', OUTPUT=root/'output',
                      DB_ROOT=root/'db', VERSION='pig_chain', RAW_INPUT_ROOT=None,
                      BIN='igblastn', THREADS=2, BAD_V={'', '*'})
            exec(compile(functions, str(source), 'exec'), ns)
            fasta = root/'input'/'pair'/'sample'/'pair_merged.fasta'
            fasta.parent.mkdir(parents=True)
            fasta.write_text('>read\nACGT\n')
            for part in ('V', 'D', 'J'):
                index = Path(str(ns['db']('TRA', part)) + '.nin')
                index.parent.mkdir(parents=True, exist_ok=True)
                index.touch()
            def fake_run(args, **kwargs):
                self.assertEqual(args[args.index('-germline_db_D')+1], str(ns['db']('TRA', 'D')))
                Path(args[args.index('-out')+1]).write_text('sequence_id\tv_call\tproductive\nread\tTRAV1\tT\n')
                return subprocess.CompletedProcess(args, 0)
            with patch.object(subprocess, 'run', side_effect=fake_run) as run:
                for _ in range(2):
                    result = ns['task'](('sample', 'pair', 'TRA', '/raw/R1.fq'))
                    self.assertEqual(result['status'], 'OK', result)
                    self.assertEqual(result['output_rows'], 1)
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
