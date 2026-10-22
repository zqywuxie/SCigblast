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
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.helper=Path(__file__).resolve().parents[2]/'pipeline_resource_lock.sh'
        self.env={**os.environ,'SCIGBLAST_RESOURCE_LOCK_FILE':str(self.root/'shared.lock'),
                  'SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS':'10'}
        self.command=f'source "{self.helper}"; scigblast_acquire_resource_lock || exit $?'
        self.processes=[]

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
        self.temp.cleanup()

    def start(self,name):
        path=self.root/f'{name}.log'
        with path.open('wb') as output:
            process=subprocess.Popen(['bash','-c',self.command+'; read -r release'],env=self.env,
                                     stdin=subprocess.PIPE,stdout=output,stderr=output,text=True)
        self.processes.append(process)
        return process,path

    def acquired(self,process,path):
        deadline=time.monotonic()+5
        while 'acquired shared' not in path.read_text() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertIn('acquired shared',path.read_text())
        self.assertIsNone(process.poll())

    def test_two_runners_execute_and_third_waits_until_a_slot_is_released(self):
        import fcntl
        first,a=self.start('first');self.acquired(first,a)
        second,b=self.start('second');self.acquired(second,b)
        self.assertIn('1/2',a.read_text())
        self.assertIn('2/2',b.read_text())
        full=subprocess.run(['bash','-c',self.command],env={**self.env,'SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS':'0'},capture_output=True,text=True,timeout=5)
        self.assertEqual(full.returncode,75,full.stdout+full.stderr)
        third,c=self.start('third')
        time.sleep(.15)
        self.assertNotIn('acquired shared',c.read_text())
        with Path(self.env['SCIGBLAST_RESOURCE_LOCK_FILE']).open('a') as gate:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
        first.stdin.write('\n');first.stdin.flush();first.wait(timeout=5)
        self.acquired(third,c)
        self.assertIsNone(second.poll())

    def test_exclusive_treemap_gate_blocks_both_pipeline_slots(self):
        import fcntl
        with Path(self.env['SCIGBLAST_RESOURCE_LOCK_FILE']).open('a') as gate:
            fcntl.flock(gate,fcntl.LOCK_EX)
            waiting=subprocess.run(['bash','-c',self.command],env={**self.env,'SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS':'0'},capture_output=True,text=True,timeout=5)
            self.assertEqual(waiting.returncode,75)
            fcntl.flock(gate,fcntl.LOCK_UN)
            first,a=self.start('first');self.acquired(first,a)
            second,b=self.start('second');self.acquired(second,b)

    def test_child_holds_slot_after_launcher_exits(self):
        path=self.root/'child.log'
        with path.open('wb') as output:
            parent=subprocess.Popen(['bash','-c',self.command+'; sleep 2 &'],env=self.env,stdout=output,stderr=output)
            parent.wait(timeout=5)
        self.assertEqual(parent.returncode,0)
        second,b=self.start('second');self.acquired(second,b)
        full=subprocess.run(['bash','-c',self.command],env={**self.env,'SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS':'0'},capture_output=True,text=True,timeout=5)
        self.assertEqual(full.returncode,75,full.stdout+full.stderr)
        time.sleep(2.1)
        free=subprocess.run(['bash','-c',self.command],env={**self.env,'SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS':'0'},capture_output=True,text=True,timeout=5)
        self.assertEqual(free.returncode,0,free.stdout+free.stderr)


if __name__ == "__main__":
    unittest.main()
