import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("bash") and shutil.which("flock"),
                     "shared runner lock requires POSIX bash and flock")
class PipelineResourceLockTests(unittest.TestCase):
    def test_independent_runner_processes_are_serialized(self):
        helper = Path(__file__).resolve().parents[2] / "pipeline_resource_lock.sh"
        self.assertTrue(helper.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["SCIGBLAST_RESOURCE_LOCK_FILE"] = str(Path(tmp) / "shared.lock")
            env["SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS"] = "10"
            command = f'source "{helper}"; scigblast_acquire_resource_lock; sleep 0.8'
            first = subprocess.Popen(
                ["bash", "-c", command], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                time.sleep(0.1)
                start = time.monotonic()
                second = subprocess.run(
                    ["bash", "-c", f'source "{helper}"; scigblast_acquire_resource_lock'],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                waited = time.monotonic() - start
                first_output, first_error = first.communicate(timeout=10)
                self.assertEqual(first.returncode, 0, first_error or first_output)
                self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
                self.assertGreaterEqual(waited, 0.45)
            finally:
                if first.poll() is None:
                    first.terminate()
                    first.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
