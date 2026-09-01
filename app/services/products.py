"""Products, categories and stock movements."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from app import config, db, logs
from app.money import D, to_float, usd
from app.services import audit


class ProductError(Exception):
    """Raised for user-facing product/stock failures."""


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #

def list_categories() -> list[sqlite3.Row]:
    return db.query("SELECT * FROM categories ORDER BY name COLLATE NOCASE")


def get_or_create_category(name: str) -> int | None:
    """Used by the CSV importer, which works in category names, not ids."""
    name = (name or "").strip()
    if not name:
        return None
    row = db.query_one("SELECT category_id FROM categories WHERE name = ?", (name,))
    if row is not None:
        return row["category_id"]
    return create_category(name)


def create_category(name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ProductError("Category name is required.")
    try:
        return db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError as exc:
        raise ProductError(f"A category named '{name}' already exists.") from exc


def rename_category(category_id: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise ProductError("Category name is required.")
    try:
        db.execute(
            "UPDATE categories SET name = ? WHERE category_id = ?", (name, category_id)
        )
    except sqlite3.IntegrityError as exc:
        raise ProductError(f"A category named '{name}' already exists.") from exc


def delete_category(category_id: int) -> None:
    """Products in the category are kept and become uncategorised."""
    db.execute("DELETE FROM categories WHERE category_id = ?", (category_id,))


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #

_PRODUCT_SELECT = """
    SELECT p.*, c.name AS category_name, s.name AS supplier_name,
           (p.stock_qty <= p.reorder_level) AS is_low_stock,
           (p.stock_qty * p.cost_usd) AS stock_value_usd
    FROM products p
    LEFT JOIN categories c ON c.category_id = p.category_id
    LEFT JOIN suppliers s ON s.supplier_id = p.supplier_id
"""


def list_products(
    search: str = "",
    category_id: int | None = None,
    supplier_id: int | None = None,
    include_inactive: bool = False,
    low_stock_only: bool = False,
    in_stock_only: bool = False,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []

    if not include_inactive:
        clauses.append("p.is_active = 1")
    if search and search.strip():
        clauses.append(
            "(p.name LIKE ? OR p.sku LIKE ? OR p.barcode LIKE ? OR p.description LIKE ?)"
        )
        pattern = f"%{search.strip()}%"
        params += [pattern] * 4
    if category_id:
        clauses.append("p.category_id = ?")
        params.append(category_id)
    if supplier_id:
        clauses.append("p.supplier_id = ?")
        params.append(supplier_id)
    if low_stock_only:
        clauses.append("p.stock_qty <= p.reorder_level")
    if in_stock_only:
        clauses.append("p.stock_qty > 0")

    sql = _PRODUCT_SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY p.name COLLATE NOCASE"
    return db.query(sql, tuple(params))


def get_product(product_id: int) -> sqlite3.Row | None:
    return db.query_one(_PRODUCT_SELECT + " WHERE p.product_id = ?", (product_id,))


def get_by_sku(sku: str) -> sqlite3.Row | None:
    return db.query_one(_PRODUCT_SELECT + " WHERE p.sku = ?", ((sku or "").strip(),))


def get_by_code(code: str) -> sqlite3.Row | None:
    """Look a product up by SKU or by scanned barcode."""
    code = (code or "").strip()
    if not code:
        return None
    return db.query_one(
        _PRODUCT_SELECT + " WHERE p.sku = ? OR (p.barcode != '' AND p.barcode = ?)",
        (code, code),
    )


def low_stock_products() -> list[sqlite3.Row]:
    return list_products(low_stock_only=True)


def _validate(name: str, sku: str, price, cost, reorder_level) -> None:
    """Callers may pass numbers as strings, Decimals or floats; all are accepted."""
    if not (name or "").strip():
        raise ProductError("Product name is required.")
    if not (sku or "").strip():
        raise ProductError("SKU is required.")
    try:
        price, cost = D(price), D(cost)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a message
        raise ProductError("Price and cost must be numbers.") from exc
    if price < 0 or cost < 0:
        raise ProductError("Price and cost cannot be negative.")
    try:
        reorder_level = int(reorder_level)
    except (TypeError, ValueError) as exc:
        raise ProductError("Reorder level must be a whole number.") from exc
    if reorder_level < 0:
        raise ProductError("Reorder level cannot be negative.")


def create_product(
    *,
    sku: str,
    name: str,
    price_usd,
    cost_usd=0,
    barcode: str = "",
    category_id: int | None = None,
    supplier_id: int | None = None,
    description: str = "",
    stock_qty: int = 0,
    reorder_level: int = 5,
    image_path: str = "",
    user_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    _validate(name, sku, price_usd, cost_usd, reorder_level)
    stock_qty = int(stock_qty)
    if stock_qty < 0:
        raise ProductError("Opening stock cannot be negative.")

    def _apply(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            """
            INSERT INTO products
                (sku, barcode, name, description, category_id, supplier_id,
                 cost_usd, price_usd, stock_qty, reorder_level, image_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sku.strip(),
                (barcode or "").strip(),
                name.strip(),
                (description or "").strip(),
                category_id,
                supplier_id,
                to_float(cost_usd),
                to_float(price_usd),
                stock_qty,
                int(reorder_level),
                (image_path or "").strip(),
            ),
        )
        product_id = cursor.lastrowid
        if stock_qty:
            connection.execute(
                """
                INSERT INTO inventory_log
                    (product_id, change_qty, new_stock, reason, user_id, note)
                VALUES (?, ?, ?, 'Initial', ?, 'Opening stock')
                """,
                (product_id, stock_qty, stock_qty, user_id),
            )
        return product_id

    try:
        if conn is not None:
            product_id = _apply(conn)
        else:
            with db.transaction() as connection:
                product_id = _apply(connection)
    except sqlite3.IntegrityError as exc:
        raise ProductError(f"A product with SKU '{sku}' already exists.") from exc

    audit.record("Product created", "product", product_id,
                 f"{sku.strip()} {name.strip()} @ {usd(price_usd)}")
    return product_id


