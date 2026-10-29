#!/usr/bin/env python3
"""
Ervisio Database Plugin Bridge (db-bridge.py)
Provides unified HeidiSQL-style management for MySQL, MariaDB, PostgreSQL, SQLite, MongoDB and Redis/Valkey.
Communicates via JSON over stdout.
"""
import sys
import os
import json
import base64
import time
import socket
import shlex
import fnmatch
import subprocess
import glob
from typing import Dict, Any, List, Optional, Tuple

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

def run_cmd(cmd: List[str], timeout: int = 300, input: Optional[str] = None) -> str:
    try:
        extra: Dict[str, Any] = {}
        if IS_WINDOWS:
            extra["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, input=input,
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
    """Database services registered with the Windows service manager."""
    script = (
        "Get-Service | Where-Object { $_.Name -match '^(mariadb|mysql|postgresql|mongodb|redis|memurai|valkey)' } | "
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

SYSTEMD_UNITS = [
    (re.compile(r"^mariadb(@.*)?\.service$"), ("mariadb", "mysql", 3306, "MariaDB")),
    (re.compile(r"^mysqld?(@.*)?\.service$"), ("mysql", "mysql", 3306, "MySQL")),
    (re.compile(r"^postgresql(@.*)?\.service$"), ("postgres", "postgres", 5432, "PostgreSQL")),
    (re.compile(r"^(mongod|mongodb)\.service$"), ("mongodb", "mongodb", 27017, "MongoDB")),
    (re.compile(r"^redis(-server)?(@.*)?\.service$"), ("redis", "redis", 6379, "Redis")),
    (re.compile(r"^valkey(-server)?(@.*)?\.service$"), ("valkey", "redis", 6379, "Valkey")),
    (re.compile(r"^keydb(-server)?\.service$"), ("redis", "redis", 6379, "KeyDB")),
]

# Image name (last path segment, without tag) -> (type, engine, container port)
DOCKER_IMAGES = {
    "mariadb": ("mariadb", "mysql", 3306),
    "mysql": ("mysql", "mysql", 3306),
    "mysql-server": ("mysql", "mysql", 3306),
    "postgres": ("postgres", "postgres", 5432),
    "postgresql": ("postgres", "postgres", 5432),
    "postgis": ("postgres", "postgres", 5432),
    "mongo": ("mongodb", "mongodb", 27017),
    "mongodb": ("mongodb", "mongodb", 27017),
    "mongodb-community-server": ("mongodb", "mongodb", 27017),
    "mongodb-enterprise-server": ("mongodb", "mongodb", 27017),
    "redis": ("redis", "redis", 6379),
    "redis-stack": ("redis", "redis", 6379),
    "redis-stack-server": ("redis", "redis", 6379),
    "keydb": ("redis", "redis", 6379),
    "valkey": ("valkey", "redis", 6379),
}

def docker_engine(image: str) -> Optional[Tuple[str, str, int]]:
    base = image.lower().split("@")[0].rsplit("/", 1)[-1].split(":")[0]
    return DOCKER_IMAGES.get(base)

PORT_MAP_RE = re.compile(r"(?:[\d.]+|\[[0-9a-f:]*\]|::)?:(\d+)->(\d+)/tcp")

def docker_host_port(ports: str, container_port: int) -> int:
    """Host port published for the engine port (0.0.0.0:13306->3306/tcp), else the first published one."""
    maps = [(int(h), int(c)) for h, c in PORT_MAP_RE.findall(ports)]
    for h, c in maps:
        if c == container_port:
            return h
    return maps[0][0] if maps else container_port

LOCAL_PORTS = {
    3306: ("mysql", "mysql", "MySQL / MariaDB"),
    5432: ("postgres", "postgres", "PostgreSQL"),
    27017: ("mongodb", "mongodb", "MongoDB"),
    6379: ("redis", "redis", "Redis"),
}

def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

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
                elif low.startswith("mongodb"):
                    db_type, engine, port, label = "mongodb", "mongodb", 27017, "MongoDB"
                elif low.startswith(("redis", "memurai")):
                    db_type, engine, port, label = "redis", "redis", 6379, "Memurai" if low.startswith("memurai") else "Redis"
                elif low.startswith("valkey"):
                    db_type, engine, port, label = "valkey", "redis", 6379, "Valkey"
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
            seen = set()
            for line in units_out.splitlines():
                parts = line.replace("●", " ").split()
                if not parts:
                    continue
                unit = parts[0]
                hit = None
                for pattern, spec in SYSTEMD_UNITS:
                    if pattern.match(unit):
                        hit = spec
                        break
                if not hit:
                    continue
                db_type, engine, port, label = hit
                if db_type in seen or (db_type == "mysql" and "mariadb" in seen):
                    continue
                seen.add(db_type)
                instances.append({
                    "id": f"native-{db_type}",
                    "name": f"{label} (Native Systemd)",
                    "type": db_type,
                    "engine": engine,
                    "mode": "native",
                    "service": unit,
                    "host": "127.0.0.1",
                    "port": port,
                    "status": "running" if "running" in line else "stopped",
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
                spec = docker_engine(image)
                if not spec:
                    continue
                db_type, engine, default_port = spec
                instances.append({
                    "id": f"docker-{cid[:12]}",
                    "name": f"{name} ({image})",
                    "container": name,
                    "type": db_type,
                    "engine": engine,
                    "mode": "docker",
                    "host": "127.0.0.1",
                    "port": docker_host_port(ports, default_port),
                    "status": "running" if "Up" in status else "stopped",
                    "version": image
                })
    except Exception:
        pass

    # 3. Engines listening on their default local port that neither the service manager nor Docker reported
    #    (started by hand, portable builds, other service managers).
    taken = {i.get("port") for i in instances if i.get("status") == "running"}
    for port, (db_type, engine, label) in LOCAL_PORTS.items():
        if port in taken or not port_open("127.0.0.1", port):
            continue
        instances.append({
            "id": f"local-{db_type}-{port}",
            "name": f"{label} (localhost:{port})",
            "type": db_type,
            "engine": engine,
            "mode": "local",
            "host": "127.0.0.1",
            "port": port,
            "status": "running",
            "version": "local"
        })

    # 4. Known SQLite files (/home/ubuntu and /var/lib; the profile and ProgramData on Windows)
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
            "mongodb": ["8.0", "7.0", "6.0"],
            "redis": ["7.4", "7.2", "6.2"],
            "valkey": ["8.1", "8.0", "7.2"],
            "sqlite": ["3.x (system)"]
        }
    }

DOCKER_SPECS: Dict[str, Dict[str, Any]] = {
    "mysql": {"image": "mysql", "port": 3306, "data": "/var/lib/mysql", "env": ["MYSQL_ROOT_PASSWORD={pw}"]},
    "mariadb": {"image": "mariadb", "port": 3306, "data": "/var/lib/mysql", "env": ["MYSQL_ROOT_PASSWORD={pw}"]},
    "postgres": {"image": "postgres", "port": 5432, "data": "/var/lib/postgresql/data", "env": ["POSTGRES_PASSWORD={pw}"]},
    "mongodb": {"image": "mongo", "port": 27017, "data": "/data/db",
                "env": ["MONGO_INITDB_ROOT_USERNAME=root", "MONGO_INITDB_ROOT_PASSWORD={pw}"]},
    "redis": {"image": "redis", "port": 6379, "data": "/data",
              "cmd": ["redis-server", "--requirepass", "{pw}", "--appendonly", "yes"]},
    "valkey": {"image": "valkey/valkey", "port": 6379, "data": "/data",
               "cmd": ["valkey-server", "--requirepass", "{pw}", "--appendonly", "yes"]},
}
CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

def action_install_docker(params: Dict[str, Any]) -> Dict[str, Any]:
    engine = params.get("engine", "mysql")
    spec = DOCKER_SPECS.get(engine)
    if not spec:
        raise RuntimeError(f"Unsupported engine: {engine}")
    version = str(params.get("version", "latest")).strip()
    name = str(params.get("name", f"db-{engine}")).strip()
    password = str(params.get("password", "")).strip()
    port = int(params.get("port") or spec["port"])
    if not CONTAINER_RE.match(name):
        raise RuntimeError(f"Invalid container name: {name!r}")
    if not TAG_RE.match(version):
        raise RuntimeError(f"Invalid version: {version!r}")
    if not password:
        raise RuntimeError("A password is required")

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{port}:{spec['port']}",
        "-v", f"{name}-data:{spec['data']}",
    ]
    for e in spec.get("env", []):
        cmd += ["-e", e.format(pw=password)]
    cmd.append(f"{spec['image']}:{version}")
    cmd += [a.format(pw=password) for a in spec.get("cmd", [])]

    cid = run_cmd(cmd).strip()
    return {"ok": True, "container_id": cid, "name": name, "port": port}

WINGET_IDS = {
    "mariadb": "MariaDB.Server",
    "mysql": "Oracle.MySQL",
    "postgres": "PostgreSQL.PostgreSQL.17",
    "mongodb": "MongoDB.Server",
    "redis": "Memurai.MemuraiDeveloper",
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
    engine = params.get("engine", "mariadb")
    if engine == "mongodb":
        return install_mongodb_apt()
    pkg_map = {
        "mariadb": "mariadb-server",
        "mysql": "mysql-server",
        "postgres": "postgresql",
        "redis": "redis-server",
        "valkey": "valkey-server",
        "sqlite": "sqlite3"
    }
    services = {"mariadb": "mariadb", "mysql": "mysql", "postgres": "postgresql", "redis": "redis-server", "valkey": "valkey-server"}
    pkg = pkg_map.get(engine)
    if not pkg:
        raise RuntimeError(f"Unsupported engine: {engine}")
    run_cmd(privileged(["apt-get", "update"]), timeout=120)
    run_cmd(privileged(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", pkg]), timeout=300)
    if engine in services:
        run_cmd(privileged(["systemctl", "enable", "--now", services[engine]]))
    return {"ok": True, "engine": engine, "package": pkg}

MONGO_SERIES = "8.0"

def install_mongodb_apt() -> Dict[str, Any]:
    """MongoDB is not in the Debian/Ubuntu archives: adds the official repo.mongodb.org repository, then installs mongodb-org."""
    import urllib.request
    info: Dict[str, str] = {}
    with open("/etc/os-release", encoding="utf-8") as fh:
        for line in fh:
            k, _, v = line.strip().partition("=")
            info[k] = v.strip('"')
    distro = info.get("ID", "")
    codename = info.get("VERSION_CODENAME") or info.get("UBUNTU_CODENAME", "")
    if distro not in ("ubuntu", "debian") or not re.match(r"^[a-z]+$", codename):
        raise RuntimeError("Native MongoDB needs Ubuntu or Debian; deploy it with Docker instead")
    keyring = f"/usr/share/keyrings/mongodb-server-{MONGO_SERIES}.gpg"
    component = "multiverse" if distro == "ubuntu" else "main"
    repo = (f"deb [ arch=amd64,arm64 signed-by={keyring} ] https://repo.mongodb.org/apt/{distro} "
            f"{codename}/mongodb-org/{MONGO_SERIES} {component}\n")
    with urllib.request.urlopen(f"https://pgp.mongodb.com/server-{MONGO_SERIES}.asc", timeout=30) as resp:
        key = resp.read().decode("ascii")
    run_cmd(privileged(["apt-get", "update"]), timeout=120)
    run_cmd(privileged(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "gnupg"]), timeout=300)
    run_cmd(privileged(["gpg", "--batch", "--yes", "--dearmor", "-o", keyring]), input=key)
    run_cmd(privileged(["tee", f"/etc/apt/sources.list.d/mongodb-org-{MONGO_SERIES}.list"]), input=repo)
    try:
        run_cmd(privileged(["apt-get", "update"]), timeout=120)
    except RuntimeError as e:
        raise RuntimeError(f"MongoDB {MONGO_SERIES} has no packages for {distro} {codename}: {e}")
    run_cmd(privileged(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "mongodb-org"]), timeout=600)
    run_cmd(privileged(["systemctl", "enable", "--now", "mongod"]))
    return {"ok": True, "engine": "mongodb", "package": "mongodb-org"}

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
        if SERVICE_RE.match(target or "") and target.endswith(".service"):
            svc = target
        else:
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

# ---------------------------------------------------------------------------
# Docker credentials: a detected container carries no password; read the one it was started with.
# ---------------------------------------------------------------------------

def fill_docker_credentials(instance: Dict[str, Any]) -> None:
    if instance.get("mode") != "docker" or instance.get("password") or not instance.get("container"):
        return
    try:
        cfg = json.loads(run_cmd(["docker", "inspect", "--format", "{{json .Config}}", instance["container"]], timeout=20))
    except Exception:
        return
    env = dict(e.split("=", 1) for e in (cfg.get("Env") or []) if "=" in e)
    engine = instance.get("engine")
    user = None
    pw = None
    if engine == "mysql":
        pw = env.get("MYSQL_ROOT_PASSWORD") or env.get("MARIADB_ROOT_PASSWORD")
        user = "root"
    elif engine == "postgres":
        pw = env.get("POSTGRES_PASSWORD")
        user = env.get("POSTGRES_USER") or "postgres"
    elif engine == "mongodb":
        pw = env.get("MONGO_INITDB_ROOT_PASSWORD")
        user = env.get("MONGO_INITDB_ROOT_USERNAME")
    elif engine == "redis":
        args = list(cfg.get("Entrypoint") or []) + list(cfg.get("Cmd") or [])
        for i, a in enumerate(args):
            if a == "--requirepass" and i + 1 < len(args):
                pw = args[i + 1]
        pw = pw or env.get("REDIS_PASSWORD") or env.get("VALKEY_PASSWORD")
    if pw:
        instance["password"] = pw
    if user and not instance.get("user"):
        instance["user"] = user

# ---------------------------------------------------------------------------
# MongoDB: databases -> databases, collections -> tables, documents -> rows (top-level fields as columns).
# Values travel as relaxed Extended JSON ({"$oid": ...}, {"$date": ...}) so ObjectId and dates round-trip.
# ---------------------------------------------------------------------------

MONGO_DB_RE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
MONGO_COLL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}$")
MONGO_SYSTEM_DBS = ("admin", "local", "config")

def _bson():
    try:
        from bson import json_util
        return json_util
    except ImportError:
        raise RuntimeError("The Python package 'pymongo' is missing: pip install pymongo")

def get_mongo_client(instance: Dict[str, Any]):
    try:
        import pymongo
    except ImportError:
        raise RuntimeError("The Python package 'pymongo' is missing: pip install pymongo")
    if instance.get("uri"):
        return pymongo.MongoClient(instance["uri"], serverSelectionTimeoutMS=5000)
    kw: Dict[str, Any] = {
        "host": instance.get("host") or "127.0.0.1",
        "port": int(instance.get("port") or 27017),
        "serverSelectionTimeoutMS": 5000,
        "connectTimeoutMS": 5000,
        "directConnection": True,
    }
    if instance.get("user"):
        kw["username"] = instance["user"]
        kw["password"] = instance.get("password", "")
        kw["authSource"] = instance.get("auth_db") or "admin"
    return pymongo.MongoClient(**kw)

def _check_mongo_db(name: str) -> str:
    if not name or not MONGO_DB_RE.match(name):
        raise RuntimeError(f"Invalid database name: {name!r}")
    return name

def _check_mongo_coll(name: str) -> str:
    if not name or not MONGO_COLL_RE.match(name):
        raise RuntimeError(f"Invalid collection name: {name!r}")
    return name

def mdoc(value: Any) -> Any:
    """BSON value -> plain JSON value (relaxed Extended JSON)."""
    ju = _bson()
    return json.loads(ju.dumps(value, json_options=ju.RELAXED_JSON_OPTIONS))

def mparse(value: Any) -> Any:
    """Plain JSON value (Extended JSON allowed) -> BSON value."""
    ju = _bson()
    return ju.loads(json.dumps(value))

def btype(v: Any) -> str:
    from bson import ObjectId, Decimal128, Binary
    import datetime
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int" if -2**31 <= v < 2**31 else "long"
    if isinstance(v, float):
        return "double"
    if isinstance(v, str):
        return "string"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    if isinstance(v, ObjectId):
        return "objectId"
    if isinstance(v, datetime.datetime):
        return "date"
    if isinstance(v, Decimal128):
        return "decimal"
    if isinstance(v, (bytes, Binary)):
        return "binData"
    return type(v).__name__

_SHELL_WRAP = {"ObjectId": "$oid", "ISODate": "$date", "Date": "$date", "NumberLong": "$numberLong",
               "NumberInt": "$numberInt", "NumberDecimal": "$numberDecimal", "UUID": "$uuid"}

def shell_to_json(src: str) -> str:
    """mongosh literal syntax -> JSON: unquoted keys, 'single quotes', ObjectId("..."), ISODate("..."), trailing commas."""
    out: List[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            j = i + 1
            buf: List[str] = []
            while j < n and src[j] != ch:
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    buf.append(nxt if (ch == "'" and nxt == "'") else src[j:j + 2])
                    j += 2
                    continue
                buf.append('\\"' if (ch == "'" and src[j] == '"') else src[j])
                j += 1
            out.append('"' + "".join(buf) + '"')
            i = j + 1
            continue
        if ch.isalpha() or ch in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$."):
                j += 1
            word = src[i:j]
            k = j
            while k < n and src[k].isspace():
                k += 1
            if word == "new":
                i = k
                continue
            if word in _SHELL_WRAP and k < n and src[k] == "(":
                e = src.find(")", k)
                if e < 0:
                    raise RuntimeError(f"Unclosed {word}(")
                inner = src[k + 1:e].strip().strip("\"'")
                if _SHELL_WRAP[word] == "$date":
                    if not inner:
                        inner = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    elif "T" not in inner:
                        inner += "T00:00:00Z"
                out.append(json.dumps({_SHELL_WRAP[word]: inner}))
                i = e + 1
                continue
            if k < n and src[k] == ":":
                out.append(json.dumps(word))
            else:
                out.append(word)
            i = j
            continue
        out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))

