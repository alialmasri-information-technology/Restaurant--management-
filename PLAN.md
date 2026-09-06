# RE4 Desktop System — Upgrade Plan

Where RE4 goes from "feature-complete codebase" to a fully functioning desktop
system: packaged, responsive on any database size, and self-maintaining.

Work proceeds in phases; each one ends with the full test suite and ruff green.

## Phase 1 — Performance and correctness pass

1. ~~Sargable date filters + missing indexes~~ — done. Every date query now
   compares the raw ISO timestamp instead of wrapping the column in `date()`,
   and the ledger, sale/purchase lines, supplier and returns indexes exist.
2. ~~Debounced search + cached till settings~~ — done. Search boxes wait for a
   quiet moment; the till reads the exchange rate once per refresh.
3. CSV import: preload the SKU→product map so `analyse` stops issuing one
   joined query per row of the supplier's file.
4. Stock take: index the count sheet in memory so a scan stops re-reading and
   re-rendering the entire worksheet per barcode.
5. Bounded lists and N+1 removals: LIMIT on the POS/products/customers
   listings with a "showing the first N" note; rewrite the correlated
   subqueries in `list_customers`, `_SALE_SELECT` and the supplier roll-ups as
   aggregate JOINs.
6. Housekeeping: prune stale `login_throttle` rows, abandoned parked sales and
   receipt PDFs past a retention setting; audit and inventory logs trimmed to a
   configurable age.

## Phase 2 — Heavy work off the UI thread

- ~~Printer jobs, printer discovery, manual backups and the integrity check
  run on a background worker (`app/ui/background.py`) with a busy cursor and
  outcomes delivered on the UI thread~~ — done.
- ~~Restore stays on the UI thread by design (it owns the calling thread's
  connection) and marks the window busy~~ — done.
- Remaining: a busy cursor around the CSV import modal while `analyse` and
  `apply` run, should a supplier file ever grow past what a transaction
  commits in a blink.

## Phase 3 — Desktop packaging and first-run experience

- ~~One-click Windows installer (`installer.iss`, Inno Setup): Program Files
  install, desktop/Start-menu shortcuts, writable data in
  `%LOCALAPPDATA%\RE4`, wired into `build_executable.bat` and CI~~ — done.
- ~~Single-instance guard (`app/instance.py`) with a plain-language message
  instead of two copies on one database~~ — done.
- Remaining: first-run wizard (store name, exchange rate, admin password,
  shift policy) and an update-check banner pointing at the latest GitHub
  release, once the repository URL in `pyproject.toml` points somewhere real.

## Phase 4 — Database hardening

- ~~WAL checkpoint at closing and after a housekeeping purge~~ — done.
- ~~Optional backup at closing (Settings → Data safety, off by default)~~ — done.
- ~~Restore drill in the test suite: a restore passes its integrity check and
  can be undone with its own safety copy~~ — done, and it caught a real bug:
  two backups in the same second shared a filename, so a restore's safety copy
  could overwrite the startup backup taken moments before. Filenames are now
  uniquified.
- ~~`--check` reports ledger/audit/parked counts, foreign-key violations and
  the pending WAL size~~ — done.
- Remaining: an idle checkpoint on the 15-second tick is not worth its wakeups;
  closing time is the right time.

## Phase 5 — Completing the retail feature set

- ~~A printable F-key help card at the till (Keys button on the New Sale
  header)~~ — done.
- ~~CSV export for customers and for the audit trail, joining sales (Reports)
  and the catalogue (Products), which could already export~~ — done.
- LBP dual pricing was already consistent across labels, invoices and
  receipts; nothing to change.
- Candidates for later: layaways, gift cards, per-category tax profiles.

## Phase 6 — Quality gates and release discipline

- ~~Debounce is covered by tests: three keystrokes must run the query once,
  and typing after a pause must run it again~~ — done.
- ~~CI builds the exe *and* the installer on every tag and attaches both to a
  GitHub release with generated notes~~ — done (`release` job).
- Version 2.5.0 with a CHANGELOG entry in the repository's voice.
