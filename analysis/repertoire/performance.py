"""Small, private phase receipts; no per-read instrumentation or public data."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import time

from .io import write_json
CURRENT=ContextVar('report_performance',default=None)


def io_counts():
    try:
        return {key:int(value) for line in Path('/proc/self/io').read_text().splitlines()
                for key,value in [line.split(':',1)] if key in {'read_bytes','write_bytes'}}
    except (OSError,ValueError):return {}


@contextmanager
def detail(name, **metadata):
    active=CURRENT.get()
    if active is None:yield
    else:
        with active.phase(name,**metadata) as entry:
            yield entry


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
        started, cpu, io_before = time.perf_counter(), time.process_time(), io_counts()
        entry = dict(name=name, status='running', started_at=time.time(), **details)
        self.data['phases'].append(entry)
        self.save()
        token=CURRENT.set(self)
        try:
            yield entry
            entry['status'] = 'succeeded'
        except BaseException:
            entry['status'] = 'failed'
            raise
        finally:
            CURRENT.reset(token)
            entry.update(wall_seconds=round(time.perf_counter()-started, 6),
                         coordinator_cpu_seconds=round(time.process_time()-cpu, 6),ended_at=time.time())
            entry['coordinator_io_bytes']={key:value-io_before[key] for key,value in io_counts().items() if key in io_before}
            self.save()

    def call(self, name, function, *args, **kwargs):
        with self.phase(name):
            return function(*args, **kwargs)

    def finish(self, status):
        self.data['status'] = status
        self.save()
