# RE4 — Business & Retail Management

A desktop point-of-sale and stock-control application for small shops, built with
Python 3, CustomTkinter and SQLite. It runs offline on a single machine, prices
in USD, and settles and prints in both **USD and LBP** at a rate you control.

It covers the whole counter: scanning and selling, the cash drawer, returns,
purchase orders and receiving, barcode labels, receipt printing, backups and an
audit trail of who did what.

---

## What it does

| Screen | What you get |
| --- | --- |
| **Dashboard** | Revenue today and this month, gross profit, stock value, a 7-day revenue chart, restock alerts and recent sales |
| **New Sale** | Scan a barcode or search, build a cart, override a price, discount a line, park a sale and pick it up later, take payment in USD or LBP, print a receipt |
| **Till** | Open a shift with a counted float, record cash in and out, take an X report mid-shift, close with a count that reports the variance and prints a Z report, print the end-of-day sheet |
| **Invoices** | Search past sales; view line-by-line detail; reprint or save the PDF; return part of an invoice or refund all of it |
| **Products** | Catalogue with cost, price, barcode, supplier, stock and reorder level; product photos; barcode label sheets; CSV import and export; stock adjustments with a full movement history |
| **Stock take** | Count the shelves against the system: scan or type, see the variance in units and at cost as you go, and post it when it is signed off |
| **Purchasing** | Suppliers, purchase orders, receiving stock against an order at weighted-average cost, and a reorder list that can raise the orders for you |
| **Customers** | Contact details, lifetime spend, purchase history, and accounts: a credit limit, what is owed, a statement, and taking payment against it |
| **Reports** | Revenue net of returns, margin, best sellers, top customers, payment mix, sales per user, stock valuation, dead stock, money owed to you, the end-of-day sheet for any past day, CSV export |
| **Users** | Admin and Employee accounts, password resets, activation |
| **Settings** | Store details, exchange rate, tax, printer and receipt width, screen lock and sign-in throttling, backups and restore, the audit log, light/dark theme |

**Roles.** *Admin* sees everything. *Employee* can make sales, run the till, view
invoices, manage customers, take payments against an account, adjust stock and
count a stock take — but not edit the catalogue, post a stock take, adjust a
customer's balance, raise purchase orders, view reports, or touch users and
settings.

### Keyboard at the till

| Key | Does |
| --- | --- |
| `Enter` in the search box | Adds the scanned or exactly matched product |
| `F2` / `F3` | Park the sale / open the held sales |
| `F4` | Complete the sale |
| `F6` / `F7` / `F8` | Quantity / line discount / price override |
| `Ctrl+L` | Back to the search box |
| `Ctrl+Shift+L` | Lock the screen (anywhere in the app) |

---

## Requirements

- Python 3.10 or newer (developed on 3.12)
- Windows, macOS or Linux with Tkinter available (bundled with the standard
  Windows and macOS Python installers; on Debian/Ubuntu run
  `sudo apt install python3-tk`)

---

## Running it

### Windows, the short way

Double-click **`run.bat`**. It installs the dependencies the first time and
starts the app.

### Any platform

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

### First sign-in

```
Username: admin
Password: admin123
```

The bootstrap account is created only when the database has no users at all.
Because that password is printed here and shown on the login screen, RE4 insists
on a real one: signing in with it opens a dialog that will not let you past until
you choose your own. Then add real accounts under *Users*.

To explore with a sample catalogue on a fresh database:

```bash
python main.py --demo
```

Other options. Each of these does one thing and exits without opening the
application — they exist for the moment something has gone wrong and nobody can
get into the interface to fix it:

```bash
python main.py --version                      # print the version
python main.py --check                        # verify the database, summarise it
python main.py --backup                       # write a backup
python main.py --reset-admin "new-password"   # lost the admin password
python main.py --unlock cashier               # clear a sign-in lockout ("all" for every one)
python main.py --data-dir "D:\shop"           # keep the data somewhere specific
```

`--check` is the one to run after a power cut or a crash: it reports the
integrity of the file, the schema version, how much is in it, and which accounts
are locked out. It exits non-zero if SQLite finds damage.

---

## Where your data lives

Everything sits in one folder: the project folder when running from source, or
the folder holding `RE4.exe` when running the executable.

