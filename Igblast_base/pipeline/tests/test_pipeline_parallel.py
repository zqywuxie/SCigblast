"""Scheduling invariants that protect result equivalence and bounded memory."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pipeline_parallel import FastaCountCache, bounded_completed


class ParallelTests(unittest.TestCase):
    def test_submission_is_bounded_and_slow_first_task_does_not_hold_progress(self):
        release_first = Event()
        consumed = []

        def inputs():
            for value in range(1000):
                consumed.append(value)
                yield value

        def worker(value):
            if value == 0:
                if not release_first.wait(5):
                    raise TimeoutError("test did not release first task")
            return value * 2

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = bounded_completed(executor, worker, inputs(), 4)
            try:
                first = next(results)
                self.assertNotEqual(first[0], 0)
                self.assertEqual(len(consumed), 5)  # Four slots and one refill.
            finally:
                release_first.set()
            rows = [first, *results]
        self.assertEqual(sorted(rows), [(i, i*2) for i in range(1000)])

    def test_worker_exception_is_propagated(self):
        def worker(value):
            if value == 1:
                raise ValueError("invalid input")
            return value

        with ThreadPoolExecutor(max_workers=2) as executor:
            with self.assertRaisesRegex(ValueError, "invalid input"):
                list(bounded_completed(executor, worker, range(10), 4))

    def test_shared_fasta_scan_and_independent_file_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder)/"a.fasta", Path(folder)/"b.fasta"
            first.write_bytes(b">one\nACGT\n>two\nACGT\n")
            second.write_bytes(b">three\nACGT\n")
            cache, barrier = FastaCountCache(), Barrier(4)
            first_opened, release_first, second_finished = Event(), Event(), Event()
            opened, opened_lock, original_open = [], Lock(), Path.open

            def tracked_open(path, *args, **kwargs):
                with opened_lock:
                    opened.append(path)
                if path == first:
                    first_opened.set()
                    if not release_first.wait(5):
                        raise TimeoutError("test did not release FASTA scan")
                return original_open(path, *args, **kwargs)

            def shared():
                barrier.wait(timeout=5)
                return cache.count(first)

            def separate():
                result = cache.count(second)
                second_finished.set()
                return result

            with patch.object(Path, "open", tracked_open), ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(shared) for _ in range(4)]
                try:
                    self.assertTrue(first_opened.wait(5))
                    other = executor.submit(separate)
                    self.assertTrue(second_finished.wait(5))
                finally:
                    release_first.set()
                self.assertEqual([future.result() for future in futures], [2]*4)
                self.assertEqual(other.result(), 1)
            self.assertEqual(opened.count(first), 1)
            self.assertEqual(opened.count(second), 1)
            self.assertEqual(cache.count(Path(folder)/"missing.fasta"), 0)


if __name__ == "__main__":
    unittest.main()
