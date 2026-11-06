import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import db_runtime


class Builder:
    def create(self, directory):
        python = db_runtime.runtime_python(Path(directory))
        python.parent.mkdir(parents=True)
        python.write_text("fixture", encoding="utf-8")


class RuntimeTests(unittest.TestCase):
    def test_missing_driver_is_actionable_without_network(self):
        with patch("db_runtime.importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(db_runtime.DependencyMissing, "Prepare drivers"):
                db_runtime.require_driver("psycopg2")

    def test_setup_is_private_pinned_and_atomic(self):
        with tempfile.TemporaryDirectory() as home:
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
            self.assertNotIn("shell", options)
            self.assertEqual(len(calls), 2)

    def test_failed_setup_preserves_previous_runtime(self):
        with tempfile.TemporaryDirectory() as home:
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