| | Holds |
| --- | --- |
| `re4.db` | The entire shop: products, sales, customers, users, the audit log |
| `receipts/` | Generated receipts, return slips, till reports and label sheets |
| `backups/` | Database snapshots |
| `images/` | Product photos |
| `logs/` | The rolling application log |

Override the location with `--data-dir` or the `RE4_DATA_DIR` environment
variable.

**Backups.** RE4 snapshots the database at start-up and keeps the most recent
few (both settings live under *Settings → Data safety*, where you can also back
up on demand, restore, and run an integrity check). Snapshots use SQLite's own
backup API, so one taken while the shop is trading is still consistent — a plain
file copy of a live database is not. Restoring saves a copy of the current data
first, so it can always be undone.

Copy the `backups/` folder off the machine periodically. A backup on the same
disk does not survive the disk failing.

---

## Building a Windows executable

```
build_executable.bat
```

This creates a virtual environment, installs PyInstaller and produces
`dist\RE4.exe`. Put the executable in a folder you can write to — **not** inside
`Program Files` — because it creates its database, receipts, backups, images and
logs folders next to itself on first run.

---

## Printing

Receipts, return slips, till reports and label sheets are all PDFs written into
`receipts/`. Pick a printer under *Settings → Printing* and RE4 sends them
straight to it after a sale; leave it unset and it opens each one in your PDF
viewer to print by hand instead. Set the receipt width to match the paper —
80 mm or 58 mm — and the layout, font size and column widths follow.

Product labels print as Code128 barcodes on A4 sheets (24 or 12 to a page) or on
50 x 30 mm thermal roll stock. A product with no barcode falls back to its SKU,
the quantity starts at the stock on hand, and the price in USD, the price in LBP
and the shop name are each optional.

---

## The till and the audit trail

Selling requires an open till shift (this can be switched off in *Settings*).
The shift records the opening float, every cash movement, and which sales and
refunds went through it, so closing produces a real variance: what the drawer
should hold against what was counted. LBP in the drawer is converted at the rate
the shift opened with.

Separately, the audit log records who did what — sign-ins and failed sign-ins,
price overrides and discounts, stock adjustments, catalogue edits, returns,
goods received, shifts opened and closed, and every account change. Read it
under *Settings → Audit log*. Passwords are never written to it, only the fact
that one changed.

---

## Keeping the till safe

A point of sale stands on a counter all day, often with nobody in front of it.
Three things guard it, all under *Settings → Security*:

**The screen locks.** After the configured idle time — 15 minutes out of the box,
or on demand from the sidebar or `Ctrl+Shift+L` — the screen is covered and only
the operator's password brings it back. The shell underneath is left intact, so a
half-built cart, the open shift and the page you were on are all still there.
Signing out instead is one button away.

**Wrong passwords are throttled.** Five consecutive failures lock an account for
five minutes; both numbers are yours to set, and 0 switches it off. Unknown
usernames are throttled the same way, so the lockout never reveals which accounts
exist. An administrator can clear a lockout from *Users*, from *Settings*, or —
when nobody can get in at all — with `python main.py --unlock all`.

**A password somebody else set has to be replaced.** The bootstrap
`admin`/`admin123` account, and any password an administrator resets, must be
changed at the next sign-in before the app will do anything else.

Separately, whenever a correct password is entered against a hash made at an
older work factor, it is quietly re-hashed at the current one — so an account
made years ago does not keep years-old protection.

## Counting the stock

Recorded stock drifts. Theft, breakage, miskeyed receiving and mis-scanned sales
all leave the system holding more than the shelf does, and counting is the only
way to find out. *Stock take* runs one properly:

1. **Open a count.** Everything in scope — the whole catalogue, or one category —
   is frozen onto a worksheet at the quantity the system currently believes. The
   till keeps trading; the worksheet does not move.
2. **Count.** Scan with a barcode reader and each scan adds one, or select a line
   and type the figure. The sheet filters to what is still uncounted, or to the
   lines that disagree, and the shortage, the surplus and what the variance is
   worth at cost are on screen the whole time.
3. **Apply.** The *difference* is posted to stock, not the counted number — so
   the five units sold during the count are not silently put back. Every
   adjustment lands in that product's stock history tagged with the count's
   reference, and lines nobody counted are left exactly as they are.

