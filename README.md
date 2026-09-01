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
| **Till** | Open a shift with a counted float, record cash in and out, take an X report mid-shift, close with a count that reports the variance and prints a Z report |
| **Invoices** | Search past sales; view line-by-line detail; reprint or save the PDF; return part of an invoice or refund all of it |
| **Products** | Catalogue with cost, price, barcode, supplier, stock and reorder level; product photos; barcode label sheets; CSV import and export; stock adjustments with a full movement history |
| **Purchasing** | Suppliers, purchase orders, receiving stock against an order at weighted-average cost, and a reorder list that can raise the orders for you |
| **Customers** | Contact details, lifetime spend, purchase history |
| **Reports** | Revenue net of returns, margin, best sellers, top customers, payment mix, sales per user, stock valuation, dead stock, CSV export |
| **Users** | Admin and Employee accounts, password resets, activation |
| **Settings** | Store details, exchange rate, tax, printer and receipt width, backups and restore, the audit log, light/dark theme |

**Roles.** *Admin* sees everything. *Employee* can make sales, run the till, view
invoices, manage customers and adjust stock — but not edit the catalogue, raise
purchase orders, view reports, or touch users and settings.

### Keyboard at the till

| Key | Does |
| --- | --- |
| `Enter` in the search box | Adds the scanned or exactly matched product |
| `F2` / `F3` | Park the sale / open the held sales |
| `F4` | Complete the sale |
| `F6` / `F7` / `F8` | Quantity / line discount / price override |
| `Ctrl+L` | Back to the search box |

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
**Change the password immediately** under *Settings → Change my password*, then
add real accounts under *Users*.

To explore with a sample catalogue on a fresh database:

```bash
python main.py --demo
```

Other options:

```bash
python main.py --reset-admin "new-password"   # locked out? reset and exit
python main.py --data-dir "D:\shop"           # keep the data somewhere specific
```

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
  receipts.py            receipts, return slips and X/Z till reports (ReportLab)
  labels.py              Code128 barcode label sheets
  printing.py            sending a PDF to a printer
  logs.py                rolling application log
  services/              business logic, free of any UI imports
    products.py    customers.py   sales.py       reports.py    settings.py
    shifts.py      returns.py     purchases.py   suppliers.py
    catalog_io.py  backups.py     audit.py
  ui/
    app.py               root window, login/shell swap
    shell.py             sidebar navigation and page header
    theme.py             colour tokens, fonts, ttk styling
    widgets.py           cards, tables, modals, forms
    login.py             dashboard_view.py  pos_view.py     till_view.py
    products_view.py     purchasing_view.py customers_view.py
    invoices_view.py     reports_view.py    users_view.py
    settings_view.py     receipt_actions.py
tests/                   246 unit tests over the service layer
RE4.spec                 PyInstaller build definition
```

The `services/` package imports no Tkinter, which is what makes the business
rules directly testable.

---

## Tests

```bash
python -m unittest discover -s tests -t .
```

246 tests cover money arithmetic, password hashing and the admin guards, stock
movements, the checkout pipeline (including rollback when stock runs out
mid-sale), invoice numbering, partial returns and their pricing, till shifts and
reconciliation, purchase orders and weighted-average costing, CSV import
(including that a failure part-way through rolls the whole file back), backup
and restore, reporting aggregates, barcode label geometry and PDF generation.

---

## Database schema

`users`, `categories`, `suppliers`, `products`, `customers`, `shifts`,
`cash_movements`, `sales`, `sale_items`, `returns`, `return_items`,
`parked_sales`, `purchase_orders`, `purchase_order_items`, `inventory_log`,
`audit_log`, `settings` — created automatically on first run and versioned
through `PRAGMA user_version`, so an upgrade migrates an existing shop database
rather than replacing it.

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
