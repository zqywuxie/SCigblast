"""Small, private phase receipts; no per-read instrumentation or public data."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import threading
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


def measured(name):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with detail(name):
                return function(*args, **kwargs)
        return invoke
    return decorate


def measured_rows(name, records, **metadata):
    # The interval includes processing by the caller, not only fetching rows.
    with detail(name, **metadata) as entry:
        count = 0
        for record in records:
            count += 1
            yield record
        if entry is not None:entry['records'] = count


def process_tree_sample():
    """Sample this report's descendants only; never include sibling pipelines."""
    pending=[os.getpid()];seen=set();rss=read=write=0
    while pending:
        pid=pending.pop()
        if pid in seen:continue
        seen.add(pid)
        try:
            folder=Path('/proc')/str(pid)
            rss+=int((folder/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
            for line in (folder/'io').read_text().splitlines():
                key,value=line.split(':',1)
                if key=='read_bytes':read+=int(value)
                elif key=='write_bytes':write+=int(value)
            for path in (folder/'task').glob('*/children'):
                pending.extend(map(int,path.read_text().split()))
        except (OSError,ValueError,IndexError,AttributeError):continue
    return dict(rss_bytes=rss,live_read_bytes=read,live_write_bytes=write,processes=len(seen))


class Performance:
    def __init__(self, path):
        self.path = Path(path)
        self.started = time.perf_counter()
        self.data = dict(version=1, pid=os.getpid(), started_at=time.time(), phases=[], status='running')
        self._stop=threading.Event();self._sampler=None
        self._lock=threading.RLock()

    def monitor(self, workspace):
        """Low-frequency sampled peaks; counters for exited children come from receipts."""
        def sample():
            while not self._stop.is_set():
                current=process_tree_sample();disk=0
                try:
                    for folder,dirs,files in os.walk(Path(workspace() if callable(workspace) else workspace),followlinks=False):
                        dirs[:]=[name for name in dirs if not Path(folder,name).is_symlink()]
                        for name in files:
                            path=Path(folder,name)
                            if not path.is_symlink():disk+=path.stat().st_size
                except OSError:pass
                with self._lock:
                    resources=self.data.setdefault('resources',dict(sample_interval_seconds=2,peak_tree_rss_bytes=0,peak_workspace_bytes=0))
                    resources.update(current)
                    resources['peak_tree_rss_bytes']=max(resources['peak_tree_rss_bytes'],current['rss_bytes'])
                    resources['peak_workspace_bytes']=max(resources['peak_workspace_bytes'],disk)
                self._stop.wait(2)
        self._sampler=threading.Thread(target=sample,name='report-resources',daemon=True)
        self._sampler.start()

    def save(self):
        with self._lock:
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
        self._stop.set()
        if self._sampler is not None:self._sampler.join(timeout=3)
        self.data['status'] = status
        self.save()
