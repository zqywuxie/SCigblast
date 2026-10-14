import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from mapping.pipeline.resources import detect_resources, worker_budget, GIB


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.cg = self.root / 'proc', self.root / 'cgroup'
        self.put(self.proc / 'meminfo', 'MemTotal: 524288000 kB\nMemAvailable: 419430400 kB\n')
        self.config = dict(SCIGBLAST_IGBLAST_THREADS='8', SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS='8',
                           SCIGBLAST_PARALLEL_THREAD_BUDGET='64', SCIGBLAST_MEMORY_BUDGET_GB='300',
                           SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB='48')

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))

    def detect(self):
        with patch('mapping.pipeline.resources.os.cpu_count', return_value=64):
            return detect_resources(self.proc, self.cg)

    def test_v2_parent_memory_usage_and_cpu_quota_bound_workers(self):
        self.put(self.proc / 'self/cgroup', '0::/jobs/current\n')
        self.put(self.cg / 'memory.max', 240 * GIB)
        self.put(self.cg / 'memory.current', 150 * GIB)
        self.put(self.cg / 'jobs/current/memory.max', 'max')
        self.put(self.cg / 'jobs/cpu.max', '1200000 100000')
        resources = self.detect()
        self.assertEqual(resources['memory_available_bytes'], 90 * GIB)
        result = worker_budget(self.config, resources)
        self.assertEqual((result['workers'], result['threads']), (1, 8))

    def test_v1_and_fractional_cpu_reduce_threads(self):
        self.put(self.proc / 'self/cgroup', '3:memory:/job\n4:cpu,cpuacct:/job\n')
        self.put(self.cg / 'memory/job/memory.limit_in_bytes', 120 * GIB)
        self.put(self.cg / 'memory/job/memory.usage_in_bytes', 10 * GIB)
        self.put(self.cg / 'cpu,cpuacct/job/cpu.cfs_quota_us', 250000)
        self.put(self.cg / 'cpu,cpuacct/job/cpu.cfs_period_us', 100000)
        result = worker_budget(self.config, self.detect())
        self.assertEqual((result['workers'], result['threads']), (1, 2))

    def test_insufficient_memory_does_not_force_one_worker(self):
        resources = dict(cpu_count=64, memory_limit_bytes=32 * GIB, memory_available_bytes=31 * GIB)
        with self.assertRaisesRegex(ValueError, '可用内存不足'):
            worker_budget(self.config, resources)

    def test_unlimited_and_missing_cgroup_use_host(self):
        self.put(self.cg / 'memory.max', 'max')
        self.put(self.cg / 'memory/memory.limit_in_bytes', 9223372036854771712)
        resources = self.detect()
        self.assertEqual(resources['memory_limit_bytes'], 500 * GIB)
        result = worker_budget(self.config, dict(resources, cpu_count=64))
        self.assertEqual(result['workers'], 5)
        self.assertEqual(result['memory_usable_gib'], 285)


if __name__ == '__main__':
    unittest.main()
