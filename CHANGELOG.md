# Changelog

All notable changes to RE4 are recorded here. Versions follow
[semantic versioning](https://semver.org/): the major number moves when a shop
database needs a migration it cannot undo, the minor when features are added,
the patch for fixes.

## [2.5.0] — 2026-09-06

No schema change. This release is about speed that lasts: the queries that were
cheap on a young database stay cheap on one the shop has traded on for years.

### Changed

**Dates are compared, not unwrapped.** Every date filter in the app wrapped the
column in `date()` — `date(sale_time) >= date(?)` — which asks SQLite to read
and parse every row before it can compare, and threw away the index on that
column. The day report, the ledger, the audit trail, the invoices list and every
chart on the dashboard now compare the raw timestamp instead, so the index does
the work. On a database with years of trading in it this is the difference
between a scan and a lookup, on every one of those screens.

**Nine new indexes on the columns the app actually filters by.** X/Z reports sum
the customer ledger per drawer; the CSV importer and the product-delete guard
probe sale and purchase lines by product; the suppliers screen counts products
per supplier; the returns list walks a day's returns. Each of those was a
full-table scan, growing for as long as the shop trades. Existing databases get
the indexes on their next start-up, without a migration.

**Printing, backups and the database check happen away from the till.** A
printer that is asleep can hold the conversation open for the better part of a
minute, PowerShell's list of installed printers nearly as long, and a backup of
a database grown over years is a file copy — all of which used to leave the
window grey and the till dead while they ran. They now run on a background
worker; the cursor reads as busy, and the shop keeps answering the scanner.
Restoring a backup deliberately stays on the spot: it must close and reopen the
calling thread's database connection, and the cursor says the window is busy
for the second it takes.

**One copy of the register per shop.** Starting a second copy of RE4 against
the same database was a corruption lottery — two tills selling the last unit,
one restore running under the other's sale. A second launch now says so and
stops. Windows is told with a named mutex, which the operating system releases
even after an ugly exit; elsewhere an advisory lock file beside the database
does the same job.

**The database tidies up after itself.** The write-ahead log is folded back
into the file when the shop closes — and after a housekeeping purge — instead
of growing for as long as the till stays up. Closing can also take a backup
first (Settings → Data safety, off by default), so the day is snapshotted
before anyone goes home. `--check` now says what the big tables hold, whether
any row points at data that is not there, and how much is still pending in the
write-ahead log.

**The customer ledger and the audit trail are tidied.** Held sales nobody came
back for are discarded after 30 days, stale sign-in throttle rows after 30, and
— only if an administrator asks for it in Settings — printed receipts and audit
lines older than a stated number of days. The defaults delete nothing; records
are a decision, not a side effect. The rules live in Settings → Data safety.

### Performance

- The CSV importer reads the catalogue once, not once per row: picking a
  900-line supplier file no longer runs 900 queries on the UI thread.
- A stock take reads its worksheet once and patches it in place. Every scan
  used to re-query the entire sheet and rebuild the whole table; on a full
  count of a thousand lines, one barcode meant a thousand-row join per beep.
- Search boxes wait for a quiet moment before querying, so typing "stapler"
  searches the catalogue once instead of six times. The same grace applies to
  the invoice, customer, supplier, purchasing and audit searches.
- The till reads the exchange rate and LBP rounding once per refresh, not once
  per character typed into the amount-received box.
- Customer, supplier and invoice listings answer their per-row counts and sums
  with one grouped pass over the child tables instead of a sub-query per row —
  a 500-invoice search used to mean 1,500 of them per keystroke.

### Fixed

- Two backups written in the same second shared a filename, and the second
  silently overwrote the first. A restore writes its safety copy moments
  after a start-up backup, so this happened in practice — the restore drill
  that found it is now a permanent test.
- The sign-in screen checked the bootstrap password by *signing in*: two
  PBKDF2 runs the moment the app opened, a *Signed in* line in the audit trail
  that no person wrote, and a last sign-in time from before anyone touched the
  keyboard. It now checks the stored hash quietly.
- Unlocking several accounts committed one account at a time; a failure
  halfway left the rest still locked. All of it is one commit now.
- Raising draft orders from reorder suggestions created one transaction per
  supplier, so a failure part-way left half the suppliers with an order and
  half without. The set is raised all-or-nothing.

### Added

**The keyboard, on one card.** Till work is keyboard work, but nobody can
remember six function keys on their first shift. The New Sale screen has a
*Keys* button now, and the card it opens lists every shortcut and what it
does — parking a sale, bringing one back, completing it, changing a quantity,
discounting a line, changing a price, and locking the screen.

**Everything leaves in a spreadsheet.** Sales could already be exported from
Reports and the catalogue from Products; Customers and the audit log can now
be exported too — the audit export follows whatever search and action filter
is on screen, so a question about one person's sign-in failures is one file.

**An installer for Windows.** `installer.iss` builds a real setup program:
RE4 goes in Program Files, a shortcut goes on the desktop and the Start menu,
and the shop's data — the database, receipts, backups — lives in
`%LOCALAPPDATA%\RE4` where it can be backed up and survives an uninstall of
the application. CI now builds and uploads the installer alongside the bare
executable.

### Tests

- 491, up from 470.

## [2.4.0] — 2026-09-04

No schema change. This release is about how the application talks.

### Added

**A briefing on the dashboard.** The dashboard opened on a wall of numbers.
Numbers answer a question you already have; somebody who has just walked in does
not yet know what to ask. Above the figures there is now a short list of what
actually needs a person, worst first — a till left open overnight, something off
the shelf, a customer at their credit limit, a sale still parked, an order not
yet received, a backup nobody has taken. Each line is one sentence and a click
away from the screen that fixes it, and staff are only shown what they are
allowed to act on. When there is genuinely nothing outstanding it says so, which
is what makes the rest worth reading.

**Times written the way people say them.** *Just now*, *22 minutes ago*,
*Yesterday 17:40*, *Wednesday 11:00* — and then, deliberately, back to
``12 Aug 11:00`` once a relative phrase would start hiding something. Receipts,
reports and the audit log are untouched: they still print the exact timestamp,
because that is what a document is for.

### Changed

- The dashboard greets whoever is signed in, by name and by the hour.
- The sign-in screen greets rather than restating the product name.
- The lock screen says what happened *and* that the half-built cart underneath
  is exactly where it was left.
- Signing out now warns that anything half-finished goes with it, and points at
  Lock for anyone coming back.
- The forced first password change reads as a welcome rather than an accusation.
- Empty tables say what to do next instead of stating that a table is empty —
  "Nothing is parked. Press F2 during a sale to hold it and come back to it
  later" rather than "Nothing is being held."
- Counts agree with their nouns and verbs throughout: *1 sale*, *3 sales*,
  *1 customer owes*, *2 customers owe*.
- A customer who has never bought anything reads *Never*, not an em dash.
- Change due at the end of a sale is now an instruction — "Give $2.50 change" —
  on its own line, because it is the one thing the cashier must act on before
  the customer walks away.

### Fixed

- The till's shift history showed an open drawer as counted `$0.00` with a
  `$0.00` variance, which reads as *counted, and empty*. An open drawer now
  reads as not yet counted.
- A shift opened seconds ago said it had been "open for a moment"; it now says
  "Just opened".

### Tests

- 470, up from 388.

## [2.3.0] — 2026-09-04

No schema change: everything here reads what was already being recorded.

### Added

**The end-of-day sheet.** Closing up meant opening four screens and adding up by
hand, which is exactly when a figure gets missed. One sheet now ties the day
together, in the order the person locking the door works through it:

- **Anything unfinished goes at the top** — a till still open, a drawer that did
  not come back to its expected figure, sales taken with no shift open. These
  are the things discovered a week later when nobody can explain them.
- **Trading**: sales, discounts, returns netted off, net revenue in USD and LBP,
  tax, gross profit and margin, units, average sale, what it was taken as, who
  served, and the five best sellers.
- **Every drawer opened that day** with its expected, counted and variance
  figures, and the day's total variance underneath. An uncounted drawer reads
  as *not yet counted*, never as balanced.
- **Customer accounts**: what went on account today, what was paid off (and how
  much of that is cash in the drawer), and the receivable being carried into
  tomorrow — as it stood at the end of *that* day, so reprinting an old sheet
  does not re-price it with today's balances.
- **Stock**: what was received, and any stock take posted, with its variance at
  cost.
- Sign-off lines for whoever counted and whoever checked.

The day is bounded by the date, not by a shift, so a drawer left open overnight
still belongs to the day it opened rather than quietly disappearing.

It prints on the same roll as every other document, because a shop with a
thermal printer at the till usually has nothing else. Reach it from *Till → Day
report* while locking up, from *Reports → Day report* to reprint any past day,
or from the command line with `--day-report [DATE]` for a machine that closes
unattended.

### Fixed

- Recovery commands no longer risk a `UnicodeEncodeError` when a Windows console
  cannot spell the message they are printing.

### Changed

- 388 tests, up from 350.

## [2.2.0] — 2026-09-04

Database schema v4. An existing shop database is migrated in place on first
start; nothing needs to be reimported.

### Added

**Customer accounts.** `Credit` has been a payment method since 1.0 and recorded
no debt: the goods left the shop and the money was forgotten. It is now a real
account.

- Every movement writes one signed row in a ledger — a credit sale adds, a
  payment or a refund subtracts, an owner's adjustment does either — and the
  balance is their sum. It is never a stored column, so it cannot drift out of
  step with the rows that explain it.
- *Statement* shows every movement in order with a running balance and the
  invoice or return number beside each one. A wrong payment is reversed with a
  visible adjustment rather than by deleting history; adjusting is admin-only.
- **Credit limits.** A customer with no limit cannot buy on account at all, so
  credit is granted rather than handed out by picking the wrong payment method
  in a hurry. The limit is checked *before* the invoice is written, so a refusal
  costs the cashier a payment method, not a half-committed sale.
- *Take a payment* accepts USD or LBP at the rate in force, in full or in part,
  by any payment method. Overpayment is refused — at a counter that is nearly
  always a typo.
- **Cash paid against an account reaches the till.** It is attached to the open
  shift and counted into the expected drawer figure, so a close no longer comes
  up over, and both the X and Z reports print it.
- Returning goods from a credit sale reduces the debt instead of paying cash out
  of a drawer that never took any in. Refunding to *Credit* on a sale with no
  customer is refused.
- *Customers* gained Owes and Limit columns, an "Owing only" filter, the total
  receivable, and a flag on anyone at or over their limit. *Reports* carries the
  same total with the list behind it, and `--check` prints it.
- Payments, adjustments and limit changes are all audited.

### Fixed

- A sale on `Credit` recorded an amount paid and change due, as though money had
  changed hands. It now records nothing paid and creates the debt instead.

### Changed

- Migration tests cover v2 → v4, including putting a sale on account against a
  database migrated from before accounts existed.
- 350 tests, up from 310.

## [2.1.0] — 2026-09-04

Database schema v3. An existing shop database is migrated in place on first
start; nothing needs to be reimported.

### Added

**Stock takes.** A new screen for counting the shelves and reconciling them
against the system.

- Opening a count freezes a worksheet of what the system believes is in stock,
  so trading during the count does not move the goalposts.
- Count by scanning (a scanner just works — the box keeps focus and each scan
  adds one), or by typing a figure against a line.
- The sheet filters to what is uncounted or to the variances alone, and the
  progress, shortage, surplus and cost of the variance are on screen throughout.
- Applying posts the *difference* to stock, so anything sold during the count
  survives it, and each movement lands in the product's stock history with the
  count's reference on it. Lines nobody counted are left alone.
- A count can be abandoned with no effect on stock, and past counts are kept so
  a run of shrinkage in one aisle is visible over time.
- Staff can count; only an administrator can post the variance.

**Security.**

- Sign-in throttling: a run of wrong passwords locks the account for a few
  minutes. Configurable under *Settings → Security*, off at 0, and applied to
  unknown usernames too so the lockout does not reveal which accounts exist.
- Screen lock: after a period of inactivity — or on demand, from the sidebar or
  `Ctrl+Shift+L` — the screen locks behind the operator's password. The shell is
  covered, not torn down, so a half-built cart is still there afterwards.
- A password set by somebody else must be replaced at the next sign-in. This
  covers the bootstrap `admin`/`admin123` account and any administrator reset.
- Password hashes are silently re-hashed at the current work factor when a
  correct password is used, so an old account does not keep an old cost forever.
- Administrators can see and clear lockouts, from *Users*, from *Settings*, or
  from the command line when nobody can get in at all.
- A changed password must differ from the one it replaces.
- Sign-in throttling, lockouts, screen locks and unlocks are all audited.

**Dashboard.** Revenue and profit now carry a comparison against the equally
long period immediately before, so a number reads as good or bad at a glance.

**Command line.** `--version`, `--check` (integrity plus a summary of what is in
the database), `--backup`, and `--unlock` for an account — or every account —
locked out by failed sign-ins.

**Tests.** 310 tests, up from 246. New coverage for stock takes, the sign-in
policy, and a smoke suite that builds and refreshes every screen against a real
Tk root — which is the failure a released build shows as a blank window, and was
previously untested. The UI tests skip themselves where there is no display.
A migration suite builds a v2-shaped database and checks that the upgrade
adds what it should while leaving every existing row alone.

### Changed

- SQLite connections now set `busy_timeout` (5s), `synchronous = NORMAL` and
  `temp_store = MEMORY`. Previously a backup running while a sale committed
  could surface as "database is locked" the instant the two overlapped.
- The test suite runs in about 25 seconds for 310 tests, where it took about
  116 for 246, by hashing at a test work factor instead of the production
  260,000 rounds. The production cost is unchanged, and is now read at call time
  so it can be raised without a restart.
- *Users* shows when each account last signed in and whether it is locked out or
  waiting for its owner to choose a password.
- The `Employee` role reaches the new stock take screen; every other permission
  is unchanged.

### Fixed

- Deferred focus callbacks on the login and lock screens no longer raise from
  inside Tcl's event loop if the frame is swapped out before they fire.

### Developer experience

- `pyproject.toml` with project metadata, a `re4` console entry point, and Ruff
  configuration. `ruff check` passes clean.
- GitHub Actions CI: lint, then the full suite on Linux, Windows and macOS
  across Python 3.10 – 3.13, then a Windows executable built from `RE4.spec`.
- `.editorconfig` and `requirements-dev.txt`.

## [2.0.0]

The release this changelog starts from: point of sale, till shifts, returns,
purchasing, barcode labels, receipt printing, reporting, backups and the audit
trail, all offline against a single SQLite file, priced in USD and settled in
USD and LBP.
