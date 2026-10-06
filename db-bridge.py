#!/usr/bin/env python3
"""
Ervisio Database Plugin Bridge (db-bridge.py)
Provides unified HeidiSQL-style management for MySQL, MariaDB, PostgreSQL, and SQLite.
Communicates via JSON over stdout.
"""
import sys
import os
import json
import base64
import time
import subprocess
import glob
from typing import Dict, Any, List, Optional

import re

IS_WINDOWS = os.name == "nt"

# JSON goes out as UTF-8 on every system (the Windows console code page would mangle it).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

IDENT_RE = re.compile(r"^[A-Za-z0-9_$]{1,64}$")

def check_ident(name: str, what: str = "identifier"):
    if not name or not IDENT_RE.match(name):
        raise RuntimeError(f"Invalid {what}: {name!r} (letters, digits, _ and $ only, max 64)")

def qident(name: str, engine: str) -> str:
    check_ident(name)
    if engine == "postgres":
        return '"' + name.replace('"', '""') + '"'
    return "`" + name.replace("`", "``") + "`"

def json_serial(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", "replace")
    import decimal
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return str(obj)

def send_json(data: Any, status: int = 0):
    print(json.dumps(data, default=json_serial, ensure_ascii=False))
    sys.exit(status)

def send_error(msg: str, code: str = "error"):
    send_json({"error": {"message": str(msg), "code": code}}, status=1)

def run_cmd(cmd: List[str], timeout: int = 300) -> str:
    try:
        extra: Dict[str, Any] = {}
        if IS_WINDOWS:
            extra["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, **extra)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or f"Command failed with exit code {p.returncode}")
        return p.stdout
    except Exception as e:
        raise RuntimeError(f"Execution error: {e}")

def privileged(cmd: List[str]) -> List[str]:
    """Linux: prefix with sudo unless already root. Windows: the manifest runs the bridge elevated, no prefix."""
    if IS_WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return cmd
    return ["sudo"] + cmd

def tail_file(path: str, lines: int = 100) -> str:
    """Last lines of a text file (no external `tail`, which Windows does not have)."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 256 * 1024))
        data = fh.read()
    return "\n".join(data.decode("utf-8", "replace").splitlines()[-lines:])

SERVICE_RE = re.compile(r"^[A-Za-z0-9_.$-]{1,80}$")

def windows_database_services() -> List[Dict[str, str]]:
    """MariaDB, MySQL and PostgreSQL services registered with the Windows service manager."""
    script = (
        "Get-Service | Where-Object { $_.Name -match '^(mariadb|mysql|postgresql)' } | "
        "Select-Object Name, @{n='State';e={$_.Status.ToString()}} | ConvertTo-Json -Compress"
    )
    out = run_cmd(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=60).strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]

# Database connection helpers
def get_mysql_conn(instance: Dict[str, Any], database: Optional[str] = None):
    import pymysql
    host = instance.get("host", "127.0.0.1")
    port = int(instance.get("port", 3306))
    user = instance.get("user", "root")
    password = instance.get("password", "")
    unix_socket = instance.get("unix_socket")

    # If unix socket exists and host is localhost/127.0.0.1, try unix socket
    if not unix_socket and not IS_WINDOWS and (host in ("localhost", "127.0.0.1")):
        for sock in ["/var/run/mysqld/mysqld.sock", "/var/run/mysql/mysql.sock", "/tmp/mysql.sock"]:
            if os.path.exists(sock):
                unix_socket = sock
                break

    kwargs = {
        "user": user,
        "password": password,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
        "connect_timeout": 5
    }
    if database:
        kwargs["database"] = database
    if unix_socket and os.path.exists(unix_socket):
        kwargs["unix_socket"] = unix_socket
    else:
        kwargs["host"] = host
        kwargs["port"] = port

    return pymysql.connect(**kwargs)

def get_postgres_conn(instance: Dict[str, Any], database: Optional[str] = None):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    host = instance.get("host", "127.0.0.1")
    port = int(instance.get("port", 5432))
    user = instance.get("user", "postgres")
    password = instance.get("password", "")
    db_name = database or instance.get("default_db", "postgres")
    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=db_name,
        cursor_factory=RealDictCursor,
        connect_timeout=5
    )

def get_sqlite_conn(path: str):
    import sqlite3
    if not os.path.exists(path) and not path.endswith(".db") and not path.endswith(".sqlite"):
        raise RuntimeError(f"SQLite file not found: {path}")
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn

# Actions
WIN_SKIP_DIRS = {"appdata", "node_modules", ".git", "windows", "docker", "$recycle.bin"}

def find_sqlite_windows(max_per_root: int = 15, max_depth: int = 4) -> List[Dict[str, Any]]:
    """Shallow walk of the user profile and %ProgramData%, skipping AppData and other noisy folders."""
    found: List[Dict[str, Any]] = []
    roots = [os.path.expanduser("~"), os.environ.get("ProgramData", r"C:\ProgramData")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        count = 0
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.rstrip("\\/").count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in WIN_SKIP_DIRS]
            for fn in filenames:
                if not fn.lower().endswith((".db", ".sqlite", ".sqlite3")):
                    continue
                f = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(f)
                except OSError:
                    continue
                found.append({
                    "id": f"sqlite-{base64.b64encode(f.encode()).decode()[:16]}",
                    "name": fn,
                    "path": f,
                    "type": "sqlite",
                    "engine": "sqlite",
                    "mode": "file",
                    "status": "ready",
                    "size": size
                })
                count += 1
                if count >= max_per_root:
                    break
            if count >= max_per_root:
                break
    return found

def action_detect() -> Dict[str, Any]:
    instances = []
    
    # 1. Native services (systemd on Linux, the service manager on Windows)
    if IS_WINDOWS:
        try:
            seen = set()
            for svc in windows_database_services():
                name = svc.get("Name", "")
                low = name.lower()
                if low.startswith("mariadb"):
                    db_type, engine, port, label = "mariadb", "mysql", 3306, "MariaDB"
                elif low.startswith("mysql"):
                    db_type, engine, port, label = "mysql", "mysql", 3306, "MySQL"
                elif low.startswith("postgresql"):
                    db_type, engine, port, label = "postgres", "postgres", 5432, "PostgreSQL"
                else:
                    continue
                if db_type in seen:
                    continue
                seen.add(db_type)
                instances.append({
                    "id": f"native-{db_type}",
                    "name": f"{label} (Windows service)",
                    "type": db_type,
                    "engine": engine,
                    "mode": "native",
                    "service": name,
                    "host": "127.0.0.1",
                    "port": port,
                    "status": "running" if svc.get("State") == "Running" else "stopped",
                    "version": "native"
                })
        except Exception:
            pass
    else:
        try:
            units_out = run_cmd(["systemctl", "list-units", "--type=service", "--state=all", "--no-pager", "--no-legend"])
            for line in units_out.splitlines():
                parts = line.split()
                if not parts:
                    continue
                unit = parts[0]
                if "mariadb" in unit:
                    is_active = "running" in line
                    instances.append({
                        "id": "native-mariadb",
                        "name": "MariaDB (Native Systemd)",
                        "type": "mariadb",
                        "engine": "mysql",
                        "mode": "native",
                        "host": "127.0.0.1",
                        "port": 3306,
                        "status": "running" if is_active else "stopped",
                        "version": "native"
                    })
                elif "mysql" in unit and "native-mariadb" not in [x["id"] for x in instances]:
                    is_active = "running" in line
                    instances.append({
                        "id": "native-mysql",
                        "name": "MySQL (Native Systemd)",
                        "type": "mysql",
                        "engine": "mysql",
                        "mode": "native",
                        "host": "127.0.0.1",
                        "port": 3306,
                        "status": "running" if is_active else "stopped",
                        "version": "native"
                    })
                elif "postgres" in unit:
                    is_active = "running" in line
                    instances.append({
                        "id": "native-postgres",
                        "name": "PostgreSQL (Native Systemd)",
                        "type": "postgres",
                        "engine": "postgres",
                        "mode": "native",
                        "host": "127.0.0.1",
                        "port": 5432,
                        "status": "running" if is_active else "stopped",
                        "version": "native"
                    })
        except Exception:
            pass

    # 2. Docker database containers
    try:
        docker_out = run_cmd(["docker", "ps", "-a", "--format", "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"])
        for line in docker_out.splitlines():
            parts = line.split("|")
            if len(parts) >= 5:
                cid, name, image, status, ports = parts[0], parts[1], parts[2], parts[3], parts[4]
                img_lower = image.lower()
                is_running = "Up" in status
                
                db_type = None
                engine = None
                default_port = 3306
                
                if "mariadb" in img_lower:
                    db_type = "mariadb"
                    engine = "mysql"
                    default_port = 3306
                elif "mysql" in img_lower:
                    db_type = "mysql"
                    engine = "mysql"
                    default_port = 3306
                elif "postgres" in img_lower:
                    db_type = "postgres"
                    engine = "postgres"
                    default_port = 5432

                if db_type:
                    # Extract mapped port if available
                    host_port = default_port
                    if "->" in ports:
                        try:
                            # 0.0.0.0:3306->3306/tcp
                            host_port = int(ports.split("->")[0].split(":")[-1])
                        except Exception:
                            pass

                    instances.append({
                        "id": f"docker-{cid[:12]}",
                        "name": f"{name} ({image})",
                        "container": name,
                        "type": db_type,
                        "engine": engine,
                        "mode": "docker",
                        "host": "127.0.0.1",
                        "port": host_port,
                        "status": "running" if is_running else "stopped",
                        "version": image
                    })
    except Exception:
        pass

    # 3. Known SQLite files (/home/ubuntu and /var/lib; the profile and ProgramData on Windows)
    sqlite_files = []
    if IS_WINDOWS:
        sqlite_files = find_sqlite_windows()
    for root_dir in ([] if IS_WINDOWS else ["/home/ubuntu", "/var/lib"]):
        try:
            for ext in ["*.db", "*.sqlite", "*.sqlite3"]:
                for f in glob.glob(os.path.join(root_dir, "**", ext), recursive=True)[:15]:
                    if os.path.isfile(f) and not f.startswith("/var/lib/docker"):
                        sqlite_files.append({
                            "id": f"sqlite-{base64.b64encode(f.encode()).decode()[:16]}",
                            "name": os.path.basename(f),
                            "path": f,
                            "type": "sqlite",
                            "engine": "sqlite",
                            "mode": "file",
                            "status": "ready",
                            "size": os.path.getsize(f)
                        })
        except Exception:
            pass

    return {
        "instances": instances,
        "sqlite_files": sqlite_files,
        "supported_versions": {
            "mysql": ["8.4-lts", "8.0", "9.0", "5.7"],
            "mariadb": ["11.4-lts", "11.2", "10.11-lts", "10.6"],
            "postgres": ["17", "16", "15", "14"],
            "sqlite": ["3.x (system)"]
        }
    }

def action_install_docker(params: Dict[str, Any]) -> Dict[str, Any]:
    engine = params.get("engine", "mysql") # mysql, mariadb, postgres
    version = params.get("version", "8.4")
    name = params.get("name", f"db-{engine}").strip()
    root_password = params.get("password", "ErvisioSecret2026!").strip()
    port = int(params.get("port", 3306 if engine in ("mysql", "mariadb") else 5432))
    volume_name = f"{name}-data"

    image_tag = f"{engine}:{version}"
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{port}:{3306 if engine in ('mysql', 'mariadb') else 5432}",
        "-v", f"{volume_name}:/var/lib/{'mysql' if engine in ('mysql', 'mariadb') else 'postgresql/data'}",
    ]

    if engine in ("mysql", "mariadb"):
        cmd.extend(["-e", f"MYSQL_ROOT_PASSWORD={root_password}"])
    elif engine == "postgres":
        cmd.extend(["-e", f"POSTGRES_PASSWORD={root_password}"])

    cmd.append(image_tag)

    cid = run_cmd(cmd).strip()
    return {"ok": True, "container_id": cid, "name": name, "port": port}

WINGET_IDS = {
    "mariadb": "MariaDB.Server",
    "mysql": "Oracle.MySQL",
    "postgres": "PostgreSQL.PostgreSQL.17",
    "sqlite": "SQLite.SQLite",
}

def action_install_winget(params: Dict[str, Any]) -> Dict[str, Any]:
    engine = params.get("engine", "mariadb")
    pkg = WINGET_IDS.get(engine)
    if not pkg:
        raise RuntimeError(f"Unsupported engine: {engine}")
    cmd = ["winget.exe", "install", "--id", pkg, "-e", "--silent",
           "--accept-package-agreements", "--accept-source-agreements"]
    if engine == "postgres":
        password = str(params.get("password", "")).strip()
        if password:
            if '"' in password:
                raise RuntimeError("The password cannot contain double quotes")
            cmd += ["--override", f'--mode unattended --superpassword "{password}"']
    run_cmd(cmd, timeout=1200)
    return {"ok": True, "engine": engine, "package": pkg}

def action_install_apt(params: Dict[str, Any]) -> Dict[str, Any]:
    if IS_WINDOWS:
        return action_install_winget(params)
    engine = params.get("engine", "mariadb") # mariadb, mysql, postgres, sqlite
    pkg_map = {
        "mariadb": "mariadb-server",
        "mysql": "mysql-server",
        "postgres": "postgresql",
        "sqlite": "sqlite3"
    }
    pkg = pkg_map.get(engine, "mariadb-server")
    run_cmd(privileged(["apt-get", "update"]), timeout=120)
    run_cmd(privileged(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", pkg]), timeout=300)
    if engine in ("mariadb", "mysql"):
        run_cmd(privileged(["systemctl", "enable", "--now", engine]))
    elif engine == "postgres":
        run_cmd(privileged(["systemctl", "enable", "--now", "postgresql"]))
    return {"ok": True, "engine": engine, "package": pkg}

def action_create_sqlite(params: Dict[str, Any]) -> Dict[str, Any]:
    path = params.get("path", "").strip()
    if not path:
        raise RuntimeError("File path is required for SQLite database")
    path = os.path.expanduser(path)
    if not path.endswith(".sqlite") and not path.endswith(".db"):
        path = path + ".sqlite"
    dir_name = os.path.dirname(os.path.abspath(path))
    if not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS _info (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, note TEXT)")
    conn.commit()
    conn.close()
    return {"ok": True, "path": path}

def action_manage_instance(params: Dict[str, Any]) -> Dict[str, Any]:
    op = params.get("op") # start, stop, restart, remove
    mode = params.get("mode") # docker, native
    target = params.get("target")

    if mode == "docker":
        if op == "start":
            run_cmd(["docker", "start", target])
        elif op == "stop":
            run_cmd(["docker", "stop", target])
        elif op == "restart":
            run_cmd(["docker", "restart", target])
        elif op == "remove":
            run_cmd(["docker", "rm", "-f", target])
    elif mode == "native" and IS_WINDOWS:
        if not SERVICE_RE.match(target or ""):
            raise RuntimeError(f"Invalid service name: {target!r}")
        if op == "start":
            run_cmd(["net.exe", "start", target], timeout=180)
        elif op == "stop":
            run_cmd(["net.exe", "stop", target], timeout=180)
        elif op == "restart":
            try:
                run_cmd(["net.exe", "stop", target], timeout=180)
            except RuntimeError:
                pass  # already stopped
            run_cmd(["net.exe", "start", target], timeout=180)
    elif mode == "native":
        svc = "mariadb" if "mariadb" in target else ("mysql" if "mysql" in target else "postgresql")
        if op in ("start", "stop", "restart"):
            run_cmd(privileged(["systemctl", op, svc]))
    return {"ok": True, "op": op, "target": target}

def action_list_databases(instance: Dict[str, Any]) -> Dict[str, Any]:
    engine = instance.get("engine", "mysql")
    if engine == "sqlite":
        path = instance.get("path")
        return {"databases": [{"name": os.path.basename(path), "tables_count": 0, "size": os.path.getsize(path) if os.path.exists(path) else 0}]}

    dbs = []
    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        s.schema_name as name,
                        s.default_character_set_name as charset,
                        s.default_collation_name as collation,
                        COALESCE(SUM(t.data_length + t.index_length), 0) as size_bytes,
                        COUNT(t.table_name) as tables_count
                    FROM information_schema.schemata s
                    LEFT JOIN information_schema.tables t ON s.schema_name = t.table_schema
                    GROUP BY s.schema_name, s.default_character_set_name, s.default_collation_name
                    ORDER BY s.schema_name ASC
                """)
                dbs = cur.fetchall()
        finally:
            conn.close()

    elif engine == "postgres":
        conn = get_postgres_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        datname as name,
                        pg_encoding_to_char(encoding) as charset,
                        datcollate as collation,
                        pg_database_size(datname) as size_bytes
                    FROM pg_database
                    WHERE datistemplate = false
                    ORDER BY datname ASC
                """)
                dbs = cur.fetchall()
        finally:
            conn.close()

    return {"databases": dbs}

def action_create_database(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    db_name = params.get("name", "").strip()
    charset = params.get("charset", "utf8mb4")
    collation = params.get("collation", "utf8mb4_unicode_ci")
    
    if not db_name or not db_name.isidentifier():
        raise RuntimeError("Invalid database name")

    engine = instance.get("engine", "mysql")
    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET {charset} COLLATE {collation}")
        finally:
            conn.close()
    elif engine == "postgres":
        conn = get_postgres_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            conn.close()
    return {"ok": True, "created": db_name}

def action_drop_database(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    db_name = params.get("name", "").strip()
    if not db_name or not db_name.isidentifier():
        raise RuntimeError("Invalid database name")
    
    # Safety: protect system databases
    if db_name.lower() in ("information_schema", "performance_schema", "mysql", "sys", "postgres"):
        raise RuntimeError(f"Cannot drop system database: {db_name}")

    engine = instance.get("engine", "mysql")
    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE `{db_name}`")
        finally:
            conn.close()
    elif engine == "postgres":
        conn = get_postgres_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE "{db_name}"')
        finally:
            conn.close()
    return {"ok": True, "dropped": db_name}

def action_list_tables(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    db_name = params.get("database")
    engine = instance.get("engine", "mysql")
    tables = []

    if engine == "mysql":
        conn = get_mysql_conn(instance, database=db_name)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        table_name as name,
                        table_type as type,
                        engine,
                        table_rows as rows_count,
                        data_length as data_size,
                        index_length as index_size,
                        table_collation as collation,
                        table_comment as comment
                    FROM information_schema.tables 
                    WHERE table_schema = %s
                    ORDER BY table_name ASC
                """, (db_name,))
                tables = cur.fetchall()
        finally:
            conn.close()

    elif engine == "postgres":
        conn = get_postgres_conn(instance, database=db_name)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        table_name as name,
                        table_type as type,
                        '' as engine,
                        (SELECT reltuples::bigint FROM pg_class WHERE relname = table_name LIMIT 1) as rows_count,
                        pg_total_relation_size(quote_ident(table_name)) as data_size,
                        0 as index_size,
                        '' as collation,
                        '' as comment
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    ORDER BY table_name ASC
                """)
                tables = cur.fetchall()
        finally:
            conn.close()

    elif engine == "sqlite":
        conn = get_sqlite_conn(instance.get("path"))
        try:
            cur = conn.cursor()
            cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name ASC")
            for row in cur.fetchall():
                # Count rows
                rc = 0
                try:
                    c2 = conn.cursor()
                    c2.execute(f"SELECT COUNT(*) FROM \"{row['name']}\"")
                    rc = c2.fetchone()[0]
                except Exception:
                    pass
                tables.append({
                    "name": row["name"],
                    "type": "BASE TABLE" if row["type"] == "table" else "VIEW",
                    "engine": "SQLite",
                    "rows_count": rc,
                    "data_size": 0,
                    "index_size": 0,
                    "collation": "BINARY",
                    "comment": ""
                })
        finally:
            conn.close()

    return {"tables": tables, "database": db_name}

def action_table_structure(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    db_name = params.get("database")
    tbl_name = params.get("table")
    check_ident(tbl_name, "table name")
    engine = instance.get("engine", "mysql")
    columns = []
    indexes = []
    create_sql = ""

    if engine == "mysql":
        conn = get_mysql_conn(instance, database=db_name)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        column_name as name,
                        column_type as type,
                        is_nullable as nullable,
                        column_key as key_type,
                        column_default as default_val,
                        extra,
                        column_comment as comment
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position ASC
                """, (db_name, tbl_name))
                columns = cur.fetchall()

                cur.execute(f"SHOW INDEX FROM `{tbl_name}`")
                for idx in cur.fetchall():
                    indexes.append({
                        "name": idx.get("Key_name"),
                        "column": idx.get("Column_name"),
                        "unique": idx.get("Non_unique") == 0,
                        "seq": idx.get("Seq_in_index")
                    })

                cur.execute(f"SHOW CREATE TABLE `{tbl_name}`")
                res = cur.fetchone()
                if res:
                    create_sql = res.get("Create Table") or res.get("Create View") or ""
        finally:
            conn.close()

    elif engine == "postgres":
        conn = get_postgres_conn(instance, database=db_name)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        column_name as name,
                        data_type as type,
                        is_nullable as nullable,
                        '' as key_type,
                        column_default as default_val,
                        '' as extra,
                        '' as comment
                    FROM information_schema.columns
                    WHERE table_catalog = %s AND table_name = %s
                    ORDER BY ordinal_position ASC
                """, (db_name, tbl_name))
                for row in cur.fetchall():
                    columns.append({
                        "name": row["name"],
                        "type": row["type"],
                        "nullable": row["nullable"],
                        "key_type": row["key_type"],
                        "default_val": row["default_val"],
                        "extra": row["extra"],
                        "comment": row["comment"]
                    })
                cur.execute("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_catalog = %s AND tc.table_name = %s
                """, (db_name, tbl_name))
                pk_cols = {r["column_name"] for r in cur.fetchall()}
                for c in columns:
                    if c["name"] in pk_cols:
                        c["key_type"] = "PRI"
                cur.execute("""
                    SELECT indexname as name, indexdef as definition
                    FROM pg_indexes WHERE tablename = %s
                """, (tbl_name,))
                for idx in cur.fetchall():
                    definition = (idx.get("definition") or "")
                    indexes.append({
                        "name": idx.get("name"),
                        "column": definition,
                        "unique": "UNIQUE" in definition.upper(),
                        "seq": 1
                    })
                create_sql = f"-- DDL for postgres table {tbl_name} (use pg_dump for full definition)"
        finally:
            conn.close()

    elif engine == "sqlite":
        check_ident(tbl_name, "table name")
        conn = get_sqlite_conn(instance.get("path"))
        try:
            cur = conn.cursor()
            cur.execute(f'PRAGMA table_info("{tbl_name}")')
            for row in cur.fetchall():
                columns.append({
                    "name": row["name"],
                    "type": row["type"],
                    "nullable": "YES" if row["notnull"] == 0 else "NO",
                    "key_type": "PRI" if row["pk"] > 0 else "",
                    "default_val": row["dflt_value"],
                    "extra": "auto_increment" if row["pk"] == 1 and row["type"].upper() == "INTEGER" else "",
                    "comment": ""
                })
            cur.execute("SELECT sql FROM sqlite_master WHERE name = ?", (tbl_name,))
            r = cur.fetchone()
            if r:
                create_sql = r[0]
        finally:
            conn.close()

    return {"columns": columns, "indexes": indexes, "create_sql": create_sql}

