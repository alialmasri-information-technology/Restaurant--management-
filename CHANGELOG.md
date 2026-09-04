# Changelog

All notable changes to RE4 are recorded here. Versions follow
[semantic versioning](https://semver.org/): the major number moves when a shop
database needs a migration it cannot undo, the minor when features are added,
the patch for fixes.

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
