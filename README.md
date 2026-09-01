# RE4 — Business & Retail Management

A desktop point-of-sale and stock-control application for small shops, built with
Python 3, CustomTkinter and SQLite. It runs offline on a single machine, prices
in USD, and settles and prints in both **USD and LBP** at a rate you control.

---

## What it does

| Screen | What you get |
| --- | --- |
| **Dashboard** | Revenue today and this month, gross profit, stock value, a 7-day revenue chart, restock alerts and recent sales |
| **New Sale** | Search or scan a SKU, build a cart, apply a discount, take payment in USD or LBP, see the change due, print a receipt |
| **Invoices** | Search past sales by invoice number, customer or cashier; view line-by-line detail; reprint or save the PDF; refund |
| **Products** | Full catalogue with cost, price, stock and reorder level; categories; stock adjustments with a complete movement history |
| **Customers** | Contact details, lifetime spend, purchase history |
| **Reports** | Revenue, margin, best sellers, top customers, payment mix, sales per user, CSV export |
| **Users** | Admin and Employee accounts, password resets, activation |
| **Settings** | Store details printed on receipts, exchange rate, tax rate, LBP rounding, light/dark theme |

**Roles.** *Admin* sees everything. *Employee* can make sales, view invoices,
manage customers and adjust stock — but not edit the catalogue, view reports, or
touch users and settings.

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

| | Running from source | Running the .exe |
| --- | --- | --- |
| Database | `re4.db` in the project folder | `re4.db` beside `RE4.exe` |
| Receipts | `receipts/` in the project folder | `receipts/` beside `RE4.exe` |

Override both with `--data-dir` or the `RE4_DATA_DIR` environment variable.

**Back up `re4.db`.** It is the entire shop — products, sales, customers and
users. Copy it somewhere safe on a schedule; it is a single self-contained file.
It is also listed in `.gitignore` so a live shop database is never committed.

---

## Building a Windows executable

```
build_executable.bat
```

This creates a virtual environment, installs PyInstaller and produces
`dist\RE4.exe`. Put the executable in a folder you can write to — **not** inside
`Program Files` — because it creates its database and receipts folder next to
itself on first run.

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
  receipts.py            PDF receipt generation (ReportLab)
  services/              business logic, free of any UI imports
    products.py  customers.py  sales.py  reports.py  settings.py
  ui/
    app.py               root window, login/shell swap
    shell.py             sidebar navigation and page header
    theme.py             colour tokens, fonts, ttk styling
    widgets.py           cards, tables, modals, forms
    login.py  dashboard_view.py  pos_view.py  products_view.py
    customers_view.py  invoices_view.py  reports_view.py
    users_view.py  settings_view.py  receipt_actions.py
tests/                   96 unit tests over the service layer
RE4.spec                 PyInstaller build definition
```

The `services/` package imports no Tkinter, which is what makes the business
rules directly testable.

---

## Tests

```bash
python -m unittest discover -s tests -t .
```

96 tests cover money arithmetic, password hashing and the admin guards, stock
movements, the checkout pipeline (including rollback when stock runs out
mid-sale), invoice numbering, refunds, reporting aggregates and PDF generation.

---

## Database schema

`users`, `categories`, `products`, `customers`, `sales`, `sale_items`,
`inventory_log`, `settings` — created automatically on first run and versioned
through `PRAGMA user_version`, so future upgrades can migrate an existing shop
database rather than replace it.

Two deliberate choices worth knowing:

- **`sale_items` stores the name, SKU, price and cost as they were at the time
  of sale.** Renaming a product or changing its cost never rewrites history, and
  reported margins stay accurate.
- **Nothing that has been sold is ever hard-deleted.** Products that appear on an
  invoice are archived instead, and users are deactivated rather than removed, so
  every past invoice still resolves.
