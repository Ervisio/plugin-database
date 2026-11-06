# Changelog

All notable changes to the Ervisio Database Plugin will be documented in this file.

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
## 1.1.1

- Fix missing PostgreSQL/MySQL driver errors with an explicit Prepare drivers action. Drivers live in a private, versioned Python virtual environment for the command's account; system Python is unchanged.
- Pin binary wheels and SHA-256 hashes, verify imports before atomically activating the runtime, and preserve the previous runtime on failure. SQLite remains usable without extra drivers.
- Show runtime status and actionable setup errors in Instances and the connection error screen.
- Include runtime helpers and the dependency lock in the signed plugin inventory; validate package/manifest/tag version agreement.
