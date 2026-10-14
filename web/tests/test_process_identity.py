"""A tiny detached process reproduces a Web-only restart; no pipeline is run."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_identity


@unittest.skipUnless(os.name == 'posix' and Path('/proc').is_dir(), 'Linux /proc required')
class DetachedProcessTests(unittest.TestCase):
    def test_group_survives_parent_exit_and_only_verified_identity_can_be_stopped(self):
        token = uuid.uuid4().hex
        code = ('import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                'start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)')
        wrapper = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                                 env={**os.environ, 'SCIGBLAST_WEB_PROCESS_TOKEN': token})
        pgid = int(wrapper.stdout)
        try:
            self.assertEqual(process_identity.group_state(pgid, token), 'verified')
            with patch.object(process_identity.os, 'killpg') as kill:
                self.assertEqual(process_identity.signal_group(pgid, 'different-attempt', signal.SIGTERM), 'unverified')
                kill.assert_not_called()
            self.assertEqual(process_identity.signal_group(pgid, token, signal.SIGTERM), 'verified')
            for _ in range(100):
                if process_identity.group_state(pgid, token) == 'gone':
                    break
                time.sleep(.02)
            self.assertEqual(process_identity.group_state(pgid, token), 'gone')
        finally:
            process_identity.signal_group(pgid, token, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
