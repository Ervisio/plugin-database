import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import types
import sys
import io
import contextlib
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('discovery_bridge', Path(__file__).parents[1] / 'db-bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class DiscoveryTests(unittest.TestCase):
    def test_private_payload_survives_runtime_activation_then_is_consumed(self):
        with tempfile.TemporaryDirectory() as home:
            directory=Path(home,bridge.PAYLOAD_SUBDIR);directory.mkdir(parents=True)
            token='a'*32;payload=directory/(token+'.json')
            payload.write_text(json.dumps({'instance':{'engine':'mysql'},'sql':'SELECT 1'}))
            activated=[]
            def activate():
                self.assertTrue(payload.exists());activated.append(True)
            with patch.object(bridge.os.path,'expanduser',return_value=home),patch.object(bridge.glob,'glob',return_value=[]),patch.object(bridge,'activate_runtime',side_effect=activate),patch.object(bridge,'action_execute_query',return_value={'rows':[{'n':1}]}),patch.object(bridge.sys,'argv',['db-bridge.py','query','@file:'+token]),contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as exit:
                    bridge.main()
                self.assertEqual(exit.exception.code,0)
            self.assertTrue(activated);self.assertFalse(payload.exists());self.assertEqual(json.loads(output.getvalue())['rows'][0]['n'],1)

    def test_docker_mysql_does_not_connect_to_unrelated_native_socket(self):
        fake=types.ModuleType('pymysql')
        fake.cursors=types.SimpleNamespace(DictCursor=dict)
        fake.connect=lambda **kwargs:kwargs
        with patch.dict(sys.modules,{'pymysql':fake}),patch.object(bridge,'require_driver'),patch.object(bridge,'IS_WINDOWS',False),patch.object(bridge.os.path,'exists',return_value=True):
            result=bridge.get_mysql_conn({'mode':'docker','host':'127.0.0.1','port':13306})
            self.assertEqual(result['port'],13306)
            self.assertNotIn('unix_socket',result)
            result=bridge.get_mysql_conn({'mode':'native','service':'mariadb.service','host':'localhost','port':3306})
            self.assertIn('unix_socket',result)

    def test_mysql_tls_verifies_the_server(self):
        fake=types.ModuleType('pymysql');fake.cursors=types.SimpleNamespace(DictCursor=dict);fake.connect=lambda **kwargs:kwargs
        with patch.dict(sys.modules,{'pymysql':fake}),patch.object(bridge,'require_driver'):
            result=bridge.get_mysql_conn({'mode':'local','host':'db.example.test','tls':True})
            self.assertTrue(result['ssl'].check_hostname)

    def make_db(self, path):
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE "order items" ("row id" INTEGER PRIMARY KEY, "unit price" REAL, doubled REAL GENERATED ALWAYS AS ("unit price"*2))')
            conn.execute('INSERT INTO "order items" ("unit price") VALUES (12.5)')
        conn.close()

    def test_same_directory_files_have_distinct_stable_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'one.sqlite', Path(folder)/'two.sqlite3'
            self.make_db(a); self.make_db(b)
            found, report = bridge.scan_sqlite([folder, folder])
            self.assertEqual(len(found), 2)
            self.assertEqual(len({i['id'] for i in found}), 2)
            self.assertEqual(bridge.sqlite_instance(str(a))['id'], bridge.sqlite_instance(str(a))['id'])

    def test_header_validation_excludes_non_sqlite_and_includes_extensionless(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'not-a-database.db').write_text('ordinary text')
            self.make_db(Path(folder, 'application.data'))
            found, _ = bridge.scan_sqlite([folder])
            self.assertEqual([i['name'] for i in found], ['application.data'])

    def test_open_missing_never_creates_an_empty_database(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder, 'missing.db')
            with self.assertRaisesRegex(RuntimeError, 'does not exist'):
                bridge.get_sqlite_conn(str(missing))
            self.assertFalse(missing.exists())

    def test_real_sqlite_names_generated_columns_and_uri_characters(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'caffè #report.sqlite3'); self.make_db(path)
            instance = bridge.sqlite_instance(str(path))
            databases = bridge.action_list_databases(instance)['databases']
            self.assertEqual(databases[0]['tables_count'], 1)
            self.assertGreater(databases[0]['size_bytes'], 0)
            result = bridge.action_browse_data(instance, {'table':'order items','sort_col':'unit price'})
            self.assertEqual(result['rows'][0]['unit price'], 12.5)
            self.assertEqual(result['rows'][0]['doubled'], 25)
            self.assertTrue(next(c for c in result['meta'] if c['name']=='doubled')['generated'])
            structure=bridge.action_table_structure(instance, {'table':'order items'})
            self.assertEqual(len(structure['columns']), 3)
            bridge.action_save_row(instance, {'table':'order items','data':{'unit price':8},'pks':{'row id':1}})
            self.assertEqual(bridge.action_browse_data(instance, {'table':'order items'})['rows'][0]['doubled'], 16)

    def test_quoted_table_name_is_safe_and_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'quoted.db')
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE "a""b" ("x""y" TEXT)')
                conn.execute('INSERT INTO "a""b" VALUES (?)', ('hello',))
            conn.close()
            instance=bridge.sqlite_instance(str(path))
            self.assertEqual(bridge.action_browse_data(instance, {'table':'a"b'})['rows'][0]['x"y'],'hello')
            self.assertEqual(bridge.action_list_tables(instance,{})['tables'][0]['rows_count'],1)

    def test_create_does_not_rewrite_existing_file_and_preserves_sqlite3_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'existing.sqlite3');self.make_db(path);before=path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                bridge.action_create_sqlite({'path':str(path)})
            self.assertEqual(path.read_bytes(),before)
            new=Path(folder,'new.sqlite3')
            bridge.action_create_sqlite({'path':str(new)})
            self.assertTrue(bridge.sqlite_header(new));self.assertFalse(Path(str(new)+'.sqlite').exists())

    def test_scan_limit_is_reported_and_noisy_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            skipped=Path(folder,'node_modules');skipped.mkdir();self.make_db(skipped/'hidden.db')
            self.make_db(Path(folder,'first.db'));self.make_db(Path(folder,'second.db'))
            found, report=bridge.scan_sqlite([folder],max_files=1)
            self.assertEqual(len(found),1);self.assertTrue(report['limited'])
            found,_=bridge.scan_sqlite([folder]);self.assertEqual(len(found),2)

    def test_partial_discovery_reports_unavailable_sources_instead_of_hiding_them(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bridge,'run_cmd',side_effect=RuntimeError('fixture source unavailable')), patch.object(bridge,'port_open',return_value=False):
            self.make_db(Path(folder,'still-available.db'))
            result=bridge.action_detect({'roots':[folder]})
            self.assertEqual(len(result['sqlite_files']),1)
            self.assertTrue(result['warnings'])

    @unittest.skipUnless(os.name=='posix','Linux service instances')
    def test_multiple_native_clusters_are_not_dropped(self):
        lines='postgresql@16-main.service loaded active running PostgreSQL\npostgresql@17-main.service loaded active running PostgreSQL'
        with tempfile.TemporaryDirectory() as folder, patch.object(bridge,'run_cmd',side_effect=lambda args,**kw: lines if args[0]=='systemctl' else ''),patch.object(bridge,'port_open',return_value=False):
            result=bridge.action_detect({'roots':[folder]})
            self.assertEqual(len(result['instances']),2)
            self.assertEqual(len({i['id'] for i in result['instances']}),2)