def update_product(
    product_id: int,
    *,
    sku: str,
    name: str,
    price_usd,
    cost_usd=0,
    barcode: str = "",
    category_id: int | None = None,
    supplier_id: int | None = None,
    description: str = "",
    reorder_level: int = 5,
    is_active: bool = True,
) -> None:
    """Update the catalogue record. Stock is only ever changed via ``adjust_stock``."""
    _validate(name, sku, price_usd, cost_usd, reorder_level)
    before = get_product(product_id)
    if before is None:
        raise ProductError("Product not found.")

    try:
        db.execute(
            """
            UPDATE products
               SET sku = ?, barcode = ?, name = ?, description = ?, category_id = ?,
                   supplier_id = ?, cost_usd = ?, price_usd = ?, reorder_level = ?,
                   is_active = ?, updated_at = datetime('now', 'localtime')
             WHERE product_id = ?
            """,
            (
                sku.strip(),
                (barcode or "").strip(),
                name.strip(),
                (description or "").strip(),
                category_id,
                supplier_id,
                to_float(cost_usd),
                to_float(price_usd),
                int(reorder_level),
                1 if is_active else 0,
                product_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ProductError(f"A product with SKU '{sku}' already exists.") from exc

    detail = audit.describe_changes(
        dict(before),
        {
            "sku": sku.strip(), "name": name.strip(),
            "price_usd": to_float(price_usd), "cost_usd": to_float(cost_usd),
            "reorder_level": int(reorder_level), "is_active": 1 if is_active else 0,
        },
        ["sku", "name", "price_usd", "cost_usd", "reorder_level", "is_active"],
    )
    if detail:
        audit.record("Product updated", "product", product_id, detail)


def set_active(product_id: int, active: bool) -> None:
    db.execute(
        """
        UPDATE products SET is_active = ?, updated_at = datetime('now', 'localtime')
        WHERE product_id = ?
        """,
        (1 if active else 0, product_id),
    )


def delete_product(product_id: int) -> None:
    """Delete outright if never sold, otherwise archive to keep invoices intact."""
    product = get_product(product_id)
    if product is None:
        raise ProductError("Product not found.")

    sold = db.scalar(
        "SELECT COUNT(*) FROM sale_items WHERE product_id = ?", (product_id,), default=0
    )
    ordered = db.scalar(
        "SELECT COUNT(*) FROM purchase_order_items WHERE product_id = ?",
        (product_id,), default=0,
    )
    if sold or ordered:
        set_active(product_id, False)
        audit.record("Product archived", "product", product_id, product["name"])
        raise ProductError(
            "This product appears on past invoices or purchase orders, so it was "
            "archived instead of deleted. It no longer shows up when making a sale."
        )
    db.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
    audit.record("Product deleted", "product", product_id, product["name"])


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #

def set_image(product_id: int, source_path) -> str:
    """Copy an image into the data folder and attach it to the product."""
    source = Path(source_path)
    if not source.exists():
        raise ProductError(f"Image not found: {source}")
    if source.suffix.lower() not in config.IMAGE_EXTENSIONS:
        raise ProductError("Use a PNG, JPG, GIF, BMP or WEBP image.")

    config.ensure_directories()
    target = config.IMAGES_DIR / f"{product_id}{source.suffix.lower()}"
    try:
        for existing in config.IMAGES_DIR.glob(f"{product_id}.*"):
            if existing != target:
                existing.unlink()
        shutil.copyfile(source, target)
    except OSError as exc:
        raise ProductError(f"Could not save the image: {exc}") from exc

    db.execute(
        "UPDATE products SET image_path = ? WHERE product_id = ?",
        (target.name, product_id),
    )
    audit.record("Product image set", "product", product_id, target.name)
    return target.name


def clear_image(product_id: int) -> None:
    product = get_product(product_id)
    if product is None or not product["image_path"]:
        return
    path = image_file(product["image_path"])
    if path is not None:
        try:
            path.unlink()
        except OSError:
            logs.warning("Could not delete product image %s", path)
    db.execute("UPDATE products SET image_path = '' WHERE product_id = ?", (product_id,))


def image_file(image_path: str) -> Path | None:
    """Resolve a stored image name to a path, or None when it is missing."""
    if not image_path:
        return None
    path = config.IMAGES_DIR / image_path
    return path if path.exists() else None


# --------------------------------------------------------------------------- #
# Stock
# --------------------------------------------------------------------------- #

def adjust_stock(
    product_id: int,
    change_qty: int,
    reason: str = "Adjustment",
    user_id: int | None = None,
    note: str = "",
    sale_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Apply a stock movement and log it. Returns the new stock level.

    Pass ``conn`` to join an in-progress transaction (the sale path does this so
    stock, the sale, and the log all commit or roll back together).
    """
    change_qty = int(change_qty)
    if change_qty == 0:
        raise ProductError("Enter a non-zero quantity.")

    def _apply(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT name, stock_qty FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if row is None:
            raise ProductError("Product not found.")
        new_stock = row["stock_qty"] + change_qty
        if new_stock < 0:
            raise ProductError(
                f"Not enough stock for {row['name']}: {row['stock_qty']} available, "
                f"{abs(change_qty)} requested."
            )
        connection.execute(
            """
            UPDATE products SET stock_qty = ?, updated_at = datetime('now', 'localtime')
            WHERE product_id = ?
            """,
            (new_stock, product_id),
        )
        connection.execute(
            """
            INSERT INTO inventory_log
                (product_id, change_qty, new_stock, reason, sale_id, user_id, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, change_qty, new_stock, reason, sale_id, user_id, note or ""),
        )
        return new_stock

    if conn is not None:
        return _apply(conn)

    with db.transaction() as connection:
        new_stock = _apply(connection)
    # Sales, returns and receiving write their own audit entries; only manual
    # moves need one here.
    if reason in ("Adjustment", "Spoilage", "Restock", "Import"):
        audit.record(
            "Stock adjusted", "product", product_id,
            f"{reason} {change_qty:+d} → {new_stock}" + (f" ({note})" if note else ""),
        )
    return new_stock


def stock_history(product_id: int, limit: int = 100) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT l.*, u.username
        FROM inventory_log l
        LEFT JOIN users u ON u.user_id = l.user_id
        WHERE l.product_id = ?
        ORDER BY l.log_id DESC
        LIMIT ?
        """,
        (product_id, limit),
    )