def _ser(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<blob {len(v)} bytes>"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v

def _row_dict(r, columns):
    if isinstance(r, dict):
        return {k: _ser(v) for k, v in r.items()}
    return {k: _ser(r[k]) for k in columns}

def _open_conn(instance: Dict[str, Any], db_name: Optional[str]):
    engine = instance.get("engine", "mysql")
    if engine == "mysql":
        return get_mysql_conn(instance, database=db_name)
    if engine == "postgres":
        return get_postgres_conn(instance, database=db_name)
    if engine == "sqlite":
        return get_sqlite_conn(instance.get("path"))
    raise RuntimeError(f"Unsupported engine: {engine}")

def _exec(engine: str, cur, sql: str, args: List[Any]):
    # pymysql/psycopg2 run % formatting only when args are given
    if engine == "sqlite":
        cur.execute(sql, args)
    else:
        cur.execute(sql, args or None)

def _column_meta(engine: str, cur, tbl_name: str) -> List[Dict[str, Any]]:
    """Light column list (name, type, pk, nullable, auto, default) for the data grid."""
    meta: List[Dict[str, Any]] = []
    if engine == "mysql":
        cur.execute("""
            SELECT column_name AS name, column_type AS type, column_key AS key_type,
                   is_nullable AS nullable, extra, column_default AS default_val
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY ordinal_position
        """, (tbl_name,))
        for r in cur.fetchall():
            r = {k.lower(): v for k, v in r.items()}
            meta.append({
                "name": r["name"], "type": r["type"], "pk": r["key_type"] == "PRI",
                "nullable": r["nullable"] == "YES", "auto": "auto_increment" in (r["extra"] or ""),
                "default": _ser(r["default_val"]),
            })
    elif engine == "postgres":
        cur.execute("""
            SELECT column_name AS name, data_type AS type, is_nullable AS nullable, column_default AS default_val
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (tbl_name,))
        cols = cur.fetchall()
        cur.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public' AND tc.table_name = %s
        """, (tbl_name,))
        pks = {r["column_name"] for r in cur.fetchall()}
        for r in cols:
            dflt = r["default_val"] or ""
            meta.append({
                "name": r["name"], "type": r["type"], "pk": r["name"] in pks,
                "nullable": r["nullable"] == "YES", "auto": dflt.startswith("nextval(") or "identity" in dflt.lower(),
                "default": _ser(r["default_val"]),
            })
    elif engine == "sqlite":
        cur.execute(f'PRAGMA table_info("{tbl_name}")')
        for r in cur.fetchall():
            meta.append({
                "name": r["name"], "type": r["type"], "pk": r["pk"] > 0,
                "nullable": r["notnull"] == 0 and r["pk"] == 0,
                "auto": r["pk"] == 1 and (r["type"] or "").upper() == "INTEGER",
                "default": r["dflt_value"],
            })
    return meta

FILTER_OPS = {
    "eq": "{c} = {p}", "ne": "{c} <> {p}", "gt": "{c} > {p}", "lt": "{c} < {p}",
    "gte": "{c} >= {p}", "lte": "{c} <= {p}",
    "contains": "{t} LIKE {p}", "starts": "{t} LIKE {p}", "ends": "{t} LIKE {p}",
    "null": "{c} IS NULL", "notnull": "{c} IS NOT NULL",
}
BINARY_TYPES = ("blob", "binary", "bytea")

def _quote(engine: str, name: str) -> str:
    return f"`{name}`" if engine == "mysql" else f'"{name}"'

def _as_text(engine: str, col: str) -> str:
    return f"CAST({col} AS CHAR)" if engine == "mysql" else f"CAST({col} AS TEXT)"

def _build_where(engine: str, meta: List[Dict[str, Any]], params: Dict[str, Any]):
    """WHERE clause from the quick search, the per-column filters and the raw WHERE. Values are always bound."""
    ph = "?" if engine == "sqlite" else "%s"
    like = "ILIKE" if engine == "postgres" else "LIKE"
    known = {m["name"] for m in meta}
    parts: List[str] = []
    args: List[Any] = []

    search = str(params.get("search") or "").strip()
    if search:
        ors = []
        for m in meta:
            if any(b in (m["type"] or "").lower() for b in BINARY_TYPES):
                continue
            ors.append(f"{_as_text(engine, _quote(engine, m['name']))} {like} {ph}")
            args.append(f"%{search}%")
        if ors:
            parts.append("(" + " OR ".join(ors) + ")")

    for f in params.get("col_filters") or []:
        col = f.get("col")
        op = f.get("op")
        if op not in FILTER_OPS or col not in known:
            continue
        check_ident(col, "column name")
        q = _quote(engine, col)
        tmpl = FILTER_OPS[op].replace("LIKE", like)
        parts.append("(" + tmpl.format(c=q, t=_as_text(engine, q), p=ph) + ")")
        if op in ("null", "notnull"):
            continue
        val = f.get("value", "")
        if op == "contains":
            val = f"%{val}%"
        elif op == "starts":
            val = f"{val}%"
        elif op == "ends":
            val = f"%{val}"
        args.append(val)

    raw = str(params.get("filter") or "").strip()
    if raw:
        parts.append("(" + (raw.replace("%", "%%") if args and engine != "sqlite" else raw) + ")")

    return ("WHERE " + " AND ".join(parts)) if parts else "", args

def action_browse_data(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    db_name = params.get("database")
    tbl_name = params.get("table")
    page = max(1, int(params.get("page", 1)))
    per_page = max(1, min(1000, int(params.get("per_page", 50))))
    offset = (page - 1) * per_page
    sort_col = params.get("sort_col")
    sort_dir = "DESC" if str(params.get("sort_dir", "")).upper() == "DESC" else "ASC"
    engine = instance.get("engine", "mysql")
    check_ident(tbl_name, "table name")
    if sort_col:
        check_ident(sort_col, "sort column")
    if engine == "postgres":
        check_ident(db_name or "", "database name")

    conn = _open_conn(instance, db_name)
    try:
        cur = conn.cursor()
        meta = _column_meta(engine, cur, tbl_name)
        where_clause, args = _build_where(engine, meta, params)
        t = _quote(engine, tbl_name)
        order_clause = f"ORDER BY {_quote(engine, sort_col)} {sort_dir}" if sort_col else ""

        _exec(engine, cur, f"SELECT COUNT(*) AS c FROM {t} {where_clause}", args)
        first = cur.fetchone()
        total = first["c"] if isinstance(first, dict) else first[0]

        _exec(engine, cur, f"SELECT * FROM {t} {where_clause} {order_clause} LIMIT {per_page} OFFSET {offset}", args)
        columns = [d[0] for d in cur.description] if cur.description else [m["name"] for m in meta]
        rows = [_row_dict(r, columns) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()

    return {
        "columns": columns,
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
        "meta": meta,
        "primary_keys": [m["name"] for m in meta if m["pk"]],
    }

def _pk_where(engine: str, pks: Dict[str, Any]):
    ph = "?" if engine == "sqlite" else "%s"
    where = [f"{_quote(engine, k)} IS NULL" if v is None else f"{_quote(engine, k)} = {ph}" for k, v in pks.items()]
    return " AND ".join(where), [v for v in pks.values() if v is not None]

def action_save_row(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Insert (`is_new`) or update the row identified by `pks`. An update only sets the columns in `data`."""
    db_name = params.get("database")
    tbl_name = params.get("table")
    row_data = params.get("data") or {}
    pks = params.get("pks") or {}
    is_new = bool(params.get("is_new", False))
    check_ident(tbl_name, "table name")
    for c in list(row_data.keys()) + list(pks.keys()):
        check_ident(c, "column name")
    if not is_new and not pks:
        raise RuntimeError("Primary keys required to update a row")
    if not is_new and not row_data:
        return {"ok": True, "affected": 0}

    engine = instance.get("engine", "mysql")
    ph = "?" if engine == "sqlite" else "%s"
    t = _quote(engine, tbl_name)
    conn = _open_conn(instance, db_name)
    try:
        cur = conn.cursor()
        if is_new:
            cols = list(row_data.keys())
            if cols:
                col_names = ", ".join(_quote(engine, c) for c in cols)
                cur.execute(f"INSERT INTO {t} ({col_names}) VALUES ({', '.join([ph] * len(cols))})", list(row_data.values()))
            else:
                cur.execute(f"INSERT INTO {t} () VALUES ()" if engine == "mysql" else f"INSERT INTO {t} DEFAULT VALUES")
        else:
            set_clauses = ", ".join(f"{_quote(engine, k)} = {ph}" for k in row_data.keys())
            where, where_args = _pk_where(engine, pks)
            cur.execute(f"UPDATE {t} SET {set_clauses} WHERE {where}", list(row_data.values()) + where_args)
        affected = cur.rowcount
        cur.close()
        if engine != "mysql":
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "affected": affected}

def action_delete_row(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Deletes one row (`pks`) or several (`pks_list`), each identified by its primary key values."""
    db_name = params.get("database")
    tbl_name = params.get("table")
    pks_list = params.get("pks_list") or ([params["pks"]] if params.get("pks") else [])
    if not pks_list or any(not p for p in pks_list):
        raise RuntimeError("Primary keys required to delete a row")
    check_ident(tbl_name, "table name")
    for p in pks_list:
        for c in p.keys():
            check_ident(c, "column name")

    engine = instance.get("engine", "mysql")
    t = _quote(engine, tbl_name)
    affected = 0
    conn = _open_conn(instance, db_name)
    try:
        cur = conn.cursor()
        for pks in pks_list:
            where, args = _pk_where(engine, pks)
            cur.execute(f"DELETE FROM {t} WHERE {where}", args)
            affected += max(cur.rowcount, 0)
        cur.close()
        if engine != "mysql":
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "affected": affected}

def split_sql(sql: str) -> List[str]:
    """Splits a script on ; outside quotes, backticks and comments."""
    out: List[str] = []
    buf: List[str] = []
    i, n = 0, len(sql)
    quote = None
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == "\\" and quote != "`" and i + 1 < n:
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
        elif ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j])
            i = j
            continue
        elif ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            buf.append(sql[i:j])
            i = j
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    stmt = "".join(buf).strip()
    if stmt:
        out.append(stmt)
    return out

def action_execute_query(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Runs one statement or a whole script. Returns the last result set and the total of affected rows."""
    db_name = params.get("database")
    statements = split_sql(params.get("sql", "") or "")
    if not statements:
        return {"columns": [], "rows": [], "affected": 0, "time_ms": 0, "statements": 0}

    engine = instance.get("engine", "mysql")
    start = time.time()
    columns: List[str] = []
    rows: List[Dict[str, Any]] = []
    total_rows = 0
    affected = 0

    conn = _open_conn(instance, db_name)
    try:
        cur = conn.cursor()
        for idx, stmt in enumerate(statements):
            try:
                cur.execute(stmt)
            except Exception as e:
                if engine != "mysql":
                    conn.rollback()
                prefix = f"Statement {idx + 1} of {len(statements)}: " if len(statements) > 1 else ""
                raise RuntimeError(f"{prefix}{e}")
            if cur.description:
                columns = [d[0] for d in cur.description]
                fetched = cur.fetchmany(1001)
                total_rows = len(fetched)
                rows = [_row_dict(r, columns) for r in fetched[:1000]]
            elif cur.rowcount and cur.rowcount > 0:
                affected += cur.rowcount
        cur.close()
        if engine != "mysql":
            conn.commit()
    finally:
        conn.close()

    return {
        "columns": columns,
        "rows": rows,
        "affected": affected,
        "time_ms": round((time.time() - start) * 1000, 2),
        "total_rows": total_rows,
        "truncated": total_rows > 1000,
        "statements": len(statements),
    }

# User management (HeidiSQL style)
def action_list_users(instance: Dict[str, Any]) -> Dict[str, Any]:
    engine = instance.get("engine", "mysql")
    users = []
    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT User as user, Host as host FROM mysql.user ORDER BY User, Host")
                for u in cur.fetchall():
                    # get privileges summary
                    users.append({
                        "user": u["user"],
                        "host": u["host"],
                        "privileges": "Standard" if u["user"] != "root" else "Superuser"
                    })
        finally:
            conn.close()
    elif engine == "postgres":
        conn = get_postgres_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT rolname as user FROM pg_roles ORDER BY rolname")
                for u in cur.fetchall():
                    name = u["user"]
                    users.append({
                        "user": name,
                        "host": "*",
                        "privileges": "Superuser" if name == "postgres" else "Standard"
                    })
        finally:
            conn.close()
    return {"users": users}

def action_create_user(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    user = params.get("user", "").strip()
    host = params.get("host", "%").strip()
    password = params.get("password", "").strip()
    grants = params.get("grants", ["ALL PRIVILEGES"]) # list of privileges
    database = params.get("database", "*") # * for global, or specific db

    check_ident(user, "username")
    allowed_grants = {"ALL PRIVILEGES", "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "INDEX", "ALTER"}
    grants = [g for g in grants if g in allowed_grants] or ["SELECT"]
    if database != "*":
        check_ident(database, "database name")

    engine = instance.get("engine", "mysql")
    if engine == "mysql":
        check_ident(host.replace("%", "pct").replace(".", "d").replace("-", "m").replace("/", "s") or "pct", "host")
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE USER '{user}'@'{host}' IDENTIFIED BY %s", (password,))
                grant_str = ", ".join(grants)
                db_scope = f"`{database}`.*" if database != "*" else "*.*"
                cur.execute(f"GRANT {grant_str} ON {db_scope} TO '{user}'@'{host}'")
                cur.execute("FLUSH PRIVILEGES")
        finally:
            conn.close()
    elif engine == "postgres":
        conn = get_postgres_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f'CREATE ROLE "{user}" WITH LOGIN PASSWORD %s', (password,))
                if "ALL PRIVILEGES" in grants:
                    cur.execute(f'ALTER ROLE "{user}" WITH SUPERUSER' if database == "*" else f'GRANT ALL PRIVILEGES ON DATABASE "{database}" TO "{user}"')
                else:
                    # map simple grants to CONNECT + SELECT baseline
                    if database != "*":
                        cur.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{user}"')
                conn.commit()
        finally:
            conn.close()
    return {"ok": True, "user": f"{user}@{host}"}

def action_drop_user(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    user = params.get("user")
    host = params.get("host", "%")
    if user in ("root", "postgres"):
        raise RuntimeError("Cannot drop superuser")
    check_ident(user, "username")
    engine = instance.get("engine", "mysql")
    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP USER '{user}'@'{host}'")
                cur.execute("FLUSH PRIVILEGES")
        finally:
            conn.close()
    elif engine == "postgres":
        conn = get_postgres_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP ROLE "{user}"')
                conn.commit()
        finally:
            conn.close()
    return {"ok": True}

# Logs management
def action_get_logs(instance: Dict[str, Any]) -> Dict[str, Any]:
    engine = instance.get("engine", "mysql")
    status = {"slow_query_log": "OFF", "long_query_time": "10.0", "log_file": ""}
    log_content = ""

    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW VARIABLES LIKE 'slow_query_log%'")
                for r in cur.fetchall():
                    if r["Variable_name"] == "slow_query_log":
                        status["slow_query_log"] = r["Value"]
                    elif r["Variable_name"] == "slow_query_log_file":
                        status["log_file"] = r["Value"]
                cur.execute("SHOW VARIABLES LIKE 'long_query_time'")
                r = cur.fetchone()
                if r:
                    status["long_query_time"] = r["Value"]

            # Try to read last 50 lines of slow log if file exists
            fpath = status.get("log_file")
            if fpath and os.path.exists(fpath):
                log_content = tail_file(fpath, 100)
        finally:
            conn.close()
    return {"status": status, "content": log_content}

def action_toggle_log(instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    enable = bool(params.get("enable", True))
    threshold = float(params.get("threshold", 2.0))
    engine = instance.get("engine", "mysql")
    if engine == "mysql":
        conn = get_mysql_conn(instance)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET GLOBAL slow_query_log = '{'ON' if enable else 'OFF'}'")
                cur.execute(f"SET GLOBAL long_query_time = {threshold}")
        finally:
            conn.close()
    return {"ok": True, "enabled": enable, "threshold": threshold}

PAYLOAD_NAME_RE = re.compile(r"^[a-z0-9-]{8,64}$")
PAYLOAD_SUBDIR = ".config/ervisio/plugins/database/tmp"

def read_payload_file(name: str) -> str:
    """Large or secret payloads are written by the UI (sdk.files) to ~/.config/ervisio/plugins/database/tmp/<name>.json
    of the signed-in user. The bridge may run as that user or as root through sudo, so look in both homes.
    The file is removed once read."""
    if not PAYLOAD_NAME_RE.match(name):
        raise RuntimeError("bad payload name")
    homes = [os.path.expanduser("~")]
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            homes.insert(0, pwd.getpwnam(sudo_user).pw_dir)
        except Exception:
            pass
    if IS_WINDOWS:
        users = os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Users", "*")
        homes += sorted(glob.glob(users))
    else:
        homes += sorted(glob.glob("/home/*")) + ["/root"]
    for home in homes:
        path = os.path.join(home, PAYLOAD_SUBDIR, name + ".json")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = fh.read()
            try:
                os.remove(path)
            except OSError:
                pass
            return data
    raise RuntimeError("payload file not found")

def main():
    if len(sys.argv) < 2:
        send_error("Action argument missing")

    action = sys.argv[1]
    raw_payload = sys.argv[2] if len(sys.argv) > 2 else ""
    params = {}
    if raw_payload.startswith("@file:"):
        try:
            params = json.loads(read_payload_file(raw_payload[len("@file:"):]))
        except Exception as e:
            send_error(f"Invalid payload file: {e}")
    elif raw_payload:
        try:
            decoded = base64.b64decode(raw_payload).decode("utf-8")
            params = json.loads(decoded)
        except Exception as e:
            # Fallback to direct json if not base64
            try:
                params = json.loads(raw_payload)
            except Exception:
                send_error(f"Invalid payload encoding: {e}")

    instance = params.get("instance", {})

    try:
        if action == "detect":
            send_json(action_detect())
        elif action == "install-winget":
            send_json(action_install_winget(params))
        elif action == "install-docker":
            send_json(action_install_docker(params))
        elif action == "install-apt":
            send_json(action_install_apt(params))
        elif action == "create-sqlite":
            send_json(action_create_sqlite(params))
        elif action == "manage-instance":
            send_json(action_manage_instance(params))
        elif action == "list-databases":
            send_json(action_list_databases(instance))
        elif action == "create-database":
            send_json(action_create_database(instance, params))
        elif action == "drop-database":
            send_json(action_drop_database(instance, params))
        elif action == "list-tables":
            send_json(action_list_tables(instance, params))
        elif action == "table-structure":
            send_json(action_table_structure(instance, params))
        elif action == "browse-data":
            send_json(action_browse_data(instance, params))
        elif action == "save-row":
            send_json(action_save_row(instance, params))
        elif action == "delete-row":
            send_json(action_delete_row(instance, params))
        elif action == "query":
            send_json(action_execute_query(instance, params))
        elif action == "list-users":
            send_json(action_list_users(instance))
        elif action == "create-user":
            send_json(action_create_user(instance, params))
        elif action == "drop-user":
            send_json(action_drop_user(instance, params))
        elif action == "get-logs":
            send_json(action_get_logs(instance))
        elif action == "toggle-log":
            send_json(action_toggle_log(instance, params))
        else:
            send_error(f"Unknown action: {action}")
    except Exception as e:
        send_error(str(e))

if __name__ == "__main__":
    main()
