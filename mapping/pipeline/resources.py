"""Effective mapping concurrency under host and cgroup limits (no hard RSS cap)."""
from __future__ import annotations

import math
import os
from pathlib import Path

GIB = 1024 ** 3


def _text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def _number(path):
    try:
        value = int(_text(path))
        return value if 0 <= value < 2 ** 60 else None
    except ValueError:
        return None


def _groups(proc_root, cgroup_root, controller):
    """Include mounted root plus process subgroups and their parent constraints."""
    paths = set()
    for line in _text(proc_root / 'self/cgroup').splitlines():
        parts = line.split(':', 2)
        if len(parts) != 3:
            continue
        _, controllers, relative = parts
        if controllers and controller not in controllers.split(','):
            continue
        bases = [cgroup_root] if not controllers else [cgroup_root / controllers, cgroup_root / controller]
        for base in bases:
            paths.add(base)
            child = base / relative.lstrip('/')
            if '..' in child.parts:
                continue
            while child != base and child.is_relative_to(base):
                paths.add(child)
                child = child.parent
    # In a cgroup namespace the mounted root may already be the process cgroup.
    paths.update((cgroup_root, cgroup_root / controller))
    return paths


def detect_resources(proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    proc_root, cgroup_root = Path(proc_root), Path(cgroup_root)
    capacities, available = [], []
    memory_info = {}
    for line in _text(proc_root / 'meminfo').splitlines():
        words = line.split()
        if len(words) >= 2 and words[1].isdigit():
            memory_info[words[0].rstrip(':')] = int(words[1]) * 1024
    if memory_info.get('MemTotal'):
        capacities.append(memory_info['MemTotal'])
    if 'MemAvailable' in memory_info:
        available.append(memory_info['MemAvailable'])
    for group in _groups(proc_root, cgroup_root, 'memory'):
        for limit_file, usage_file in (('memory.max', 'memory.current'),
                                       ('memory.limit_in_bytes', 'memory.usage_in_bytes')):
            limit, usage = _number(group / limit_file), _number(group / usage_file)
            if limit is not None:
                capacities.append(limit)
                available.append(max(0, limit - (usage or 0)))
    cpus = float(os.cpu_count() or 1)
    if hasattr(os, 'sched_getaffinity'):
        try:
            cpus = min(cpus, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    for group in _groups(proc_root, cgroup_root, 'cpu'):
        values = _text(group / 'cpu.max').split()
        if len(values) == 2 and values[0] != 'max':
            try:
                quota, period = map(int, values)
                if quota > 0 and period > 0:
                    cpus = min(cpus, quota / period)
            except ValueError:
                pass
        quota, period = _number(group / 'cpu.cfs_quota_us'), _number(group / 'cpu.cfs_period_us')
        if quota and period:
            cpus = min(cpus, quota / period)
    return {'memory_limit_bytes': min(capacities) if capacities else None,
            'memory_available_bytes': min(available) if available else None,
            'cpu_count': cpus}


def worker_budget(config, resources=None):
    threads = int(config['SCIGBLAST_IGBLAST_THREADS'])
    requested = int(config['SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS'])
    budget = int(config['SCIGBLAST_PARALLEL_THREAD_BUDGET'])
    memory = min(300, int(config['SCIGBLAST_MEMORY_BUDGET_GB'])) * GIB
    per_job = int(config['SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB']) * GIB
    if min(threads, requested, budget, memory, per_job) < 1 or threads > budget or per_job > memory:
        raise ValueError('reMapping 并发配置超出线程/内存预算')
    resources = detect_resources() if resources is None else resources
    capacity = min(memory, resources['memory_limit_bytes']) if resources['memory_limit_bytes'] is not None else memory
    headroom = max(256 * 1024 ** 2, math.ceil(capacity * 0.05))
    available = min(capacity, resources['memory_available_bytes']) if resources['memory_available_bytes'] is not None else capacity
    usable = max(0, available - headroom)
    effective_cpus = max(1, math.floor(resources['cpu_count']))
    effective_threads = min(threads, effective_cpus)
    workers = min(4, requested, min(budget, effective_cpus) // effective_threads, usable // per_job)
    if workers < 1:
        raise ValueError(f'reMapping 可用内存不足：保留运行余量后 {usable / GIB:.2f} GiB，'
                         f'每个比对进程预计需要 {per_job / GIB:.2f} GiB；请调整资源配置或稍后重试')
    return {'workers': workers, 'threads': effective_threads, 'requested_threads': threads,
            'requested_workers': requested, 'memory_usable_gib': round(usable / GIB, 2),
            'memory_per_worker_gib': per_job // GIB, 'cpu_count': resources['cpu_count']}
