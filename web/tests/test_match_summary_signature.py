import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web


class MatchSummarySignatureTests(unittest.TestCase):
    def test_signature_changes_when_summary_is_replaced_or_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample_manifest.csv"
            path.write_text("sample_id,status\nA,OK\n", encoding="utf-8")
            first = web.summary_file_signature([str(path)])
            path.write_text("sample_id,status\nA,ERROR\n", encoding="utf-8")
            second = web.summary_file_signature([str(path)])
            self.assertNotEqual(first, second)
            self.assertEqual(web.summary_file_signature([str(path) + ".missing"])[0][1:], [None, None, None])


if __name__ == "__main__":
    unittest.main()
