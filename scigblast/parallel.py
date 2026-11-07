"""Bounded task submission and per-invocation FASTA counts for pipeline stages."""
from concurrent.futures import FIRST_COMPLETED, Future, wait
from pathlib import Path
from threading import Lock


def bounded_completed(executor, function, items, max_pending):
    """Yield (input index, result) as tasks finish, retaining a bounded window."""
    if max_pending < 1:
        raise ValueError("max_pending must be positive")
    remaining = iter(enumerate(items))
    pending = {}

    def submit_next():
        try:
            index, item = next(remaining)
        except StopIteration:
            return False
        pending[executor.submit(function, item)] = index
        return True

    for _ in range(max_pending):
        if not submit_next():
            break
    while pending:
        completed, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in completed:
            index = pending.pop(future)
            result = future.result()
            submit_next()
            yield index, result


class FastaCountCache:
    """Share a FASTA scan between chain workers; create anew for every stage run."""

    def __init__(self):
        self._counts = {}
        self._lock = Lock()

    def count(self, source):
        source = Path(source)
        with self._lock:
            future = self._counts.get(source)
            if isinstance(future, int):
                return future
            owner = future is None
            if owner:
                future = self._counts[source] = Future()
        if owner:
            try:
                try:
                    with source.open(encoding="utf-8", errors="ignore") as handle:
                        count = sum(line.startswith(">") for line in handle)
                except OSError:
                    count = 0
                future.set_result(count)
                with self._lock:
                    self._counts[source] = count
            except BaseException as exc:
                future.set_exception(exc)
                raise
        return future.result()
