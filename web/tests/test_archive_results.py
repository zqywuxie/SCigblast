import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive_results as archive


class ArchiveTests(unittest.TestCase):
    def test_complete_package_keeps_summaries_and_removes_bulk(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'reads.fastq').write_bytes(b'ACGT' * 10000)
            (root / 'summary.csv').write_text('sample,reads\nA,100\n')
            archive.package(root, 'job')
            self.assertFalse((root / 'reads.fastq').exists())
            self.assertTrue((root / 'summary.csv').exists())
            manifest = json.loads((root / archive.MANIFEST).read_text())
            archive.verify(root / archive.ARCHIVE, manifest['entries'])
            archive.package(root, 'job')  # interrupted cleanup is repeatable

    def test_failed_verification_does_not_delete_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'reads.fastq'
            source.write_bytes(b'ACGT' * 10000)
            with patch.object(archive, 'verify', side_effect=ValueError('bad archive')):
                with self.assertRaises(ValueError):
                    archive.package(root, 'job')
            self.assertEqual(source.read_bytes(), b'ACGT' * 10000)
            self.assertFalse((root / archive.ARCHIVE).exists())

    def test_changed_source_is_preserved_on_cleanup_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'reads.fastq'
            source.write_bytes(b'ACGT' * 10000)
            archive.package(root, 'job')
            source.write_bytes(b'new data')
            with self.assertRaises(ValueError):
                archive.package(root, 'job')
            self.assertEqual(source.read_bytes(), b'new data')

    def test_only_old_successes_are_eligible(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        for status in ('RUNNING', 'WAITING_REVIEW', 'FAILED', 'STOPPED', 'ARCHIVED'):
            self.assertFalse(archive.eligible({'status': status, 'ended_at': (cutoff-timedelta(days=1)).isoformat()}, cutoff))
        self.assertTrue(archive.eligible({'status': 'SUCCEEDED', 'ended_at': cutoff.isoformat()}, cutoff))
        self.assertFalse(archive.eligible({'status': 'SUCCEEDED', 'ended_at': (cutoff+timedelta(seconds=1)).isoformat()}, cutoff))