def shell_args(text: str) -> List[Any]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        return _bson().loads("[" + shell_to_json(text) + "]")
    except Exception as e:
        raise RuntimeError(f"Cannot parse arguments ({e}): {text[:200]}")

def _split_chain(s: str) -> List[Tuple[str, Optional[str]]]:
    """db.users.find({...}).sort({...}).limit(5) -> [('db', None), ('users', None), ('find', '{...}'), ...]"""
    parts: List[Tuple[str, Optional[str]]] = []
    i, n = 0, len(s)
    while i < n:
        j = i
        while j < n and s[j] not in ".(":
            j += 1
        name = s[i:j].strip()
        if j < n and s[j] == "(":
            depth, k, quote = 0, j, None
            while k < n:
                c = s[k]
                if quote:
                    if c == "\\":
                        k += 2
                        continue
                    if c == quote:
                        quote = None
                elif c in "\"'":
                    quote = c
                elif c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            if depth:
                raise RuntimeError("Unbalanced parentheses")
            parts.append((name, s[j + 1:k]))
            i = k + 1
            while i < n and s[i].isspace():
                i += 1
            if i < n:
                if s[i] != ".":
                    raise RuntimeError(f"Unexpected {s[i]!r} after {name}()")
                i += 1
        else:
            parts.append((name, None))
            i = j + 1
    return parts

