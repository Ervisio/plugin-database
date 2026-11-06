"""Hosted Linux CI only: real install and helper re-exec amid broken global extras.

The disposable outer venv represents a system interpreter with legacy optional
OpenSSL/service_identity packages. No pip installation modifies system Python.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
import venv

sys.dont_write_bytecode = True
PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
import db_runtime


def run(argv, ok=True):
    result = subprocess.run([str(value) for value in argv], capture_output=True, text=True, timeout=300)
    if ok and result.returncode:
        raise AssertionError((result.stdout + result.stderr)[-4000:])
    return result


def main():
    if os.name != 'posix' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() == 0:
        raise RuntimeError('Run only on a fresh, unprivileged hosted Linux CI worker.')
    managed = db_runtime.runtime_root()
    if managed.exists():
        raise RuntimeError('Refusing to replace a pre-existing account runtime.')
    payload = None
    previous_umask = os.umask(0o002)
    try:
        with tempfile.TemporaryDirectory(prefix='database-isolation-', dir=os.environ['RUNNER_TEMP']) as folder:
            folder = Path(folder)
            outer = folder / 'base-interpreter'
            venv.EnvBuilder(with_pip=False).create(outer)
            python = outer / 'bin/python'
            packages = outer / ('lib/python%d.%d/site-packages' % sys.version_info[:2])
            for name in ('OpenSSL', 'service_identity'):
                package = packages / name
                package.mkdir()
                (package / '__init__.py').write_text("raise AttributeError('legacy optional package GEN_EMAIL fixture')\n")
                broken = run([python, '-I', '-c', 'import ' + name], ok=False)
                assert broken.returncode != 0 and 'GEN_EMAIL fixture' in broken.stderr
            helper = PLUGIN / 'db-bridge.py'
            prepared = json.loads(run([python, helper, 'runtime-prepare']).stdout)
            assert prepared['ok'] and prepared['ready'], prepared
            target = db_runtime.runtime_dir()
            assert stat.S_IMODE((target / 'pyvenv.cfg').stat().st_mode) == 0o600
            for entry in target.rglob('*'):
                if not entry.is_symlink():
                    assert not entry.stat().st_mode & 0o022, 'Runtime must not be group/world writable: ' + str(entry)
            status = json.loads(run([python, helper, 'runtime-status']).stdout)
            assert status['ready'] and status['isolated'] and all(status['drivers'].values()), status
            # Existing 1.2.4 runtimes created under umask 0002 must remain
            # rejected, yet the explicit preparation action must repair them.
            packages = target / ('lib/python%d.%d/site-packages' % sys.version_info[:2])
            packages.chmod(0o775)
            (target / 'pyvenv.cfg').chmod(0o664)
            rejected = run([python, helper, 'runtime-status'], ok=False)
            assert rejected.returncode != 0 and 'unsafe owner or permissions' in rejected.stdout, rejected.stdout
            repaired = json.loads(run([python, helper, 'runtime-prepare']).stdout)
            assert repaired['ok'] and repaired['ready'], repaired
            status = json.loads(run([python, helper, 'runtime-status']).stdout)
            assert status['ready'] and status['isolated'] and all(status['drivers'].values()), status
            assert stat.S_IMODE((target / 'pyvenv.cfg').stat().st_mode) == 0o600
            assert stat.S_IMODE(packages.stat().st_mode) == 0o700
            # The original interpreter's optional globals remain unchanged.
            assert run([python, '-I', '-c', 'import OpenSSL'], ok=False).returncode != 0
            database = folder / 'actual.sqlite'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE notes(message TEXT)')
                connection.execute('INSERT INTO notes VALUES (?)', ('coherent-runtime',))
            inbox = db_runtime.account_home() / '.config/ervisio/plugins/database/tmp'
            inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
            nonce = uuid.uuid4().hex
            payload = inbox / (nonce + '.json')
            with payload.open('x', encoding='utf-8') as output:
                json.dump({'instance': {'engine': 'sqlite', 'path': str(database)}, 'sql': 'SELECT message FROM notes'}, output)
            payload.chmod(0o600)
            query = json.loads(run([python, helper, 'query', '@file:' + nonce]).stdout)
            assert query['rows'][0]['message'] == 'coherent-runtime', query
            assert not payload.exists(), 'Payload must be consumed exactly once, after re-exec'
            assert not list((PLUGIN / '__pycache__').glob('db_runtime*.pyc')), 'Signed helper files must not receive bytecode'
            print('Real pinned install under umask 0002, unsafe-runtime repair, coherent helper status and private-payload SQLite query passed with broken global OpenSSL/service_identity.')
    finally:
        os.umask(previous_umask)
        if payload is not None and payload.exists():
            payload.unlink()
        # This exact runtime did not exist before this fresh CI test.
        if managed.exists():
            assert managed == db_runtime.account_home() / '.local/share/ervisio/database-runtime'
            shutil.rmtree(managed)


if __name__ == '__main__':
    main()
