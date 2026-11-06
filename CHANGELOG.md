# Changelog

All notable changes to the Ervisio Database Plugin will be documented in this file.

## 1.2.4 - 2026-11-06

- Run database actions in the verified account-owned virtual environment instead of injecting private drivers into system Python. Old optional system OpenSSL/service_identity packages no longer conflict with the private MongoDB/cryptography drivers.
- Re-execute before consuming private payload files, using an isolated interpreter and only the signed plugin directory. System packages, databases and account credentials are unchanged.
- Verify imports using the activated runtime interpreter. Add a real Linux installation and helper-status/private-payload SQLite regression with deliberately broken optional packages in a disposable base interpreter.
- Clarify account scope: Linux root and regular users have separate runtimes; Windows linked UAC elevation of the same OS account uses the same profile and runtime.

## 1.2.3 - 2026-11-06

- Fix missing PostgreSQL, MySQL, MongoDB and Redis driver errors with an explicit Prepare drivers action and actionable connection errors.
- Install SHA-256 pinned binary wheels into an account-local Python runtime, verify imports before/after activation, and preserve previous drivers on failure. System Python and databases are unchanged; SQLite needs no extra drivers.
- Protect runtime code with account ownership/permissions, Windows token-derived profiles and DACL/reparse checks; prevent environment variables from selecting another OS account's runtime.
- Preserve all 1.2.2 SQL/NoSQL features, Windows capabilities, SDK 0.3.0 and reusable release automation. Update Vite to a compatible patched version.

## 1.2.2 - 2026-10-30

- update

## 1.2.1 - 2026-10-29

- Run CI manually instead of on every push and pull request
- Add one-command plugin release and marketplace registration (#4)
- Add MongoDB and Redis/Valkey drivers, Docker and local service discovery
- README: one-button releases
- One-button releases with the SDK's release workflow
- Add Windows support (SDK 0.3.0, platforms manifest, cross-platform bridge)

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
