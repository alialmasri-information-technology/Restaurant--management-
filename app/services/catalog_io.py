"""CSV import and export for the product catalogue.

The importer is deliberately two-phase: :func:`analyse` reads the file and
reports exactly what would happen to every row, and :func:`apply` commits that
plan. Nothing is written until someone has seen the preview, which is what makes
loading a 900-line spreadsheet from a supplier survivable.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from app import db, spreadsheets
from app.money import D
from app.services import audit
from app.services import products as products_service
from app.services import suppliers as suppliers_service

# Accepted spellings for each field, lower-cased and stripped of spaces.
COLUMN_ALIASES = {
    "sku": ("sku", "code", "itemcode", "productcode"),
    "name": ("name", "product", "productname", "description1", "title"),
    "barcode": ("barcode", "ean", "upc", "scancode"),
    "category": ("category", "group", "department"),
    "supplier": ("supplier", "vendor"),
    "cost_usd": ("cost", "costusd", "costprice", "buyprice", "purchaseprice"),
    "price_usd": ("price", "priceusd", "sellprice", "sellingprice", "retail"),
    "stock_qty": ("stock", "stockqty", "qty", "quantity", "onhand"),
    "reorder_level": ("reorder", "reorderlevel", "minstock", "min"),
    "description": ("description", "notes", "details"),
}

EXPORT_COLUMNS = (
    "sku", "barcode", "name", "category", "supplier", "cost_usd", "price_usd",
    "stock_qty", "reorder_level", "description", "is_active",
)

TEMPLATE_ROW = {
    "sku": "ABC-001",
    "barcode": "5901234123457",
    "name": "Example product",
    "category": "Beverages",
    "supplier": "Levant Wholesale",
    "cost_usd": "1.20",
    "price_usd": "2.50",
    "stock_qty": "24",
    "reorder_level": "6",
    "description": "Optional free text",
    "is_active": "1",
}


class ImportError_(Exception):
    """Raised when the file itself cannot be used at all."""


@dataclass
class RowPlan:
    line_no: int
    sku: str
    name: str
    action: str  # "create", "update" or "error"
    message: str = ""
    values: dict = field(default_factory=dict)
    product_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.action in ("create", "update")


@dataclass
class ImportPlan:
    rows: list[RowPlan] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def creates(self) -> int:
        return sum(1 for row in self.rows if row.action == "create")

    @property
    def updates(self) -> int:
        return sum(1 for row in self.rows if row.action == "update")

    @property
    def errors(self) -> list[RowPlan]:
        return [row for row in self.rows if row.action == "error"]

    @property
    def applicable(self) -> list[RowPlan]:
        return [row for row in self.rows if row.ok]

    def summary(self) -> str:
        parts = [f"{self.creates} new", f"{self.updates} updated"]
        if self.errors:
            parts.append(f"{len(self.errors)} with problems")
        return ", ".join(parts)


def _normalise(header: str) -> str:
    return "".join(character for character in header.lower() if character.isalnum())


def _map_columns(fieldnames) -> tuple[dict, list]:
    """Map the file's headers onto our field names."""
    mapping, unknown = {}, []
    for header in fieldnames or []:
        key = _normalise(header)
        for field_name, aliases in COLUMN_ALIASES.items():
            if key == field_name or key in aliases:
                mapping[field_name] = header
                break
        else:
            unknown.append(header)
    return mapping, unknown


def analyse(path) -> ImportPlan:
    """Read the CSV and work out what each row would do. Writes nothing."""
    path = Path(path)
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ImportError_(f"Could not open {path.name}: {exc}") from exc

    # The catalogue is read once, not once per row: a 900-line supplier file
    # otherwise means 900 queries on the UI thread to answer the same question
    # the first one did.
    catalogue = {
        row["sku"].lower(): row
        for row in db.query(
            """
            SELECT product_id, sku, name, barcode, cost_usd, price_usd,
                   stock_qty, reorder_level
            FROM products
            """
        )
    }

    with handle:
        reader = csv.DictReader(handle)
        mapping, unknown = _map_columns(reader.fieldnames)
        missing = {"sku", "name"} - set(mapping)
        if missing:
            raise ImportError_(
                "The file needs at least a 'sku' and a 'name' column. "
                f"Found: {', '.join(reader.fieldnames or ['nothing'])}"
            )

        plan = ImportPlan(unknown_columns=unknown)
        seen: set[str] = set()
        for line_no, raw in enumerate(reader, start=2):
            plan.rows.append(_plan_row(line_no, raw, mapping, seen, catalogue))
    return plan


