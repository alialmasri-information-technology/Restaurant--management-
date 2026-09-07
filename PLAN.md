# RE4 Desktop System — Upgrade Plan

Where RE4 goes from "feature-complete codebase" to a fully functioning desktop
system: packaged, responsive on any database size, and self-maintaining.

Work proceeds in phases; each one ends with the full test suite and ruff green.

## Phase 1 — Performance and correctness pass — complete

1. ~~Sargable date filters + missing indexes~~ — done. Every date query now
   compares the raw ISO timestamp instead of wrapping the column in `date()`,
   and the ledger, sale/purchase lines, supplier and returns indexes exist.
2. ~~Debounced search + cached till settings~~ — done. Search boxes wait for a
   quiet moment; the till reads the exchange rate once per refresh.
3. ~~CSV import: preload the SKU→product map so `analyse` stops issuing one
   joined query per row of the supplier's file~~ — done. The catalogue is read
   once before the rows are walked.
4. ~~Stock take: index the count sheet in memory so a scan stops re-reading and
   re-rendering the entire worksheet per barcode~~ — done. The sheet is held in
   memory and keyed by product, the totals are added up from that cache rather
   than re-queried, and a scan rewrites the one row it changed instead of
   rebuilding the table (`DataTable.update_row`). A filter that the line has
   just left still rebuilds, because the row has to leave the list.
5. ~~Bounded lists and N+1 removals~~ — done. The correlated subqueries in
   `list_customers`, `_SALE_SELECT` and the supplier roll-ups are aggregate
   JOINs, and the products, customers and suppliers listings stop at
   `db.LIST_LIMIT` rows. The cap is visible: `db.query_limited` fetches one row
   more than it needs, and a list that was cut short says so under the table.
   Exports, the reorder list and the pickers that must be complete pass
   `limit=None`.
6. ~~Housekeeping: prune stale `login_throttle` rows, abandoned parked sales and
   receipt PDFs past a retention setting; audit and inventory logs trimmed to a
   configurable age~~ — done. The stock movement history was the last of the
   five and is now trimmed on the same terms as the rest: kept for ever unless
   an administrator names a number of days.

## Phase 2 — Heavy work off the UI thread

- ~~Printer jobs, printer discovery, manual backups and the integrity check
  run on a background worker (`app/ui/background.py`) with a busy cursor and
  outcomes delivered on the UI thread~~ — done.
- ~~Restore stays on the UI thread by design (it owns the calling thread's
  connection) and marks the window busy~~ — done.
- ~~A busy cursor around the CSV import modal while `analyse` and `apply`
  run~~ — done. The cursor is released before any error dialog, so a failure
  is never reported under a busy pointer.

## Phase 3 — Desktop packaging and first-run experience

- ~~One-click Windows installer (`installer.iss`, Inno Setup): Program Files
  install, desktop/Start-menu shortcuts, writable data in
  `%LOCALAPPDATA%\RE4`, wired into `build_executable.bat` and CI~~ — done.
- ~~Single-instance guard (`app/instance.py`) with a plain-language message
  instead of two copies on one database~~ — done.
- ~~First-run walkthrough: an administrator's first sign-in asks the shop's
  own name, phone, address, exchange rate and tax rate, then never again
  (`app/ui/onboarding.py`)~~ — done.
- ~~Update check: once a day, on the background worker, cached in settings;
  a newer release shows one dismissible strip in the shell, an offline shop
  gets nothing (`app/services/updates.py`)~~ — done.

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
- ~~Per-category tax rates~~ — done (v2.6.0, schema v5).
- ~~Gift cards: sold at the till, redeemed as split payment, admin-managed~~ —
  done (v2.6.0, schema v6).
- ~~Layaways: held with a frozen price and a cash-honest deposit, collected
  through the normal sale pipeline~~ — done (v2.6.0, schema v7).
- LBP dual pricing was already consistent across labels, invoices and
  receipts; nothing to change.

## Phase 6 — Quality gates and release discipline

- ~~Debounce is covered by tests: three keystrokes must run the query once,
  and typing after a pause must run it again~~ — done.
- ~~CI builds the exe *and* the installer on every tag and attaches both to a
  GitHub release with generated notes~~ — done (`release` job).
- ~~Every release carries a CHANGELOG entry in the repository's voice~~ —
  done, up to and including 2.7.0.