A count can be abandoned at any point with no effect on stock, and past counts
are kept, so a pattern of shrinkage in one aisle becomes visible over time. Staff
can count; only an administrator can post the variance.

## Selling on account

*Credit* has always been one of the payment methods. Until 2.2 it recorded no
debt — the goods went out of the door and the money was simply forgotten. It is
now a real account.

**Set a limit first.** *Customers → Credit limit*. A customer with no limit
cannot buy on account at all; that is deliberate, so credit is something granted
rather than something a busy cashier can hand out by picking the wrong payment
method. Before an invoice goes on account the balance plus this sale is checked
against the limit, and the sale is refused *before* anything is written if it
would go over — the cashier loses a payment method, not a half-finished sale.

**The balance is a ledger, not a number.** Every movement writes one signed row:
a credit sale adds, a payment or a refund subtracts, and an owner's adjustment
does either. The balance is the sum of those rows, so it can never drift out of
step with them, and *Statement* can explain every cent in order with the invoice
or receipt number beside it. A mistaken payment is reversed with a visible
adjustment, never by deleting history.

**Taking the money back.** *Customers → Take a payment* accepts USD or LBP at
the rate in force, in full or in part, by any payment method. A payment larger
than the balance is refused — on a counter that is nearly always a typo. **Cash
taken against an account reaches the till**: it is attached to the open shift and
counted in the expected drawer figure, so the close does not come up over, and
both the X and Z reports print it.

**Returns off a credit sale reduce the debt** rather than paying cash out of a
drawer that never took any in.

*Customers* shows what each one owes and their limit, filters to just the people
who owe, and totals the receivable; anyone at or over their limit is flagged, so
the person on the counter sees it before they try. *Reports* carries the same
total and the list behind it, and `--check` prints it from the command line.

## Closing the day

Everything needed to close up was already recorded, but it was scattered: the Z
report knew about one drawer, *Reports* knew about revenue and margin,
*Customers* knew what was owed, and nothing tied a day together. *Till → Day
report* prints the lot on one sheet, in the order the person locking the door
works through it:

1. **Anything unfinished, first.** A till still open, a drawer that did not come
   back to its expected figure, sales taken with no shift open. These are the
   things that get discovered a week later, when nobody remembers the day well
   enough to explain them.
2. **Did we trade well?** Sales, discounts, returns netted off, net revenue in
   USD and LBP, tax, gross profit and margin, units, average sale, what it was
   taken as, who served, and the five best sellers.
3. **Does the money add up?** Every drawer opened that day, expected against
   counted, with the day's total variance underneath. An uncounted drawer reads
   as *not yet counted* — never as balanced.
4. **What is still outstanding?** Put on account today, paid off today (and how
   much of that is cash sitting in the drawer), and the receivable being carried
   into tomorrow.
5. **What moved that was not a sale?** Stock received, and any stock take posted
   with its variance at cost.

Then two sign-off lines, for whoever counted and whoever checked.

The day is bounded by the date, not by a shift, so a drawer left open overnight
belongs to the day it opened rather than quietly disappearing from both days. It
prints on the same roll as every other document, because a shop with a thermal
printer at the till usually has nothing else.

Reprint any past day from *Reports → Day report*, or from the command line:

```bash
python main.py --day-report              # today
python main.py --day-report 2026-08-31   # any past day
```

## Money handling

Prices are stored in USD. Every amount is computed with `decimal.Decimal` and
rounded to whole cents half-up, so line totals, discounts and tax never drift
the way binary floats do.

LBP is derived from USD at the rate in *Settings*, rounded to the nearest step
you choose (1,000 LBP by default). **The rate in force is saved on each sale**,
so reprinting a receipt from three months ago shows the LBP total that was
actually charged, not today's conversion.

Passwords are stored as salted PBKDF2-HMAC-SHA256 digests (260,000 iterations)
using only the standard library — nothing is ever kept in plain text.

---

## Project layout