def _plan_row(line_no: int, raw: dict, mapping: dict, seen: set, catalogue: dict) -> RowPlan:
    def value(field_name, default=""):
        column = mapping.get(field_name)
        if column is None:
            return default
        # plain_text undoes the apostrophe an export adds in front of a cell a
        # spreadsheet would otherwise run as a formula, so our own file reads
        # back exactly as it was written.
        return spreadsheets.plain_text((raw.get(column) or "").strip())

    sku = value("sku")
    name = value("name")
    if not sku and not name:
        return RowPlan(line_no, "", "", "error", "Empty row.")
    if not sku:
        return RowPlan(line_no, sku, name, "error", "Missing SKU.")
    if not name:
        return RowPlan(line_no, sku, name, "error", "Missing product name.")
    if sku.lower() in seen:
        return RowPlan(line_no, sku, name, "error", f"SKU '{sku}' appears twice in the file.")
    seen.add(sku.lower())

    values = {
        "sku": sku,
        "name": name,
        "barcode": value("barcode"),
        "category": value("category"),
        "supplier": value("supplier"),
        "description": value("description"),
    }

    for field_name, label in (("cost_usd", "cost"), ("price_usd", "price")):
        text = value(field_name)
        if text == "":
            values[field_name] = None
            continue
        try:
            amount = D(text)
        except Exception:  # noqa: BLE001 - reported per row, not raised
            return RowPlan(line_no, sku, name, "error", f"'{text}' is not a valid {label}.")
        if amount < 0:
            return RowPlan(line_no, sku, name, "error", f"{label.capitalize()} cannot be negative.")
        values[field_name] = amount

    for field_name, label in (("stock_qty", "stock"), ("reorder_level", "reorder level")):
        text = value(field_name)
        if text == "":
            values[field_name] = None
            continue
        try:
            number = int(D(text))
        except Exception:  # noqa: BLE001
            return RowPlan(line_no, sku, name, "error", f"'{text}' is not a whole {label}.")
        if number < 0:
            return RowPlan(line_no, sku, name, "error", f"{label.capitalize()} cannot be negative.")
        values[field_name] = number

    existing = catalogue.get(sku.lower())
    if existing is None:
        if values["price_usd"] is None:
            return RowPlan(line_no, sku, name, "error", "A new product needs a price.")
        return RowPlan(line_no, sku, name, "create", "Will be added", values)

    changes = _describe_update(existing, values)
    return RowPlan(
        line_no, sku, name, "update",
        changes or "No changes", values, existing["product_id"],
    )


def _describe_update(existing, values) -> str:
    parts = []
    if values["name"] != existing["name"]:
        parts.append("name")
    if values["price_usd"] is not None and float(values["price_usd"]) != existing["price_usd"]:
        parts.append(f"price {existing['price_usd']:.2f}→{float(values['price_usd']):.2f}")
    if values["cost_usd"] is not None and float(values["cost_usd"]) != existing["cost_usd"]:
        parts.append(f"cost {existing['cost_usd']:.2f}→{float(values['cost_usd']):.2f}")
    if values["stock_qty"] is not None and values["stock_qty"] != existing["stock_qty"]:
        parts.append(f"stock {existing['stock_qty']}→{values['stock_qty']}")
    if values["reorder_level"] is not None and values["reorder_level"] != existing["reorder_level"]:
        parts.append("reorder level")
    if values["barcode"] and values["barcode"] != existing["barcode"]:
        parts.append("barcode")
    return ", ".join(parts)


