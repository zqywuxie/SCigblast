"""Small, private phase receipts; no per-read instrumentation or public data."""
from contextlib import contextmanager
import os
from pathlib import Path
import time

from .io import write_json


class Performance:
    def __init__(self, path):
        self.path = Path(path)
        self.started = time.perf_counter()
        self.data = dict(version=1, pid=os.getpid(), started_at=time.time(), phases=[], status='running')

    def save(self):
        self.data['elapsed_seconds'] = round(time.perf_counter()-self.started, 6)
        self.data['updated_at'] = time.time()
        write_json(self.path, self.data)

    @contextmanager
    def phase(self, name, **details):
        started, cpu = time.perf_counter(), time.process_time()
        entry = dict(name=name, status='running', started_at=time.time(), **details)
        self.data['phases'].append(entry)
        self.save()
        try:
            yield entry
            entry['status'] = 'succeeded'
        except BaseException:
            entry['status'] = 'failed'
            raise
        finally:
            entry.update(wall_seconds=round(time.perf_counter()-started, 6),
                         coordinator_cpu_seconds=round(time.process_time()-cpu, 6),ended_at=time.time())
            self.save()

    def call(self, name, function, *args, **kwargs):
        with self.phase(name):
            return function(*args, **kwargs)

    def finish(self, status):
        self.data['status'] = status
        self.save()
