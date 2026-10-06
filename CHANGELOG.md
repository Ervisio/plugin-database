# Changelog

All notable changes to the Ervisio Database Plugin will be documented in this file.

## 1.2.0

* Windows support (needs Ervisio 0.6.2 and `@ervisio/plugin-sdk` 0.3.0): the manifest declares `platforms: ["linux", "windows"]` and a separate `db-bridge` command for Windows (`python.exe`).
* Windows engines: Docker Desktop containers, native MariaDB/MySQL/PostgreSQL detected and controlled as Windows services (`net.exe`), installed with `winget`, SQLite discovery in the user profile and `%ProgramData%`.
* The bridge no longer depends on `sudo` (skipped when root or on Windows), on `tail`, or on the console code page.
* CI validates the manifest with Ervisio 0.6.2 and smoke-tests the bridge on a Windows runner.

## 1.1.0

* HeidiSQL-grade database workbench for MySQL, MariaDB, PostgreSQL, and SQLite.
* Zero-terminal deployment: 1-click Docker container deployment (MySQL 8.4/8.0/9.0, MariaDB 11.4/10.11, PostgreSQL 17/16).
* Zero-terminal native APT installation for `mariadb-server`, `mysql-server`, `postgresql`, and `sqlite3`.
* Standalone SQLite database creator and auto-discovery across filesystem.
* Interactive schema tree with tables, views, and engine metrics.
* Editable data grid with inline cell editing, pagination, column sorting, and live search.
* CodeMirror SQL Query Studio with autocomplete, syntax highlighting, and CSV export.
* User and role management with granular privilege assignment.
* Slow query log status inspector, threshold adjustment, and real-time log viewer.
* English and Italian interface localization (`i18n.it.ts`).

## 1.0.0

* Initial release of Ervisio Database Plugin.
