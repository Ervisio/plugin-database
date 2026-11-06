import importlib.util
import builtins
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import db_runtime

@contextmanager
def private_home():
    # Only this disposable fixture's ACL is changed. Production guards stay strict.
    with tempfile.TemporaryDirectory() as folder:
        if __import__('os').name == 'nt':
            import ctypes
            from ctypes import wintypes
            from db_runtime_windows import advapi, kernel, identity, pointer
            # Replace explicit as well as inherited ACEs (GitHub's Python 3.12
            # temp directory can already have explicit non-owner grants).
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.DWORD)]
            advapi.GetSecurityDescriptorDacl.argtypes = [pointer, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL)]
            advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer]
            advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
            descriptor, dacl = pointer(), pointer()
            sddl = 'D:P(A;OICI;FA;;;' + identity()[1] + ')(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'
            if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                present, defaulted = wintypes.BOOL(), wintypes.BOOL()
                if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present:
                    raise RuntimeError('Fixture DACL is absent')
                result = advapi.SetNamedSecurityInfoW(folder, 1, 0x80000004, None, None, dacl, None)
                if result:
                    raise ctypes.WinError(result)
            finally:
                kernel.LocalFree(descriptor)
        yield folder


class Builder:
    def create(self, directory):
        python = db_runtime.runtime_python(Path(directory))
        python.parent.mkdir(parents=True)
        python.write_text("fixture", encoding="utf-8")


class RuntimeTests(unittest.TestCase):
    def test_read_only_helper_import_does_not_require_venv(self):
        real_import = builtins.__import__
        def without_venv(name, *args, **kwargs):
            if name == 'venv':
                raise ModuleNotFoundError('fixture: venv is absent')
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=without_venv):
            spec = importlib.util.spec_from_file_location('readonly_runtime', Path(__file__).parents[1] / 'db_runtime.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows token and DACL')
    def test_windows_identity_ignores_profile_environment(self):
        with patch.dict('os.environ', {'USERPROFILE': r'C:\untrusted-profile', 'HOME': r'C:\untrusted-profile'}):
            self.assertNotEqual(str(db_runtime.account_home()), r'C:\untrusted-profile')

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows token and DACL')
    def test_windows_other_writable_runtime_is_refused(self):
        with private_home() as folder:
            result = subprocess.run(['icacls.exe', folder, '/grant', '*S-1-1-0:(OI)(CI)F'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0)
            with self.assertRaisesRegex(RuntimeError, 'another Windows account'):
                db_runtime._trusted_directory(Path(folder))
    def test_missing_driver_is_actionable_without_network(self):
        with patch("db_runtime.importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(db_runtime.DependencyMissing, "Prepare drivers"):
                db_runtime.require_driver("psycopg2")

    def test_setup_is_private_pinned_and_atomic(self):
        with private_home() as home:
            calls = []
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, "ready", "")
            result = db_runtime.install(home=home, runner=run, builder=Builder())
            self.assertTrue(result["ready"])
            self.assertTrue((db_runtime.runtime_dir(home) / "ready.json").is_file())
            argv, options = calls[0]
            self.assertIn("--require-hashes", argv)
            self.assertIn("--only-binary=:all:", argv)
            self.assertIn("--isolated", argv)
            self.assertTrue(str(argv[0]).startswith(home))
            self.assertNotIn("PYTHONPATH", options["env"])
            self.assertNotIn("OPENAI_API_KEY", options["env"])
            if __import__('os').name == 'nt':
                self.assertIn('SYSTEMDRIVE', {key.upper() for key in options['env']})
            self.assertNotIn("shell", options)
            self.assertEqual(len(calls), 3)

    def test_failed_setup_preserves_previous_runtime(self):
        with private_home() as home:
            root = db_runtime.prepare_root(home)
            target = db_runtime.runtime_dir(home)
            target.mkdir(mode=0o700)
            (target / "ready.json").write_text("previous")
            def run(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, "", "fixture wheel unavailable")
            with self.assertRaisesRegex(RuntimeError, "compatible binary wheels"):
                db_runtime.install(home=home, runner=run, builder=Builder())
            self.assertEqual((target / "ready.json").read_text(), "previous")
            self.assertEqual(list(root.glob(".prepare-*")), [])

    def test_post_activation_failure_restores_previous_runtime(self):
        with private_home() as home:
            db_runtime.prepare_root(home)
            target = db_runtime.runtime_dir(home)
            target.mkdir(mode=0o700)
            (target / "ready.json").write_text("previous")
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 1 if len(calls) == 3 else 0, "", "")
            with self.assertRaisesRegex(RuntimeError, "rolled back"):
                db_runtime.install(home=home, runner=run, builder=Builder())
            self.assertEqual((target / "ready.json").read_text(), "previous")

    @unittest.skipUnless(__import__('os').name == 'posix', 'POSIX symlink/permissions')
    def test_symlink_and_writable_runtime_are_refused(self):
        import os
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            (Path(home) / ".local").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "real directory"):
                db_runtime.prepare_root(home)
            (Path(home) / ".local").unlink()
            (Path(home) / ".local").mkdir(mode=0o777)
            os.chmod(Path(home) / ".local", 0o777)
            with self.assertRaisesRegex(RuntimeError, "unsafe owner or permissions"):
                db_runtime.prepare_root(home)

    def test_sqlite_needs_no_external_driver(self):
        spec = importlib.util.spec_from_file_location("db_bridge", Path(__file__).parents[1] / "db-bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as folder:
            conn = bridge.get_sqlite_conn(str(Path(folder) / "test.sqlite"))
            conn.execute("CREATE TABLE entries(value TEXT)")
            conn.execute("INSERT INTO entries VALUES (?)", ("real SQLite",))
            self.assertEqual(conn.execute("SELECT value FROM entries").fetchone()[0], "real SQLite")
            conn.close()


if __name__ == "__main__":
    unittest.main()
