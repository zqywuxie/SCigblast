"""Output layout resolution is stable across stages and old task revisions."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from pipeline_input_manifest import assign, scan, pig_output_relative


class OutputLayoutTests(unittest.TestCase):
    def test_compact_layout_renamed_samples_and_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=root/'input';sample=raw/'K267-B-before';sample.mkdir(parents=True)
            for side in ('j','v'):(sample/f'K267-B-before_{side}.fq.gz').write_bytes(b'test input')
            rows=scan(raw,{})
            selections=[{'id':rows[0]['id'],'sample_id':'K267-B-before','chains':['TRA','TRB']}]
            value=assign({'input_path':str(raw),'rows':rows},selections,'pig_igblast')
            self.assertEqual(value['output_layout'],'sample-v1')
            manifest=root/'new.json';manifest.write_text(json.dumps(value))
            args=('K267-B-before','K267-B-before',rows[0]['r1'],raw)
            self.assertEqual(pig_output_relative(*args,str(manifest)),Path('K267-B-before'))
            self.assertEqual(pig_output_relative(*args),Path('K267-B-before/K267-B-before/K267-B-before'))
            old=dict(value);old.pop('output_layout');legacy=root/'old.json';legacy.write_text(json.dumps(old))
            self.assertEqual(pig_output_relative(*args,str(legacy)),Path('K267-B-before/K267-B-before/K267-B-before'))
            selections[0]['sample_id']='Renamed'
            renamed=assign({'input_path':str(raw),'rows':rows},selections,'pig_igblast')
            renamed_path=root/'renamed.json';renamed_path.write_text(json.dumps(renamed))
            self.assertEqual(pig_output_relative('Renamed',args[1],args[2],raw,str(renamed_path)),Path('Renamed'))
            with self.assertRaises(ValueError):pig_output_relative('Unexpected',args[1],args[2],raw,str(renamed_path))


if __name__=='__main__':unittest.main()
