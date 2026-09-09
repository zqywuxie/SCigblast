import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ir_rep', Path(__file__).resolve().parents[1] / '06.representative.py')
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)


class SourceLayoutTests(unittest.TestCase):
    def test_same_name_and_umi_are_independent_and_legacy_is_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); panda=root/'05.pandaseq'/'dataset'; out=root/'06.representative'/'dataset'
            for batch, seq in [('batchA','AAAA'),('batchB','CCCC')]:
                folder=panda/batch/'pair'/'S';folder.mkdir(parents=True)
                (folder/'reads_merged.fasta').write_text(f'>read1#ACGT\n{seq}\n')
            with patch.multiple(rep, PANDASEQ_DIR=panda, SPLIT_DIR=root/'split', OUTPUT_DIR=out,
                                FASTA_OUTPUT_DIR=out/'representative_fasta'):
                out.mkdir(parents=True)
                rep.write_state([dict(sample_id='S', umi='ACGT', sequence='AAAA', header='read1#ACGT')],out/rep.STATE_NAME)
                (out/'representative_map.tsv.gz').touch()
                with patch.object(rep,'summary_sources',return_value={}), patch.object(rep,'sidecar_index',return_value={}):
                    for _ in range(2):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(rep.main(),0)
                        rows=rep.load_state(out/rep.STATE_NAME)
                        self.assertEqual(len(rows),2)
                        self.assertEqual({r['sample_key'] for r in rows},{'batchA/pair/S','batchB/pair/S'})
                        for batch, seq in [('batchA','AAAA'),('batchB','CCCC')]:
                            self.assertIn(seq,(out/'representative_fasta'/batch/'pair/S/representative.fasta').read_text())
                self.assertEqual(len(list(out.parent.glob('dataset.legacy_flat.*'))),1)
                self.assertFalse((out/'representative_fasta/S').exists())
