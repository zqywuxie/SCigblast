"""Deployment update ordering with a local Git remote and a fake Docker CLI."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('git') and shutil.which('bash'), 'Run in Linux/WSL; never uses real Docker')
class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote, self.seed, self.checkout = (self.root / p for p in ('remote.git', 'seed', 'checkout'))
        self.git('init', '--bare', str(self.remote))
        self.git('init', '-b', 'main', str(self.seed))
        self.git('-C', str(self.seed), 'config', 'user.email', 'test@example.invalid')
        self.git('-C', str(self.seed), 'config', 'user.name', 'Deploy Test')
        self.script = (Path(__file__).resolve().parents[2] / 'deploy.sh').read_text()
        (self.seed / 'deploy.sh').write_text(self.script)
        (self.seed / 'web').mkdir()
        (self.seed / 'web/docker-compose.yml').write_text('services: {}\n')
        (self.seed / 'web/.env.example').write_text('EXAMPLE=yes\n')
        self.git('-C', str(self.seed), 'add', '.')
        self.git('-C', str(self.seed), 'commit', '-m', 'initial')
        self.git('-C', str(self.seed), 'remote', 'add', 'origin', str(self.remote))
        self.git('-C', str(self.seed), 'push', '-u', 'origin', 'main')
        self.git('clone', '--branch', 'main', str(self.remote), str(self.checkout))
        tools = self.root / 'bin'; tools.mkdir()
        docker = tools / 'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DEPLOY_TRACE"\nexit 0\n')
        docker.chmod(0o755)
        self.trace = self.root / 'docker-calls'
        self.env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'], 'DEPLOY_TRACE': str(self.trace)}
        self.env.pop('SCIGBLAST_DEPLOY_PULL_DONE', None)
        self.env.pop('SCIGBLAST_ENV_FILE', None)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True)

    def run_deploy(self):
        return subprocess.run(['bash', str(self.checkout / 'deploy.sh')], env=self.env, capture_output=True, text=True, timeout=30)

    def test_pull_then_execute_updated_script(self):
        (self.seed / 'deploy.sh').write_text(self.script.replace('command -v docker', 'log UPDATED_DEPLOY_SCRIPT\ncommand -v docker'))
        self.git('-C', str(self.seed), 'commit', '-am', 'updated deploy')
        self.git('-C', str(self.seed), 'push')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)  # first-run .env setup
        self.assertIn('UPDATED_DEPLOY_SCRIPT', result.stdout)
        self.assertTrue((self.checkout / 'web/.env').exists())
        self.assertNotIn('up', self.trace.read_text().splitlines())

    def test_local_edits_block_before_docker(self):
        (self.checkout / 'web/docker-compose.yml').write_text('local change\n')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.trace.exists())
        self.assertEqual((self.checkout / 'web/docker-compose.yml').read_text(), 'local change\n')

    def test_pull_failure_does_not_deploy(self):
        self.git('-C', str(self.checkout), 'remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        result = self.run_deploy()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.trace.exists())


if __name__ == '__main__':
    unittest.main()
