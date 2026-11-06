"""Account-local database drivers. No global pip, network during reads, or root/user sharing."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


class DependencyMissing(RuntimeError):
    pass


def account_home():
    if os.name == "posix":
        import pwd
        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    from db_runtime_windows import identity
    return Path(identity()[0])


def runtime_root(home=None):
    return Path(home or account_home()) / ".local" / "share" / "ervisio" / "database-runtime"


def _trusted_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("Database runtime path must be a real directory.")
    if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise RuntimeError("Database runtime directory has an unsafe owner or permissions.")
    if os.name == "nt":
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Database runtime path must not be a Windows reparse point.")
        from db_runtime_windows import trusted_directory
        trusted_directory(path)


def prepare_root(home=None):
    home = Path(home or account_home())
    _trusted_directory(home)
    path = home
    for part in (".local", "share", "ervisio", "database-runtime"):
        path = path / part
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _trusted_directory(path)
    return path


def runtime_dir(home=None):
    return runtime_root(home) / ("python-%d.%d" % sys.version_info[:2])


def runtime_python(path):
    return path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepared_runtime():
    """Validate the account-owned environment before executing any of its code."""
    root = runtime_root()
    if not root.exists():
        return None
    # Verify every directory before importing executable code (also on admin calls).
    prepare_root()
    path = runtime_dir()
    if not path.exists():
        return None
    _trusted_directory(path)
    marker = path / "ready.json"
    if not marker.is_file() or marker.is_symlink():
        return None
    packages = path / ("Lib/site-packages" if os.name == "nt" else "lib/python%d.%d/site-packages" % sys.version_info[:2])
    _trusted_directory(packages)
    config = path / "pyvenv.cfg"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if config.is_symlink():
        raise DependencyMissing("The database runtime configuration is unsafe. Prepare drivers again.")
    fd = os.open(config, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
            raise DependencyMissing("The database runtime configuration is unsafe. Prepare drivers again.")
        data = os.read(fd, 4097)
    finally:
        os.close(fd)
    options = dict(line.split("=", 1) for line in data.decode("utf-8").splitlines() if "=" in line)
    options = {key.strip().lower(): value.strip().lower() for key, value in options.items()}
    if len(data) > 4096 or options.get("include-system-site-packages") != "false":
        raise DependencyMissing("The database runtime must exclude system packages. Prepare drivers again.")
    if not runtime_python(path).is_file():
        raise DependencyMissing("The database runtime interpreter is missing. Prepare drivers again.")
    return path


def using_runtime(path):
    return bool(sys.flags.isolated and sys.prefix != sys.base_prefix and Path(sys.prefix).resolve() == path.resolve())


def activate_runtime():
    """Re-execute in a coherent venv, without mixing optional system packages."""
    path = prepared_runtime()
    if path is None or using_runtime(path):
        return
    plugin = Path(__file__).resolve().parent
    # -I omits the script directory. Add only the trusted signed plugin folder,
    # preserve argv (including an unconsumed private @file payload), and never
    # execute a shell. Running this before payload parsing prevents double reads.
    bootstrap = "import sys,runpy; sys.dont_write_bytecode=True; plugin=sys.argv.pop(1); sys.path.insert(0,plugin); script=sys.argv[1]; sys.argv=sys.argv[1:]; runpy.run_path(script,run_name='__main__')"
    python = str(runtime_python(path))
    os.execve(python, [python, "-I", "-c", bootstrap, str(plugin), str(plugin / "db-bridge.py"), *sys.argv[1:]], os.environ.copy())


def require_driver(name):
    if importlib.util.find_spec(name) is None:
        raise DependencyMissing("Database drivers are missing for this account. Open Databases → Prepare drivers, then retry. System Python is not modified.")


def status():
    drivers = {name: importlib.util.find_spec(name) is not None for name in ("pymysql", "psycopg2", "cryptography", "pymongo", "redis")}
    path = prepared_runtime()
    isolated = path is not None and using_runtime(path)
    return {"ready": all(drivers.values()) and isolated, "drivers": drivers, "python": "%d.%d.%d" % sys.version_info[:3], "isolated": isolated}


@contextlib.contextmanager
def setup_lock(root):
    lock = root / "setup.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise RuntimeError("Unsafe database runtime lock.")
        if os.name == "posix":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        yield
    except BlockingIOError:
        raise RuntimeError("Database drivers are already being prepared. Wait and retry.") from None
    finally:
        os.close(fd)


@contextlib.contextmanager
def private_creation():
    """The bridge is single-threaded; keep venv/pip output private on POSIX."""
    if os.name != "posix":
        yield
        return
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def install(home=None, runner=subprocess.run, builder=None):
    if sys.version_info < (3, 10):
        raise RuntimeError("Database drivers need Python 3.10 or newer.")
    root = prepare_root(home)
    target = runtime_dir(home)
    requirements = Path(__file__).with_name("requirements.lock")
    # EnvBuilder and pip otherwise inherit e.g. an SSH umask of 0002 and
    # produce group-writable metadata rejected by prepared_runtime().
    with private_creation(), setup_lock(root):
        stage = Path(tempfile.mkdtemp(prefix=".prepare-", dir=root))
        try:
            if builder is None:
                import venv
                builder = venv.EnvBuilder(with_pip=True)
            builder.create(stage)
            python = runtime_python(stage)
            allowed_env = {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "SYSTEMDRIVE", "PROGRAMDATA", "PROGRAMFILES", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
            env = {key: value for key, value in os.environ.items() if key.upper() in allowed_env}
            result = runner([str(python), "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "--only-binary=:all:", "--require-hashes", "--index-url", "https://pypi.org/simple", "-r", str(requirements)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240, env=env)
            if result.returncode:
                raise RuntimeError("Could not prepare isolated database drivers. Python venv/pip and compatible binary wheels are required. " + (result.stderr or result.stdout)[-1600:])
            check = runner([str(python), "-I", "-c", "import pymysql, psycopg2, cryptography, pymongo, redis; print('ready')"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, env=env)
            if check.returncode:
                raise RuntimeError("Database driver verification failed; previous runtime was preserved.")
            (stage / "ready.json").write_text(json.dumps({"python": list(sys.version_info[:2])}), encoding="utf-8")
            previous = root / ".previous"
            if previous.exists():
                _trusted_directory(previous)
                shutil.rmtree(previous)
            if target.exists():
                _trusted_directory(target)
                target.rename(previous)
            try:
                stage.rename(target)
            except BaseException:
                if previous.exists():
                    previous.rename(target)
                raise
            try:
                verify = runner([str(runtime_python(target)), "-I", "-c", "import sys; assert sys.flags.isolated and sys.prefix != sys.base_prefix; import pymysql, psycopg2, cryptography, pymongo, redis; print('ready')"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, env=env)
                if verify.returncode:
                    raise RuntimeError("Database driver activation failed; changes were rolled back.")
            except BaseException:
                shutil.rmtree(target)
                if previous.exists():
                    previous.rename(target)
                raise
            # Keep previous runtime until a later successful preparation.
            return {"ok": True, "ready": True}
        except (ImportError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Cannot prepare database drivers: Python venv/pip is unavailable or the download timed out. Install Python's venv package for this interpreter, then retry.") from error
        finally:
            if stage.exists():
                shutil.rmtree(stage)