```
main.py                  entry point and command-line options
app/
  config.py              paths, roles, defaults
  db.py                  schema, migrations, connections, demo data
  auth.py                password hashing, sign-in, user management
  money.py               Decimal arithmetic, USD/LBP conversion
  receipts.py            receipts, slips, X/Z reports and the day sheet (ReportLab)
  labels.py              Code128 barcode label sheets
  printing.py            sending a PDF to a printer
  logs.py                rolling application log
  services/              business logic, free of any UI imports
    products.py    customers.py   sales.py       reports.py    settings.py
    shifts.py      returns.py     purchases.py   suppliers.py   accounts.py
    catalog_io.py  backups.py     audit.py       stocktake.py   dayend.py
  ui/
    app.py               root window, login/shell swap
    shell.py             sidebar navigation and page header
    theme.py             colour tokens, fonts, ttk styling
    widgets.py           cards, tables, modals, forms
    security.py          lock screen, forced password change
    login.py             dashboard_view.py  pos_view.py     till_view.py
    products_view.py     purchasing_view.py customers_view.py
    invoices_view.py     reports_view.py    users_view.py
    settings_view.py     stocktake_view.py  receipt_actions.py
tests/                   388 tests over the service layer and every screen
pyproject.toml           metadata, the `re4` entry point, Ruff configuration
RE4.spec                 PyInstaller build definition
.github/workflows/ci.yml lint, test on three platforms, build the executable
```

The `services/` package imports no Tkinter, which is what makes the business
rules directly testable.

---

## Tests

```bash
python -m unittest discover -s tests -t .
```

388 tests, about 25 seconds. They cover money arithmetic, password hashing and
the admin guards, sign-in throttling and forced password changes, stock
movements, the checkout pipeline (including rollback when stock runs out
mid-sale), invoice numbering, partial returns and their pricing, till shifts and
reconciliation, customer accounts (credit limits, part payments, LBP conversion,
returns against a debt, and that cash on an account reaches the drawer), the
end-of-day sheet (including that a day is bounded by its date rather than by a
shift, and that an old sheet reprints the receivable that stood at the end of
that day), stock takes (including that selling during a count survives it), purchase orders and
weighted-average costing, CSV import (including that a failure part-way through
rolls the whole file back), backup and restore, reporting aggregates, barcode
label geometry and PDF generation, and the v2 → v4 upgrade against a database
shaped the way an older release left it — including putting a sale on account
against a database migrated from before accounts existed.

The last group builds every screen against a real, hidden Tk root and refreshes
it. Nothing there asserts what a screen looks like — only that it can be built
and navigated to without throwing, which is the one failure a released build
shows as a blank window. Those tests skip themselves where there is no display,
so they cost nothing on a headless machine.

### Working on it

```bash
pip install -r requirements-dev.txt
ruff check app tests main.py     # lint and import order; configured in pyproject.toml
```

CI runs the linter, then the suite on Linux, Windows and macOS across Python
3.10 to 3.13, then builds the Windows executable.

---

## Database schema

`users`, `categories`, `suppliers`, `products`, `customers`, `shifts`,
`cash_movements`, `sales`, `sale_items`, `returns`, `return_items`,
`parked_sales`, `purchase_orders`, `purchase_order_items`, `stock_takes`,
`stock_take_items`, `customer_ledger`, `inventory_log`, `audit_log`,
`login_throttle`, `settings` — created automatically on first run and versioned
through `PRAGMA user_version`, so an upgrade migrates an existing shop database
rather than replacing it. The current schema is **v4**; an older database is
migrated in place on first start and nothing needs reimporting.

Three deliberate choices worth knowing:

- **`sale_items` stores the name, SKU, price and cost as they were at the time
  of sale.** Renaming a product or changing its cost never rewrites history, and
  reported margins stay accurate.
- **Nothing that has been sold is ever hard-deleted.** Products that appear on an
  invoice are archived instead, and users are deactivated rather than removed, so
  every past invoice still resolves.
- **Returns are netted off revenue, not filtered out of it.** A partly returned
  invoice contributes exactly the part the customer kept, and an invoice-level
  discount is prorated across the returned units — so refunding one of four
  items on a discounted basket refunds the discounted price of that item, not
  the shelf price.

- **A customer's balance is never stored.** `customer_ledger` holds one signed
  row per movement and the balance is their sum. That costs one `SUM` per lookup
  and buys a figure that can always be explained line by line.

- **A stock take posts the difference it found, not the number it counted.** The
  count freezes what the system expected when it opened; applying it writes
  `counted - expected` to stock. Anything sold while the counter was working
  down the aisle therefore survives the adjustment instead of being put back.

See [CHANGELOG.md](CHANGELOG.md) for what changed between releases.
