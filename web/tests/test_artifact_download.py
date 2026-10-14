import io
import random
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from artifact_download import stream_zip


class StreamZipTests(unittest.TestCase):
    def test_incompressible_content_is_yielded_in_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.tsv'
            content = random.Random(42).randbytes(1024 * 1024)
            path.write_bytes(content)
            chunks = list(stream_zip([(path, 'batch/sample.tsv')], chunk_size=64 * 1024))
            self.assertGreater(len(chunks), 10)
            self.assertLess(max(map(len, chunks)), 128 * 1024)
            with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as archive:
                self.assertEqual(archive.read('batch/sample.tsv'), content)


if __name__ == '__main__':
    unittest.main()