def _docs_result(docs: List[Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    rows = [mdoc(d) if isinstance(d, dict) else {"value": mdoc(d)} for d in docs]
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols, rows

def mongo_run_statement(client, db_name: str, stmt: str) -> Dict[str, Any]:
    """One mongosh-style statement. Returns {columns, rows, affected}."""
    stmt = stmt.strip().rstrip(";").strip()
    low = stmt.lower()
    if low in ("show dbs", "show databases"):
        cols, rows = _docs_result([{"name": d["name"], "sizeOnDisk": d.get("sizeOnDisk")} for d in client.list_databases()])
        return {"columns": cols, "rows": rows, "affected": 0}
    if low in ("show collections", "show tables"):
        return {"columns": ["name"], "rows": [{"name": c} for c in sorted(client[db_name].list_collection_names())], "affected": 0}
    if stmt.startswith("{"):
        args = shell_args(stmt)
        res = client[db_name or "admin"].command(args[0])
        cols, rows = _docs_result([res])
        return {"columns": cols, "rows": rows, "affected": 0}

    chain = _split_chain(stmt)
    if not chain or chain[0] != ("db", None):
        raise RuntimeError("Write a mongosh command such as db.users.find({ age: { $gt: 30 } }), or a JSON command document")
    if not db_name:
        raise RuntimeError("Pick a database first")
    db = client[db_name]
    rest = chain[1:]
    names: List[str] = []
    while rest and rest[0][1] is None:
        names.append(rest.pop(0)[0])
    if not rest:
        raise RuntimeError("Missing method call, e.g. .find()")
    method, raw = rest.pop(0)
    args = shell_args(raw or "")

    if not names:
        if method == "getCollection":
            names = [str(args[0])]
            if not rest:
                raise RuntimeError("Missing method call after getCollection()")
            method, raw = rest.pop(0)
            args = shell_args(raw or "")
        else:
            if method in ("runCommand", "adminCommand"):
                target = client.admin if method == "adminCommand" else db
                cmd = args[0] if args else {}
                res = target.command(cmd if not isinstance(cmd, str) else {cmd: 1})
            elif method == "getCollectionNames":
                return {"columns": ["name"], "rows": [{"name": c} for c in sorted(db.list_collection_names())], "affected": 0}
            elif method == "stats":
                res = db.command("dbStats")
            elif method == "createCollection":
                db.create_collection(_check_mongo_coll(str(args[0])), **(args[1] if len(args) > 1 else {}))
                return {"columns": [], "rows": [], "affected": 1}
            elif method == "dropDatabase":
                if db_name in MONGO_SYSTEM_DBS:
                    raise RuntimeError(f"Cannot drop system database: {db_name}")
                client.drop_database(db_name)
                return {"columns": [], "rows": [], "affected": 1}
            else:
                raise RuntimeError(f"Unsupported database method: db.{method}()")
            cols, rows = _docs_result([res])
            return {"columns": cols, "rows": rows, "affected": 0}

    coll = db[".".join(names)]
    a = lambda i, default=None: args[i] if len(args) > i else default
    if method in ("find", "findOne"):
        cur = coll.find(a(0, {}), a(1))
        limit = 1 if method == "findOne" else 1001
        for mod, mraw in rest:
            margs = shell_args(mraw or "")
            if mod == "sort":
                spec = margs[0] if margs else {}
                cur = cur.sort(list(spec.items()))
            elif mod == "limit":
                limit = min(int(margs[0]), 1001) if margs and int(margs[0]) > 0 else limit
            elif mod == "skip":
                cur = cur.skip(int(margs[0]))
            elif mod == "projection":
                cur = coll.find(a(0, {}), margs[0])
            elif mod in ("toArray", "pretty"):
                pass
            elif mod in ("count", "itcount", "size"):
                return {"columns": ["count"], "rows": [{"count": coll.count_documents(a(0, {}))}], "affected": 0}
            else:
                raise RuntimeError(f"Unsupported cursor method: .{mod}()")
        cols, rows = _docs_result(list(cur.limit(limit)))
        return {"columns": cols, "rows": rows, "affected": 0}
    if method == "aggregate":
        pipeline = a(0, [])
        cols, rows = _docs_result(list(coll.aggregate(pipeline if isinstance(pipeline, list) else args)))
        return {"columns": cols, "rows": rows[:1001], "affected": 0}
    if method in ("countDocuments", "count"):
        return {"columns": ["count"], "rows": [{"count": coll.count_documents(a(0, {}))}], "affected": 0}
    if method == "estimatedDocumentCount":
        return {"columns": ["count"], "rows": [{"count": coll.estimated_document_count()}], "affected": 0}
    if method == "distinct":
        vals = coll.distinct(a(0), a(1, {}))
        return {"columns": ["value"], "rows": [{"value": mdoc(v)} for v in vals], "affected": 0}
    if method == "insertOne":
        res = coll.insert_one(a(0, {}))
        return {"columns": ["insertedId"], "rows": [{"insertedId": mdoc(res.inserted_id)}], "affected": 1}
    if method == "insertMany":
        res = coll.insert_many(a(0, []))
        return {"columns": ["insertedId"], "rows": [{"insertedId": mdoc(x)} for x in res.inserted_ids], "affected": len(res.inserted_ids)}
    if method in ("updateOne", "updateMany", "replaceOne"):
        fn = {"updateOne": coll.update_one, "updateMany": coll.update_many, "replaceOne": coll.replace_one}[method]
        opts = a(2, {}) or {}
        res = fn(a(0, {}), a(1, {}), upsert=bool(opts.get("upsert")))
        return {"columns": [], "rows": [], "affected": res.modified_count + (1 if res.upserted_id is not None else 0)}
    if method in ("deleteOne", "deleteMany"):
        res = (coll.delete_one if method == "deleteOne" else coll.delete_many)(a(0, {}))
        return {"columns": [], "rows": [], "affected": res.deleted_count}
    if method == "drop":
        coll.drop()
        return {"columns": [], "rows": [], "affected": 1}
    if method == "createIndex":
        name = coll.create_index(list((a(0) or {}).items()), **(a(1, {}) or {}))
        return {"columns": ["index"], "rows": [{"index": name}], "affected": 0}
    if method == "dropIndex":
        coll.drop_index(a(0))
        return {"columns": [], "rows": [], "affected": 0}
    if method == "getIndexes":
        cols, rows = _docs_result(list(coll.list_indexes()))
        return {"columns": cols, "rows": rows, "affected": 0}
    if method == "stats":
        cols, rows = _docs_result([db.command("collStats", coll.name)])
        return {"columns": cols, "rows": rows, "affected": 0}
    raise RuntimeError(f"Unsupported collection method: .{method}()")

def _mongo_sample_fields(coll, limit: int = 300) -> Tuple[List[str], Dict[str, Dict[str, int]], int]:
    """Field names (in first-seen order, _id first), type counts per field and how many documents were sampled."""
    order: List[str] = ["_id"]
    types: Dict[str, Dict[str, int]] = {"_id": {}}
    seen = 0
    for doc in coll.find({}, limit=limit):
        seen += 1
        for k, v in doc.items():
            if k not in types:
                types[k] = {}
                order.append(k)
            t = btype(v)
            types[k][t] = types[k].get(t, 0) + 1
    return order, types, seen

def _main_type(counts: Dict[str, int]) -> str:
    real = {k: v for k, v in counts.items() if k != "null"}
    return max(real, key=real.get) if real else ("null" if counts else "mixed")

def _coerce(text: Any, like: Any = None) -> Any:
    """A grid value (text) -> BSON value. Keeps strings for string fields, parses JSON/numbers/booleans otherwise."""
    if text is None or not isinstance(text, str):
        return text
    if isinstance(like, str):
        return text
    s = text.strip()
    if like is None and not re.match(r'^(-?\d+(\.\d+)?([eE][-+]?\d+)?|true|false|null|[{\["].*)$', s, re.S):
        return text
    try:
        return _bson().loads(s)
    except Exception:
        try:
            return _bson().loads(shell_to_json(s))
        except Exception:
            return text

def _mongo_filter(params: Dict[str, Any], fields: List[str], types: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    clauses: List[Dict[str, Any]] = []
    search = str(params.get("search") or "").strip()
    if search:
        rx = {"$regex": re.escape(search), "$options": "i"}
        ors: List[Dict[str, Any]] = [{f: rx} for f in fields if "string" in types.get(f, {})]
        if re.match(r"^[0-9a-fA-F]{24}$", search):
            from bson import ObjectId
            ors.append({"_id": ObjectId(search)})
        clauses.append({"$or": ors} if ors else {"_id": None})
    for f in params.get("col_filters") or []:
        col, op, val = f.get("col"), f.get("op"), f.get("value", "")
        if not col or col.startswith("$"):
            continue
        typed = _coerce(val)
        if col == "_id" and isinstance(val, str) and re.match(r"^[0-9a-fA-F]{24}$", val.strip()):
            from bson import ObjectId
            typed = ObjectId(val.strip())
        if op == "eq":
            clauses.append({col: {"$in": [typed, val]} if typed != val else val})
        elif op == "ne":
            clauses.append({col: {"$nin": [typed, val]} if typed != val else {"$ne": val}})
        elif op in ("gt", "lt", "gte", "lte"):
            clauses.append({col: {f"${op}": typed}})
        elif op in ("contains", "starts", "ends"):
            pat = re.escape(str(val))
            pat = pat if op == "contains" else ("^" + pat if op == "starts" else pat + "$")
            clauses.append({col: {"$regex": pat, "$options": "i"}})
        elif op == "null":
            clauses.append({col: None})
        elif op == "notnull":
            clauses.append({col: {"$ne": None}})
    raw = str(params.get("filter") or "").strip()
    if raw:
        parsed = shell_args(raw)
        if not parsed or not isinstance(parsed[0], dict):
            raise RuntimeError("The filter must be a JSON object, e.g. { status: 'active', age: { $gt: 30 } }")
        clauses.append(parsed[0])
    if not clauses:
        return {}
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}

def _mongo_id(pks: Dict[str, Any]) -> Any:
    if "_id" not in pks:
        raise RuntimeError("_id is required to identify the document")
    return mparse(pks["_id"])

class MongoDriver:
    def __init__(self, instance: Dict[str, Any]):
        self.client = get_mongo_client(instance)

    def close(self):
        self.client.close()

    def list_databases(self, params):
        dbs = []
        for d in self.client.list_databases():
            name = d["name"]
            try:
                count = len(self.client[name].list_collection_names())
            except Exception:
                count = None
            dbs.append({"name": name, "size_bytes": d.get("sizeOnDisk"), "tables_count": count})
        return {"databases": sorted(dbs, key=lambda x: x["name"])}

    def create_database(self, params):
        name = str(params.get("name", "")).strip()
        db_name, _, coll = name.partition(".")
        _check_mongo_db(db_name)
        coll = _check_mongo_coll(coll or str(params.get("collection") or "data"))
        if db_name in self.client.list_database_names():
            raise RuntimeError(f"Database {db_name} already exists")
        self.client[db_name].create_collection(coll)
        return {"ok": True, "created": db_name}

    def drop_database(self, params):
        name = _check_mongo_db(str(params.get("name", "")).strip())
        if name in MONGO_SYSTEM_DBS:
            raise RuntimeError(f"Cannot drop system database: {name}")
        self.client.drop_database(name)
        return {"ok": True, "dropped": name}

    def list_tables(self, params):
        db = self.client[_check_mongo_db(params.get("database") or "")]
        tables = []
        for c in db.list_collections():
            kind = c.get("type", "collection")
            name = c["name"]
            entry = {"name": name, "type": "VIEW" if kind == "view" else kind, "engine": "MongoDB",
                     "rows_count": None, "data_size": 0, "index_size": 0, "collation": "", "comment": ""}
            if kind != "view":
                try:
                    st = db.command("collStats", name)
                    entry.update(rows_count=st.get("count", 0), data_size=st.get("size", 0), index_size=st.get("totalIndexSize", 0))
                except Exception:
                    try:
                        entry["rows_count"] = db[name].estimated_document_count()
                    except Exception:
                        pass
            tables.append(entry)
        return {"tables": sorted(tables, key=lambda x: x["name"]), "database": params.get("database")}

    def table_structure(self, params):
        db = self.client[_check_mongo_db(params.get("database") or "")]
        coll = db[_check_mongo_coll(params.get("table") or "")]
        order, types, seen = _mongo_sample_fields(coll, 500)
        indexed: Dict[str, str] = {}
        indexes = []
        create = []
        for idx in coll.list_indexes():
            keys = list(idx["key"].items())
            unique = bool(idx.get("unique")) or idx["name"] == "_id_"
            for f, _ in keys:
                indexed.setdefault(f, "UNI" if unique and len(keys) == 1 else "MUL")
            indexes.append({"name": idx["name"], "column": ", ".join(f"{f} {'ASC' if d == 1 else 'DESC' if d == -1 else d}" for f, d in keys),
                            "unique": unique, "seq": 1})
            if idx["name"] != "_id_":
                opts = {k: v for k, v in idx.items() if k not in ("v", "key", "ns")}
                create.append(f"db.{coll.name}.createIndex({json.dumps(dict(keys))}, {json.dumps(mdoc(opts))});")
        columns = []
        for f in order:
            counts = types.get(f, {})
            present = sum(counts.values())
            columns.append({
                "name": f,
                "type": " | ".join(sorted(counts, key=counts.get, reverse=True)) or "objectId",
                "nullable": "NO" if f == "_id" or (present == seen and "null" not in counts) else "YES",
                "key_type": "PRI" if f == "_id" else indexed.get(f, ""),
                "default_val": None,
                "extra": "auto (ObjectId)" if f == "_id" else "",
                "comment": f"{round(100 * present / seen)}% of {seen} sampled documents" if seen else "",
            })
        info = next(iter(db.list_collections(filter={"name": coll.name})), {})
        options = mdoc(info.get("options") or {})
        create.insert(0, f"db.createCollection({json.dumps(coll.name)}, {json.dumps(options, indent=2)});")
        return {"columns": columns, "indexes": indexes, "create_sql": "\n".join(create)}

    def browse_data(self, params):
        db = self.client[_check_mongo_db(params.get("database") or "")]
        coll = db[_check_mongo_coll(params.get("table") or "")]
        page = max(1, int(params.get("page", 1)))
        per_page = max(1, min(1000, int(params.get("per_page", 50))))
        order, types, _ = _mongo_sample_fields(coll)
        flt = _mongo_filter(params, order, types)
        total = coll.count_documents(flt)
        cur = coll.find(flt)
        sort_col = params.get("sort_col")
        if sort_col:
            cur = cur.sort(sort_col, -1 if str(params.get("sort_dir", "")).upper() == "DESC" else 1)
        docs = list(cur.skip((page - 1) * per_page).limit(per_page))
        for d in docs:
            for k, v in d.items():
                if k not in types:
                    types[k] = {}
                    order.append(k)
                types[k][btype(v)] = types[k].get(btype(v), 0) + 1
        rows = [mdoc(d) for d in docs]
        meta = [{"name": f, "type": _main_type(types.get(f, {})), "pk": f == "_id", "nullable": f != "_id",
                 "auto": f == "_id", "default": None} for f in order]
        return {
            "columns": order, "rows": rows, "total": total, "page": page, "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
            "meta": meta, "primary_keys": ["_id"],
        }

    def save_row(self, params):
        db = self.client[_check_mongo_db(params.get("database") or "")]
        coll = db[_check_mongo_coll(params.get("table") or "")]
        data = params.get("data") or {}
        if any(k.startswith("$") for k in data):
            raise RuntimeError("Field names cannot start with $")
        if params.get("is_new"):
            doc = {k: _coerce(v) for k, v in data.items()}
            res = coll.insert_one(doc)
            return {"ok": True, "affected": 1, "inserted_id": mdoc(res.inserted_id)}
        if not data:
            return {"ok": True, "affected": 0}
        if "_id" in data:
            raise RuntimeError("_id cannot be changed; duplicate the document instead")
        doc_id = _mongo_id(params.get("pks") or {})
        cur = coll.find_one({"_id": doc_id}) or {}
        sets = {k: _coerce(v, cur.get(k)) for k, v in data.items()}
        res = coll.update_one({"_id": doc_id}, {"$set": sets})
        return {"ok": True, "affected": res.matched_count}

    def delete_row(self, params):
        db = self.client[_check_mongo_db(params.get("database") or "")]
        coll = db[_check_mongo_coll(params.get("table") or "")]
        pks_list = params.get("pks_list") or ([params["pks"]] if params.get("pks") else [])
        if not pks_list:
            raise RuntimeError("_id required to delete a document")
        res = coll.delete_many({"_id": {"$in": [_mongo_id(p) for p in pks_list]}})
        return {"ok": True, "affected": res.deleted_count}

    def query(self, params):
        db_name = params.get("database") or ""
        if db_name:
            _check_mongo_db(db_name)
        statements = split_sql(params.get("sql", "") or "")
        start = time.time()
        out: Dict[str, Any] = {"columns": [], "rows": [], "affected": 0}
        affected = 0
        for idx, stmt in enumerate(statements):
            try:
                out = mongo_run_statement(self.client, db_name, stmt)
            except Exception as e:
                prefix = f"Statement {idx + 1} of {len(statements)}: " if len(statements) > 1 else ""
                raise RuntimeError(f"{prefix}{e}")
            affected += out.get("affected", 0)
        rows = out["rows"]
        return {"columns": out["columns"], "rows": rows[:1000], "affected": affected,
                "time_ms": round((time.time() - start) * 1000, 2), "total_rows": len(rows),
                "truncated": len(rows) > 1000, "statements": len(statements)}

    def list_users(self, params):
        res = self.client.admin.command({"usersInfo": {"forAllDBs": True}})
        users = []
        for u in res.get("users", []):
            roles = ", ".join(f"{r['role']}@{r['db']}" for r in u.get("roles", []))
            users.append({"user": u["user"], "host": u["db"], "privileges": roles or "—"})
        return {"users": users}

    def create_user(self, params):
        user = str(params.get("user", "")).strip()
        check_ident(user, "username")
        password = str(params.get("password", ""))
        if not password:
            raise RuntimeError("A password is required")
        grants = set(params.get("grants") or ["SELECT"])
        database = params.get("database") or "*"
        if database == "*":
            role = "root" if "ALL PRIVILEGES" in grants else ("readWriteAnyDatabase" if grants - {"SELECT"} else "readAnyDatabase")
            auth_db, roles = "admin", [{"role": role, "db": "admin"}]
        else:
            auth_db = _check_mongo_db(database)
            role = "dbOwner" if "ALL PRIVILEGES" in grants else ("readWrite" if grants - {"SELECT"} else "read")
            roles = [{"role": role, "db": auth_db}]
        self.client[auth_db].command("createUser", user, pwd=password, roles=roles)
        return {"ok": True, "user": f"{user}@{auth_db}"}

    def drop_user(self, params):
        user = str(params.get("user", ""))
        check_ident(user, "username")
        auth_db = params.get("host") or "admin"
        if auth_db in ("%", "*"):
            auth_db = "admin"
        self.client[_check_mongo_db(auth_db)].command("dropUser", user)
        return {"ok": True}

    def get_logs(self, params):
        db_name = params.get("database") or ""
        status = {"slow_query_log": "OFF", "long_query_time": "0.1", "log_file": ""}
        if not db_name:
            return {"status": status, "content": "Pick a database: MongoDB profiles each database separately."}
        db = self.client[_check_mongo_db(db_name)]
        prof = db.command("profile", -1)
        status["slow_query_log"] = "ON" if prof.get("was", 0) > 0 else "OFF"
        status["long_query_time"] = str(prof.get("slowms", 100) / 1000)
        status["log_file"] = f"{db_name}.system.profile"
        lines = []
        if "system.profile" in db.list_collection_names():
            for e in db["system.profile"].find().sort("ts", -1).limit(100):
                cmd = json.dumps(mdoc(e.get("command") or e.get("query") or {}))[:400]
                ts = e.get("ts").isoformat() if e.get("ts") else ""
                lines.append(f"{ts}  {e.get('op', '')}  {e.get('ns', '')}  {e.get('millis', 0)} ms  {cmd}")
        return {"status": status, "content": "\n".join(lines)}

    def toggle_log(self, params):
        db_name = params.get("database") or ""
        if not db_name:
            raise RuntimeError("Pick a database: MongoDB profiles each database separately")
        enable = bool(params.get("enable", True))
        threshold = float(params.get("threshold", 0.1))
        self.client[_check_mongo_db(db_name)].command("profile", 1 if enable else 0, slowms=max(0, int(threshold * 1000)))
        return {"ok": True, "enabled": enable, "threshold": threshold}

# ---------------------------------------------------------------------------
# Redis / Valkey / KeyDB / Memurai: numbered databases (db0, db1, ...), "tables" are key patterns
# (* and the most common prefixes), rows are keys with their type, TTL and value.
# ---------------------------------------------------------------------------

REDIS_SCAN_CAP = 10000
REDIS_ITEMS_CAP = 500

def get_redis(instance: Dict[str, Any], db: int = 0):
    try:
        import redis
    except ImportError:
        raise RuntimeError("The Python package 'redis' is missing: pip install redis")
    return redis.Redis(
        host=instance.get("host") or "127.0.0.1",
        port=int(instance.get("port") or 6379),
        username=instance.get("user") or None,
        password=instance.get("password") or None,
        db=db,
        socket_timeout=10,
        socket_connect_timeout=5,
        decode_responses=True,
        encoding_errors="replace",
    )

def _redis_db(name: Any) -> int:
    m = re.match(r"^db(\d{1,3})$", str(name or "db0"))
    if not m:
        raise RuntimeError(f"Invalid Redis database: {name!r} (db0, db1, ...)")
    return int(m.group(1))

def _redis_pattern(table: Any) -> str:
    p = str(table or "*")
    if not p or len(p) > 512:
        raise RuntimeError("Invalid key pattern")
    return p

def _glob_escape(s: str) -> str:
    return re.sub(r"([*?\[\]\\])", r"\\\1", s)

def _redis_value(r, key: str, kind: str) -> Any:
    if kind == "string":
        return r.get(key)
    if kind == "hash":
        out = {}
        for k, v in r.hscan_iter(key, count=200):
            out[k] = v
            if len(out) >= REDIS_ITEMS_CAP:
                break
        return out
    if kind == "list":
        return r.lrange(key, 0, REDIS_ITEMS_CAP - 1)
    if kind == "set":
        out = []
        for v in r.sscan_iter(key, count=200):
            out.append(v)
            if len(out) >= REDIS_ITEMS_CAP:
                break
        return sorted(out)
    if kind == "zset":
        return [[m, s] for m, s in r.zrange(key, 0, REDIS_ITEMS_CAP - 1, withscores=True)]
    if kind == "stream":
        return [{"id": i, **f} for i, f in r.xrevrange(key, count=50)]
    if kind == "ReJSON-RL":
        return r.execute_command("JSON.GET", key)
    return None

def _redis_write(pipe, key: str, kind: str, value: Any) -> None:
    """Writes `value` as a whole new value of type `kind` (the key must already be deleted in the same transaction)."""
    if kind == "string":
        pipe.set(key, value if isinstance(value, str) else json.dumps(value))
        return
    data = json.loads(value) if isinstance(value, str) else value
    if kind == "hash":
        if not isinstance(data, dict) or not data:
            raise RuntimeError('A hash value is a non-empty JSON object, e.g. {"field": "value"}')
        pipe.hset(key, mapping={k: v if isinstance(v, str) else json.dumps(v) for k, v in data.items()})
    elif kind in ("list", "set"):
        if not isinstance(data, list) or not data:
            raise RuntimeError(f'A {kind} value is a non-empty JSON array, e.g. ["a", "b"]')
        items = [v if isinstance(v, str) else json.dumps(v) for v in data]
        (pipe.rpush if kind == "list" else pipe.sadd)(key, *items)
    elif kind == "zset":
        pairs = data.items() if isinstance(data, dict) else data
        mapping = {str(m): float(s) for m, s in pairs}
        if not mapping:
            raise RuntimeError('A zset value is a JSON array of [member, score] pairs or an object {"member": score}')
        pipe.zadd(key, mapping)
    else:
        raise RuntimeError(f"Editing {kind} values is not supported here; use the Query tab")

def _redis_reply(res: Any) -> Tuple[List[str], List[Dict[str, Any]]]:
    if isinstance(res, dict):
        return ["field", "value"], [{"field": k, "value": v} for k, v in res.items()]
    if isinstance(res, (list, tuple, set)):
        return ["#", "value"], [{"#": i + 1, "value": v} for i, v in enumerate(res)]
    return ["result"], [{"result": res}]

def _cmp(a: Any, op: str, b: str) -> bool:
    if op in ("null", "notnull"):
        return (a is None) == (op == "null")
    if a is None:
        return False
    if isinstance(a, (int, float)):
        try:
            bv: Any = float(b)
        except ValueError:
            return False
    else:
        a, bv = str(a).lower(), b.lower()
    return {
        "eq": lambda: a == bv, "ne": lambda: a != bv, "gt": lambda: a > bv, "lt": lambda: a < bv,
        "gte": lambda: a >= bv, "lte": lambda: a <= bv, "contains": lambda: bv in a,
        "starts": lambda: a.startswith(bv), "ends": lambda: a.endswith(bv),
    }.get(op, lambda: True)()

class RedisDriver:
    def __init__(self, instance: Dict[str, Any]):
        self.instance = instance

    def close(self):
        pass

    def conn(self, db_name: Any = "db0"):
        return get_redis(self.instance, _redis_db(db_name))

    def list_databases(self, params):
        r = self.conn()
        try:
            count = int(r.config_get("databases").get("databases", 16))
        except Exception:
            count = 16
        keyspace = r.info("keyspace")
        dbs = []
        for i in range(count):
            ks = keyspace.get(f"db{i}")
            if i == 0 or ks:
                dbs.append({"name": f"db{i}", "tables_count": None, "size_bytes": None, "keys": (ks or {}).get("keys", 0)})
        return {"databases": dbs}

    def create_database(self, params):
        raise RuntimeError("Redis has a fixed set of numbered databases (db0-db15 by default): open one with SELECT in the Query tab")

    def drop_database(self, params):
        self.conn(params.get("name")).flushdb()
        return {"ok": True, "dropped": params.get("name")}

    def list_tables(self, params):
        r = self.conn(params.get("database"))
        prefixes: Dict[str, int] = {}
        scanned = 0
        for key in r.scan_iter(count=1000):
            scanned += 1
            if ":" in key:
                p = key.split(":", 1)[0]
                prefixes[p] = prefixes.get(p, 0) + 1
            if scanned >= 5000:
                break
        tables = [{"name": "*", "type": "keys", "engine": "Redis", "rows_count": r.dbsize(),
                   "data_size": 0, "index_size": 0, "collation": "", "comment": "All keys"}]
        for p, n in sorted(prefixes.items(), key=lambda x: (-x[1], x[0]))[:200]:
            tables.append({"name": f"{_glob_escape(p)}:*", "type": "prefix", "engine": "Redis", "rows_count": n,
                           "data_size": 0, "index_size": 0, "collation": "", "comment": f"Keys starting with {p}:"})
        return {"tables": tables, "database": params.get("database")}

    def table_structure(self, params):
        r = self.conn(params.get("database"))
        pattern = _redis_pattern(params.get("table"))
        kinds: Dict[str, int] = {}
        for i, key in enumerate(r.scan_iter(match=pattern, count=1000)):
            k = r.type(key)
            kinds[k] = kinds.get(k, 0) + 1
            if i >= 500:
                break
        columns = [
            {"name": "key", "type": "string", "nullable": "NO", "key_type": "PRI", "default_val": None, "extra": "", "comment": f"MATCH {pattern}"},
            {"name": "type", "type": "string", "nullable": "NO", "key_type": "", "default_val": "string", "extra": "",
             "comment": ", ".join(f"{k}: {n}" for k, n in sorted(kinds.items(), key=lambda x: -x[1]))},
            {"name": "ttl", "type": "int (seconds)", "nullable": "YES", "key_type": "", "default_val": None, "extra": "", "comment": "NULL = no expiry"},
            {"name": "value", "type": "string | JSON", "nullable": "NO", "key_type": "", "default_val": None, "extra": "",
             "comment": "hash = object, list/set = array, zset = [member, score] pairs"},
        ]
        info = r.info("server")
        ddl = f"# {info.get('server_name', 'redis')} {info.get('redis_version', '')} · SCAN 0 MATCH {pattern}"
        return {"columns": columns, "indexes": [], "create_sql": ddl}

    def browse_data(self, params):
        r = self.conn(params.get("database"))
        pattern = _redis_pattern(params.get("table"))
        page = max(1, int(params.get("page", 1)))
        per_page = max(1, min(1000, int(params.get("per_page", 50))))
        search = str(params.get("search") or "").strip().lower()
        raw = str(params.get("filter") or "").strip()

        keys: List[str] = []
        for key in r.scan_iter(match=pattern, count=1000):
            if search and search not in key.lower():
                continue
            if raw and not fnmatch.fnmatchcase(key, raw):
                continue
            keys.append(key)
            if len(keys) >= REDIS_SCAN_CAP:
                break
        pipe = r.pipeline(transaction=False)
        for key in keys:
            pipe.type(key)
            pipe.ttl(key)
        res = pipe.execute() if keys else []
        rows = []
        for i, key in enumerate(keys):
            ttl = res[2 * i + 1]
            rows.append({"key": key, "type": res[2 * i], "ttl": ttl if ttl is not None and ttl >= 0 else None})
        for f in params.get("col_filters") or []:
            col = f.get("col")
            if col in ("key", "type", "ttl"):
                rows = [x for x in rows if _cmp(x[col], f.get("op"), str(f.get("value", "")))]
        sort_col = params.get("sort_col") if params.get("sort_col") in ("key", "type", "ttl") else "key"
        desc = str(params.get("sort_dir", "")).upper() == "DESC"
        rows.sort(key=lambda x: (x[sort_col] is None, x[sort_col] if x[sort_col] is not None else 0), reverse=desc)
        total = len(rows)
        rows = rows[(page - 1) * per_page: page * per_page]
        for x in rows:
            x["value"] = _redis_value(r, x["key"], x["type"])
        meta = [
            {"name": "key", "type": "string", "pk": True, "nullable": False, "auto": False, "default": None},
            {"name": "type", "type": "string", "pk": False, "nullable": False, "auto": False, "default": "string"},
            {"name": "ttl", "type": "int", "pk": False, "nullable": True, "auto": False, "default": None},
            {"name": "value", "type": "string", "pk": False, "nullable": False, "auto": False, "default": None},
        ]
        return {"columns": ["key", "type", "ttl", "value"], "rows": rows, "total": total, "page": page,
                "per_page": per_page, "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
                "meta": meta, "primary_keys": ["key"]}

    def save_row(self, params):
        r = self.conn(params.get("database"))
        data = params.get("data") or {}
        if params.get("is_new"):
            key = str(data.get("key") or "").strip()
            if not key:
                raise RuntimeError("key is required")
            if r.exists(key):
                raise RuntimeError(f"Key {key} already exists")
            kind = str(data.get("type") or "string")
            pipe = r.pipeline(transaction=True)
            _redis_write(pipe, key, kind, data.get("value") if data.get("value") is not None else "")
            if data.get("ttl") not in (None, ""):
                pipe.expire(key, int(data["ttl"]))
            pipe.execute()
            return {"ok": True, "affected": 1}

        key = str((params.get("pks") or {}).get("key") or "")
        if not key or not r.exists(key):
            return {"ok": True, "affected": 0}
        if "type" in data:
            raise RuntimeError("The type of an existing key cannot be changed; duplicate it with another type")
        if "value" in data:
            kind = r.type(key)
            pttl = r.pttl(key)
            pipe = r.pipeline(transaction=True)
            pipe.delete(key)
            _redis_write(pipe, key, kind, data["value"] if data["value"] is not None else "")
            if pttl and pttl > 0:
                pipe.pexpire(key, pttl)
            pipe.execute()
        if "ttl" in data:
            ttl = data["ttl"]
            if ttl in (None, "", "-1") or int(ttl) < 0:
                r.persist(key)
            else:
                r.expire(key, int(ttl))
        if "key" in data and data["key"] and data["key"] != key:
            if not r.renamenx(key, str(data["key"])):
                raise RuntimeError(f"Key {data['key']} already exists")
        return {"ok": True, "affected": 1}

    def delete_row(self, params):
        r = self.conn(params.get("database"))
        pks_list = params.get("pks_list") or ([params["pks"]] if params.get("pks") else [])
        keys = [str(p.get("key")) for p in pks_list if p.get("key") is not None]
        if not keys:
            raise RuntimeError("key required to delete")
        return {"ok": True, "affected": r.delete(*keys)}

    def query(self, params):
        r = self.conn(params.get("database") or "db0")
        lines = [ln.strip() for chunk in split_sql(params.get("sql", "") or "") for ln in chunk.splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith(("#", "//", "--"))]
        start = time.time()
        columns: List[str] = []
        rows: List[Dict[str, Any]] = []
        for idx, line in enumerate(lines):
            try:
                args = shlex.split(line, posix=True)
                res = r.execute_command(*args)
            except Exception as e:
                prefix = f"Command {idx + 1} of {len(lines)}: " if len(lines) > 1 else ""
                raise RuntimeError(f"{prefix}{e}")
            columns, rows = _redis_reply(res)
        return {"columns": columns, "rows": rows[:1000], "affected": 0, "time_ms": round((time.time() - start) * 1000, 2),
                "total_rows": len(rows), "truncated": len(rows) > 1000, "statements": len(lines)}

    def list_users(self, params):
        users = []
        for line in self.conn().execute_command("ACL", "LIST"):
            parts = line.split()
            if len(parts) < 2:
                continue
            rules = [p for p in parts[2:] if not p.startswith("#") and not p.startswith(">")]
            users.append({"user": parts[1], "host": "*", "privileges": " ".join(rules)[:200]})
        return {"users": users}

    def create_user(self, params):
        user = str(params.get("user", "")).strip()
        check_ident(user, "username")
        password = str(params.get("password", ""))
        grants = set(params.get("grants") or ["SELECT"])
        r = self.conn()
        try:
            exists = r.execute_command("ACL", "GETUSER", user)
        except Exception:
            exists = None
        if exists:
            raise RuntimeError(f"User {user} already exists")
        if "ALL PRIVILEGES" in grants:
            cmds = ["+@all"]
        else:
            cmds = ["+@connection"] + (["+@read"] if "SELECT" in grants else []) + (["+@write", "+@keyspace"] if grants - {"SELECT"} else [])
        rules = ["on", f">{password}" if password else "nopass", "~*"] + cmds
        r.execute_command("ACL", "SETUSER", user, *rules)
        return {"ok": True, "user": user}

    def drop_user(self, params):
        user = str(params.get("user", ""))
        if user == "default":
            raise RuntimeError("Cannot drop the default user")
        check_ident(user, "username")
        self.conn().execute_command("ACL", "DELUSER", user)
        return {"ok": True}

    def get_logs(self, params):
        r = self.conn()
        cfg = r.config_get("slowlog-*")
        slower = int(cfg.get("slowlog-log-slower-than", 10000))
        status = {"slow_query_log": "ON" if slower >= 0 else "OFF",
                  "long_query_time": str(max(slower, 0) / 1_000_000),
                  "log_file": f"SLOWLOG (in memory, last {cfg.get('slowlog-max-len', '128')} entries)"}
        lines = []
        for e in r.slowlog_get(100):
            cmd = e.get("command")
            if isinstance(cmd, (list, tuple)):
                cmd = " ".join(c.decode("utf-8", "replace") if isinstance(c, bytes) else str(c) for c in cmd)
            elif isinstance(cmd, bytes):
                cmd = cmd.decode("utf-8", "replace")
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("start_time", 0)))
            lines.append(f"{ts}  {e.get('duration', 0) / 1000:.2f} ms  {str(cmd)[:400]}")
        return {"status": status, "content": "\n".join(lines)}

    def toggle_log(self, params):
        enable = bool(params.get("enable", True))
        threshold = float(params.get("threshold", 0.01))
        self.conn().config_set("slowlog-log-slower-than", int(threshold * 1_000_000) if enable else -1)
        return {"ok": True, "enabled": enable, "threshold": threshold}

NOSQL_DRIVERS = {"mongodb": MongoDriver, "redis": RedisDriver}
NOSQL_ACTIONS = {
    "list-databases": "list_databases", "create-database": "create_database", "drop-database": "drop_database",
    "list-tables": "list_tables", "table-structure": "table_structure", "browse-data": "browse_data",
    "save-row": "save_row", "delete-row": "delete_row", "query": "query", "list-users": "list_users",
    "create-user": "create_user", "drop-user": "drop_user", "get-logs": "get_logs", "toggle-log": "toggle_log",
}

def run_nosql(action: str, instance: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    method = NOSQL_ACTIONS.get(action)
    if not method:
        raise RuntimeError(f"Unknown action: {action}")
    driver = NOSQL_DRIVERS[instance["engine"]](instance)
    try:
        return getattr(driver, method)(params)
    finally:
        driver.close()

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
        if isinstance(instance, dict) and instance:
            fill_docker_credentials(instance)
        if isinstance(instance, dict) and instance.get("engine") in NOSQL_DRIVERS and action in NOSQL_ACTIONS:
            send_json(run_nosql(action, instance, params))
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