def apply(plan: ImportPlan, user_id: int | None = None) -> dict:
    """Commit the applicable rows of a plan. All or nothing."""
    rows = plan.applicable
    if not rows:
        raise ImportError_("There is nothing in this file to import.")

    created = updated = stock_moved = 0
    with db.transaction() as conn:
        for row in rows:
            values = row.values
            category_id = (
                products_service.get_or_create_category(values["category"])
                if values["category"] else None
            )
            supplier_id = _supplier_id(values["supplier"])

            if row.action == "create":
                product_id = products_service.create_product(
                    sku=values["sku"],
                    name=values["name"],
                    barcode=values["barcode"],
                    price_usd=values["price_usd"] or 0,
                    cost_usd=values["cost_usd"] or 0,
                    category_id=category_id,
                    supplier_id=supplier_id,
                    description=values["description"],
                    stock_qty=values["stock_qty"] or 0,
                    reorder_level=values["reorder_level"]
                    if values["reorder_level"] is not None else 5,
                    user_id=user_id,
                    conn=conn,
                )
                created += 1
                if values["stock_qty"]:
                    stock_moved += values["stock_qty"]
                row.product_id = product_id
                continue

            existing = conn.execute(
                "SELECT * FROM products WHERE product_id = ?", (row.product_id,)
            ).fetchone()
            conn.execute(
                """
                UPDATE products
                   SET name = ?, barcode = ?, description = ?,
                       category_id = COALESCE(?, category_id),
                       supplier_id = COALESCE(?, supplier_id),
                       cost_usd = ?, price_usd = ?, reorder_level = ?,
                       updated_at = datetime('now', 'localtime')
                 WHERE product_id = ?
                """,
                (
                    values["name"],
                    values["barcode"] or existing["barcode"],
                    values["description"] or existing["description"],
                    category_id,
                    supplier_id,
                    float(values["cost_usd"]) if values["cost_usd"] is not None
                    else existing["cost_usd"],
                    float(values["price_usd"]) if values["price_usd"] is not None
                    else existing["price_usd"],
                    values["reorder_level"] if values["reorder_level"] is not None
                    else existing["reorder_level"],
                    row.product_id,
                ),
            )
            updated += 1

            if values["stock_qty"] is not None:
                delta = values["stock_qty"] - existing["stock_qty"]
                if delta:
                    products_service.adjust_stock(
                        row.product_id, delta, reason="Import", user_id=user_id,
                        note="CSV import", conn=conn,
                    )
                    stock_moved += abs(delta)

    audit.record(
        "Catalogue imported", "product", "",
        f"{created} created, {updated} updated, {stock_moved} stock units moved",
    )
    return {"created": created, "updated": updated, "stock_moved": stock_moved}


def _supplier_id(name: str) -> int | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = suppliers_service.find_by_name(name)
    if existing is not None:
        return existing["supplier_id"]
    return suppliers_service.create_supplier(name=name)


def export_products(path, include_inactive: bool = True) -> int:
    """Write the catalogue to a CSV the importer can read back. Returns row count."""
    # An export is the whole catalogue by definition.
    rows = products_service.list_products(include_inactive=include_inactive, limit=None)
    safe = spreadsheets.safe_cell
    path = Path(path)
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    # Every free-text field below is something a person or a
                    # supplier's file put there, so any of them can arrive
                    # starting a formula. The numbers are not escaped: they are
                    # formatted here from numeric columns, and prefixing a
                    # negative one would land it in the spreadsheet as text.
                    "sku": safe(row["sku"]),
                    "barcode": safe(row["barcode"]),
                    "name": safe(row["name"]),
                    "category": safe(row["category_name"] or ""),
                    "supplier": safe(row["supplier_name"] or ""),
                    "cost_usd": f"{row['cost_usd']:.2f}",
                    "price_usd": f"{row['price_usd']:.2f}",
                    "stock_qty": row["stock_qty"],
                    "reorder_level": row["reorder_level"],
                    "description": safe(row["description"]),
                    "is_active": 1 if row["is_active"] else 0,
                })
    except OSError as exc:
        raise ImportError_(f"Could not write {path.name}: {exc}") from exc
    return len(rows)


def write_template(path) -> Path:
    """A one-row example file, so nobody has to guess the column names."""
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerow(TEMPLATE_ROW)
    return path
