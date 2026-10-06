# Database plugin for Ervisio

Manage SQL databases directly from [Ervisio](https://github.com/Ervisio/ervisio): full HeidiSQL-grade database management
for **MySQL**, **MariaDB**, **PostgreSQL**, and **SQLite**. Deploy multi-version database containers in Docker or install
native systemd services via APT with zero terminal steps. English and Italian interface.

The plugin provides a complete database workbench inside Ervisio's sandboxed UI frame:
* **The page** (this repository's `src/`): an Ervisio plugin (SDK contract 3) with full dark-mode styling, responsive schema tree, live data grid, and CodeMirror SQL editor.
* **The engine bridge** (`db-bridge.py`): a Python execution bridge declared as an admin command capability in `plugin/manifest.json`. It coordinates Docker containers, native systemd database services, schema introspection, SQL execution, user permissions, and slow query logs.

## Install

In Ervisio, open **Plugins › Browse › Databases › Install** (the marketplace package is signed by the Ervisio team).
The dialog lists the permissions below; updates appear in **Plugins › Updates**.

## What it does

* **Instances & Engine Deployment (Zero Terminal Steps)**:
  * **🐳 Docker Containers**: 1-click deployment for MySQL (8.4-LTS, 8.0, 9.0, 5.7), MariaDB (11.4-LTS, 11.2, 10.11-LTS, 10.6), and PostgreSQL (17, 16, 15, 14) with custom ports, container names, and persistent volumes.
  * **🖥️ Native Systemd Services**: 1-click installation via APT package manager (`mariadb-server`, `mysql-server`, `postgresql`, `sqlite3`). Automatically starts and enables the systemd service.
  * **📁 SQLite Files**: Instant creation of `.sqlite` / `.db` database files anywhere on the filesystem, plus auto-discovery of existing SQLite databases in `/home` and `/var/lib`.
  * **🔗 Remote & Custom Connections**: Connect to any external or pre-existing MySQL or PostgreSQL host.
  * **Lifecycle Management**: Start, stop, and restart database containers or native systemd services directly from the card view.
* **HeidiSQL-Grade Schema Explorer**:
  * Visual tree of databases, tables, and views with size metrics and row counts.
  * Create, alter, and drop databases with charset and collation options.
  * Create and drop tables; view complete column schemas (types, keys, nullability, defaults) and indexes.
* **Visual Data Grid & Editor**:
  * Browse rows with live pagination and sorting.
  * Inline cell editing with instant row saving.
  * Insert new rows and delete existing rows with primary key safety checks.
  * Live client-side text filtering across all table columns.
* **SQL Query Studio**:
  * CodeMirror-powered SQL editor with syntax highlighting, SQL keywords autocomplete, and line numbers.
  * Execute queries or multi-statement SQL scripts.
  * Formatted tabular results with execution duration (`ms`) and affected row counts.
  * Export query results to CSV.
* **User & Privilege Management**:
  * List database users and host scopes (`user@host`).
  * Create new database users with passwords.
  * Granular privilege assignment (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `INDEX`, `ALTER`, `ALL PRIVILEGES`) scoped globally (`*.*`) or per database.
  * Delete/drop database users.
* **Slow Query Log & Diagnostic Tools**:
  * Inspect slow query log status, threshold (`long_query_time`), and log file path.
  * Toggle the slow query log on/off dynamically with custom duration thresholds.
  * Real-time viewing of recent slow log entries.

## Windows

Needs Ervisio 0.6.2 or later on Windows and Python 3.10+ (`python.exe` on the `PATH` of the account that runs the daemon).
The plugin lives in `%ProgramData%\Ervisio\data\plugins\database`. On Windows:

* **Docker**: Docker Desktop or Docker Engine (`docker.exe` on the `PATH`).
* **Native**: MariaDB, MySQL and PostgreSQL registered as Windows services are detected and started, stopped and restarted from the card view; installation uses `winget`.
* **Administrator rights**: operations that install or control services need the administrator unlock in Ervisio (members of `docker-users` run Docker commands as themselves).
* The Python packages `pymysql` and `psycopg2` must be installed for that Python (`pip install pymysql psycopg2-binary`); SQLite needs nothing.

## Permissions

The manifest is [plugin/manifest.json](plugin/manifest.json). In short:

* **Commands**, all `admin` with `adminUnlessGroup: docker` (`docker-users` on Windows):
  * `db-bridge` (`python3 /var/lib/ervisio/plugins/database/db-bridge.py {0} {1}` on Linux, `python.exe C:\ProgramData\Ervisio\data\plugins\database\db-bridge.py {0} {1}` on Windows):
    Executes database administration operations, schema introspection, container management, and query dispatching.
* **Folders**:
  * `~/.config/ervisio/plugins/database` (created on first use; saved connections, preferences).
* **Visible to** members of `docker`, `wheel`, and `sudo` (administrators always see it).

## Development

Requires Node.js 22 or newer, and Python 3.10+ on the host.

```sh
npm ci
npm run build       # typecheck, bundle to dist/database/index.js, copy plugin/* and manifest
npm run pack        # package dist/database-<version>.tar.gz and .sha256
npm run typecheck   # TypeScript check
```

Turn on developer mode in Ervisio (`plugins.dev = true`, or a daemon started with `--dev`) and load `dist/database` from
**Plugins › Developer**; after a rebuild use "Reload" there. A dev folder runs unsigned while developer mode is on.

The SDK types and Vite presets come from
[@ervisio/plugin-sdk](https://github.com/Ervisio/plugin-sdk).

## How it fits together

React is not bundled. The preset in `vite.config.ts` aliases `react` and the JSX runtime to the SDK's shim
(`@ervisio/plugin-sdk/react`), which forwards to `sdk.react`. The SDK exists only inside `activate()`
(`src/index.tsx`), so:

* Never call a React API at module top level.
* Read the SDK with `getSdk()` (`src/sdk.ts`) inside functions, never at import time.
* Use the kit through `src/kit.ts` (`Button`, `Dialog`, `toast`, ...), not `sdk.ui` directly.

```
plugin/
  manifest.json         manifest declaring commands, permissions, and plugin metadata
  logo.svg              database icon for the sidebar and marketplace
src/
  index.tsx             plugin entry point, registers page and routes
  sdk.ts                SDK v3 types and getSdk() helper
  kit.ts                typed UI kit wrappers for Ervisio design system
  api.ts                frontend bridge client, RPC dispatch to db-bridge
  router.ts             in-plugin navigation and view state
  views/
    InstancesView.tsx   database instance manager and 1-click deployment modal
    ExplorerView.tsx    HeidiSQL studio: Schema tree, Data grid, SQL runner, Users, Logs
db-bridge.py            backend Python executor (Docker, APT, PyMySQL, Psycopg2, SQLite)
```

## Releasing

On GitHub: **Actions › Release › Run workflow**, choose `patch`, `minor` or `major`, optionally type the release notes
(empty: the commit subjects since the last release), and run it. The workflow bumps the version, writes the
`CHANGELOG.md` section, tags, builds, validates and releases, then tells the Ervisio registry: an update that asks for
no new permissions is in the marketplace a few minutes later. The steps live in
[Ervisio/plugin-sdk](https://github.com/Ervisio/plugin-sdk/blob/main/docs/publishing.md).

## License

MIT, see [LICENSE](LICENSE).
