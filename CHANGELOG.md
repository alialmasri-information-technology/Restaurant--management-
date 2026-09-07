# Changelog

All notable changes to RE4 are recorded here. Versions follow
[semantic versioning](https://semver.org/): the major number moves when a shop
database needs a migration it cannot undo, the minor when features are added,
the patch for fixes.

## [2.7.2] — 2026-09-07

No schema change and no change to what the application does. It says who it is
when Windows asks.

### Added

**The executable carries a version resource.** Right-click RE4.exe, Properties,
Details — until now that tab was blank. It is the first place anyone looks when
a shop has two tills and cannot tell which one is behind, and it is what
deployment tooling reads when deciding whether a machine needs updating. The
file now states its version, its product and company name, and a description
that reads sensibly beside the process in Task Manager.

The number is not written into the build script. It is read from the same
constant the application reports when asked for its version, so the two cannot
come apart.

Windows asks for a fourth number after the three this project keeps; it is zero
rather than something invented. The copyright field is left empty for the same
reason — the repository names no holder, and a version resource is the wrong
place to decide one.

### Tests

The version is declared in four places that have to agree: the application, the
package metadata, the installer and this file. Until now a comment in
`installer.iss` asked whoever was releasing to keep them in step. A release with
the application reporting one number and the installer producing a file named
after another would have built, passed and published, and only been noticed by
whoever downloaded the odd-looking file. That is now checked.

## [2.7.1] — 2026-09-07

No schema change, and nothing to relearn. One thing on screen was wrong to
look at, and two ways the program complained about problems it did not have.

### Fixed

**The Gift cards and Layaways screens showed a black diamond where the
punctuation should be.** Eight characters had been lost to a bad encoding, so
the summary line under Gift cards read `12 card(s) <?> 9 active <?> the shop
owes $340.00 on cards` — with the diamond in place of each `<?>` — both search
boxes ended their prompt with the same mark, and every layaway with no due date
showed one in the date column. Nothing was broken behind it: the figures were
right and the cards worked. But a shop looking at that has every reason to
think something is. The dots, dashes and ellipsis are back, matching the rest
of the application.

Losing them again would be just as quiet, so it is now checked mechanically:
the tests refuse any source file carrying a replacement character, and name
the file and the line.

**A modal closed within a tenth of a second no longer reports an error.**
Every dialog claims the keyboard 80 milliseconds after it opens, which is long
enough for the window manager to have finished with it. Closing the dialog
before that moment left the appointment standing with nothing to keep it, and
the program wrote `invalid command name` to its output. Harmless, invisible in
the installed build, and exactly the kind of false alarm that teaches people to
skip past the real ones. The dialog now cancels its own appointment on the way
out.

### Tests

The test run printed seventeen of those same false alarms, from screens that
were torn down while the toolkit still had deferred work queued on them. The
teardown that the screen tests already used now lives in `tests/support.py`
where every Tk test can reach it, and the run is silent. A test suite whose
output is expected to contain errors is a test suite nobody reads.

## [2.7.0] — 2026-09-07

No schema change. The shop behaves as it did; it stops slowing down as its
records pile up.

### Changed

**Long lists stop at 500 rows, and say so.** A shop that has traded for years
holds tens of thousands of products and customers. Nobody reads the
twelve-thousandth row, but the screen still built it, one widget at a time,
every time the list refreshed. The products, customers and suppliers listings
now stop at the first 500 and print a line under the table saying where they
stopped and to search for the rest. A cap the person cannot see would be a
lie: they would have no way of knowing the customer they wanted was simply
past the end.

The lists that must be whole still are. Exports write the entire catalogue and
every customer, the reorder list sees every product below its level, and the
menus you pick a customer or a supplier from hold all of them — those callers
ask for no limit, and a test holds them to it.

**A stock take stops re-reading itself.** The count sheet was already held in
memory; finding a scanned line still walked it from the top, the totals were
re-queried from the database after every barcode, and the whole worksheet was
rebuilt to change one number. On a thousand-line count that was a round trip
and a thousand rows per scan. The sheet is now keyed by product, the totals
are added up from the copy already in hand, and a scan rewrites the single row
it changed. Filtering to "not counted yet" still rebuilds the list, because a
line that has just been counted has to leave it.

**The stock movement history can be trimmed.** It grows by a row for every
line sold, which makes it the fastest-growing table in the file. Settings →
Data safety takes a number of days for it, on the same terms as the audit log
and the receipts: kept for ever unless you name one, because knowing where the
stock went a year ago is worth more than the disk space.

**The CSV import shows a busy cursor** while it reads a supplier's file and
while it writes the rows. The cursor is released before any error dialog, so a
failure is never reported under a busy pointer.

## [2.6.0] — 2026-09-06

Schema v5, v6 and v7 — all additive: an existing shop database gains nullable
columns and new tables on its next start-up, and nothing it already has is
touched. This release is the rest of the shop: categories that tax
differently, gift cards, and layaways.

### Added

**A category can carry its own tax rate.** Essentials are often taxed
differently from everything else, and the store-wide rate could not say so.
Each category has a tax percentage of its own now; blank means whatever the
store charges. The rate travels with the cart line the way the cost does —
taken when the line is added, so a rate changed mid-sale does not rewrite the
cart — and the invoice discount comes off before tax, shared across lines in
proportion to what each is worth. One percentage everywhere is computed
exactly as it always was.

**Gift cards, sold and taken at the till.** A card is sold like any other
line: a button mints a code, the sale pays for it, and the card comes alive
inside that sale's transaction, so a rolled-back sale never leaves a live
card behind. It is spent by typing the code where the payment goes — the
card pays what it holds towards the total, re-checked in the same
transaction that writes the sale, and the rest is taken by the chosen
payment method as usual. Every movement is one signed event row, so a
card's history reads like the customer ledger reads. The Till screen has a
Gift cards window: what the shop owes on cards together, one card's history,
and — for an administrator — issuing and disabling. A gift card line cannot
be handed back as a cash refund, and a card cannot pay for another card.

**Layaways: goods set aside, paid over time.** A layaway keeps three
promises. The price is frozen when the goods are held, so next month's price
change cannot quietly rewrite somebody's agreement. A cash deposit is real
money from the moment it is taken — it goes into the drawer as a cash
movement, and collecting the rest does not count it twice; the invoice
records what it has received across both moments. And the shelves tell the
truth: stock is not decremented while goods sit in the back room, and
collection runs through the same stock check as any other sale, so goods
sold in the meantime stop the collection with an explanation instead of an
oversold invoice. Held from the New Sale screen, collected or cancelled from
the Till screen; cancelling hands the deposit back out of the drawer, and
refuses to do so into a till that is not open, before anything is written.

### Tests

- 540, up from 470.

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

**A welcome that knows it is a first meeting.** The first time an
administrator signs in, RE4 asks the shop's own five questions — name, phone,
address, the exchange rate, the tax rate — in one dialog, and then never asks
again. Everything it touches was always editable in Settings; this just means
a shop does not trade a week under a store name of "RE4 Store" because nobody
knew where to look.

**A quiet word when there is a new version.** Once a day, away from the till
thread, RE4 asks its own releases page whether anything newer exists. If so,
one strip appears at the top of the screen — *Version 2.6.0 is available*,
with a button to the release notes and one to dismiss it. An offline shop
gets no banner and no error, which is the correct answer.

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

- 503, up from 470.

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
