"""Product catalogue and stock control."""

from __future__ import annotations

import customtkinter as ctk

from app.money import fmt_usd, parse_amount, parse_int
from app.services import products as products_service
from app.services import settings as settings_service
from app.ui import theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    Modal,
    SectionTitle,
    ask_confirm,
    show_error,
    show_info,
)

NO_CATEGORY = "— none —"
ALL_CATEGORIES = "All categories"


class ProductsView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.is_admin = shell.user.is_admin
        self._categories: list = []

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = PageHeader(self, "Products", "Catalogue, pricing and stock levels")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        actions = header.actions
        if self.is_admin:
            ctk.CTkButton(
                actions, text="Categories", height=36, width=110,
                fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
                command=self._manage_categories,
            ).grid(row=0, column=0, padx=(0, 8))
            ctk.CTkButton(
                actions, text="+ Add product", height=36, width=140,
                font=theme.font(13, "bold"), command=self._add_product,
            ).grid(row=0, column=1)

        self._build_toolbar()
        self._build_table()

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.reload())
        ctk.CTkEntry(
            bar, textvariable=self.search_var, height=36,
            placeholder_text="Search products by name, SKU or description…",
        ).grid(row=0, column=0, sticky="ew")

        self.category_var = ctk.StringVar(value=ALL_CATEGORIES)
        self.category_menu = ctk.CTkOptionMenu(
            bar, variable=self.category_var, values=[ALL_CATEGORIES], width=170,
            height=36, command=lambda _value: self.reload(),
        )
        self.category_menu.grid(row=0, column=1, padx=(8, 0))

        self.low_only = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            bar, text="Low stock only", variable=self.low_only,
            command=self.reload, height=36,
        ).grid(row=0, column=2, padx=(12, 0))

        self.show_archived = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            bar, text="Show archived", variable=self.show_archived,
            command=self.reload, height=36,
        ).grid(row=0, column=3, padx=(12, 0))

    def _build_table(self) -> None:
        card = Card(self)
        card.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 12))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        self.table = DataTable(
            card,
            columns=[
                ("name", "Product", 240, "w"),
                ("sku", "SKU", 110, "w"),
                ("category_name", "Category", 140, "w"),
                ("cost_usd", "Cost", 90, "e"),
                ("price_usd", "Price", 90, "e"),
                ("stock_qty", "Stock", 70, "center"),
                ("reorder_level", "Reorder at", 90, "center"),
            ],
            id_key="product_id",
            height=16,
            border_width=0,
            fg_color="transparent",
        )
        self.table.set_formatter("cost_usd", lambda value, _row: fmt_usd(value))
        self.table.set_formatter("price_usd", lambda value, _row: fmt_usd(value))
        self.table.set_formatter(
            "category_name", lambda value, _row: value or "Uncategorised"
        )
        self.table.set_formatter(
            "name",
            lambda value, row: value if row["is_active"] else f"{value}  (archived)",
        )
        self.table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.table.on_double_click(self._edit_product if self.is_admin else self._history)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))

        specs = [("Adjust stock", self._adjust_stock, theme.PRIMARY),
                 ("Stock history", self._history, theme.NEUTRAL)]
        if self.is_admin:
            specs = [("Edit", self._edit_product, theme.NEUTRAL)] + specs
            specs.append(("Delete", self._delete_product, theme.DANGER))

        for column, (label, command, colour) in enumerate(specs):
            ctk.CTkButton(
                buttons, text=label, height=36, width=130, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=(0, 8))

    # ------------------------------------------------------------------ #

    def _category_values(self) -> list[str]:
        return [NO_CATEGORY] + [c["name"] for c in self._categories]

    def _category_id(self, label: str) -> int | None:
        return next(
            (c["category_id"] for c in self._categories if c["name"] == label), None
        )

    def _selected(self):
        product_id = self.table.selected_int()
        if product_id is None:
            show_error(self, "Select a product first.", "Nothing selected")
            return None
        return products_service.get_product(product_id)

    # ------------------------------------------------------------------ #

    def _product_fields(self, product=None) -> list[dict]:
        category_label = NO_CATEGORY
        if product is not None and product["category_name"]:
            category_label = product["category_name"]
        fields = [
            {"key": "name", "label": "Product name",
             "value": product["name"] if product else ""},
            {"key": "sku", "label": "SKU / barcode",
             "value": product["sku"] if product else ""},
            {"key": "category", "label": "Category", "type": "option",
             "values": self._category_values(), "value": category_label},
            {"key": "cost_usd", "label": "Cost price (USD)",
             "value": f"{product['cost_usd']:.2f}" if product else "0.00"},
            {"key": "price_usd", "label": "Selling price (USD)",
             "value": f"{product['price_usd']:.2f}" if product else "0.00"},
            {"key": "reorder_level", "label": "Reorder level",
             "value": product["reorder_level"] if product
             else settings_service.low_stock_default(),
             "hint": "The dashboard flags the product once stock falls to this number."},
            {"key": "description", "label": "Description", "type": "text",
             "value": product["description"] if product else ""},
        ]
        if product is None:
            fields.insert(6, {"key": "stock_qty", "label": "Opening stock", "value": "0"})
        else:
            fields.append({"key": "is_active", "label": "Active (sellable)",
                           "type": "check", "value": bool(product["is_active"])})
        return fields

    def _add_product(self) -> None:
        def submit(values):
            products_service.create_product(
                sku=values["sku"],
                name=values["name"],
                price_usd=parse_amount(values["price_usd"], "selling price"),
                cost_usd=parse_amount(values["cost_usd"], "cost price"),
                category_id=self._category_id(values["category"]),
                description=values["description"],
                stock_qty=parse_int(values["stock_qty"] or 0, "opening stock", minimum=0),
                reorder_level=parse_int(values["reorder_level"], "reorder level", minimum=0),
                user_id=self.shell.user.user_id,
            )

        if FormModal(self, "Add product", self._product_fields(), submit,
                     submit_text="Add product").wait_result():
            self.reload()

    def _edit_product(self) -> None:
        product = self._selected()
        if product is None:
            return

        def submit(values):
            products_service.update_product(
                product["product_id"],
                sku=values["sku"],
                name=values["name"],
                price_usd=parse_amount(values["price_usd"], "selling price"),
                cost_usd=parse_amount(values["cost_usd"], "cost price"),
                category_id=self._category_id(values["category"]),
                description=values["description"],
                reorder_level=parse_int(values["reorder_level"], "reorder level", minimum=0),
                is_active=bool(values["is_active"]),
            )

        if FormModal(self, f"Edit — {product['name']}", self._product_fields(product),
                     submit).wait_result():
            self.reload()

    def _adjust_stock(self) -> None:
        product = self._selected()
        if product is None:
            return

        reasons = ["Restock", "Adjustment", "Spoilage"]
        fields = [
            {"key": "current", "label": "Current stock", "type": "readonly",
             "value": product["stock_qty"]},
            {"key": "change", "label": "Change in units", "value": "",
             "hint": "Use a positive number to add stock, negative to take it out."},
            {"key": "reason", "label": "Reason", "type": "option", "values": reasons},
            {"key": "note", "label": "Note (optional)", "value": ""},
        ]

        def submit(values):
            change = parse_int(values["change"], "change in units")
            products_service.adjust_stock(
                product["product_id"], change, reason=values["reason"],
                user_id=self.shell.user.user_id, note=values["note"],
            )

        if FormModal(self, f"Adjust stock — {product['name']}", fields, submit,
                     submit_text="Apply").wait_result():
            self.reload()

    def _delete_product(self) -> None:
        product = self._selected()
        if product is None:
            return
        if not ask_confirm(
            self, f"Delete '{product['name']}' from the catalogue?", "Delete product"
        ):
            return
        try:
            products_service.delete_product(product["product_id"])
        except products_service.ProductError as exc:
            show_info(self, exc, "Archived instead")
        self.reload()

    def _history(self) -> None:
        product = self._selected()
        if product is None:
            return
        StockHistoryModal(self, product)

    def _manage_categories(self) -> None:
        CategoriesModal(self).wait_window()
        self.refresh()
        self.shell.invalidate("pos")

    # ------------------------------------------------------------------ #

    def reload(self) -> None:
        category_id = None
        if self.category_var.get() != ALL_CATEGORIES:
            category_id = self._category_id(self.category_var.get())
        rows = products_service.list_products(
            search=self.search_var.get(),
            category_id=category_id,
            include_inactive=self.show_archived.get(),
            low_stock_only=self.low_only.get(),
        )
        self.table.set_rows(
            rows,
            tag_func=_product_tag,
            empty_message="No products match these filters.",
        )

    def refresh(self) -> None:
        self._categories = list(products_service.list_categories())
        values = [ALL_CATEGORIES] + [c["name"] for c in self._categories]
        self.category_menu.configure(values=values)
        if self.category_var.get() not in values:
            self.category_var.set(ALL_CATEGORIES)
        self.reload()


def _product_tag(row):
    if not row["is_active"]:
        return "muted"
    if row["stock_qty"] <= 0:
        return "danger"
    if row["stock_qty"] <= row["reorder_level"]:
        return "warning"
    return ()


class StockHistoryModal(Modal):
    def __init__(self, parent, product):
        super().__init__(parent, f"Stock history — {product['name']}", 640, 460)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        SectionTitle(self, f"{product['name']}  ·  now {product['stock_qty']} in stock").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 8)
        )
        table = DataTable(
            self,
            columns=[
                ("log_time", "When", 140, "w"),
                ("reason", "Reason", 110, "w"),
                ("change_qty", "Change", 80, "center"),
                ("new_stock", "Balance", 80, "center"),
                ("username", "By", 90, "w"),
                ("note", "Note", 120, "w"),
            ],
            id_key="log_id",
            height=12,
        )
        table.set_formatter(
            "change_qty", lambda value, _row: f"+{value}" if value > 0 else str(value)
        )
        table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        table.set_rows(
            products_service.stock_history(product["product_id"]),
            tag_func=lambda row: "success" if row["change_qty"] > 0 else "danger",
            empty_message="No stock movements recorded.",
        )
        ctk.CTkButton(self, text="Close", height=36, command=self.on_cancel).grid(
            row=2, column=0, pady=(0, 16)
        )


class CategoriesModal(Modal):
    def __init__(self, parent):
        super().__init__(parent, "Categories", 460, 460)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        SectionTitle(self, "Product categories").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 8)
        )
        self.table = DataTable(
            self,
            columns=[("name", "Name", 340, "w")],
            id_key="category_id",
            height=10,
        )
        self.table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        for column, (label, command, colour) in enumerate((
            ("Add", self._add, theme.PRIMARY),
            ("Rename", self._rename, theme.NEUTRAL),
            ("Delete", self._delete, theme.DANGER),
            ("Close", self.on_cancel, theme.NEUTRAL),
        )):
            buttons.grid_columnconfigure(column, weight=1)
            ctk.CTkButton(
                buttons, text=label, height=36, command=command, fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=3, sticky="ew")

        self.reload()

    def reload(self) -> None:
        self.table.set_rows(
            products_service.list_categories(), empty_message="No categories yet."
        )

    def _add(self) -> None:
        dialog = FormModal(
            self, "Add category", [{"key": "name", "label": "Category name"}],
            lambda values: products_service.create_category(values["name"]),
            submit_text="Add",
        )
        if dialog.wait_result():
            self.reload()

    def _rename(self) -> None:
        category_id = self.table.selected_int()
        if category_id is None:
            return
        current = next(
            (c["name"] for c in products_service.list_categories()
             if c["category_id"] == category_id), ""
        )
        dialog = FormModal(
            self, "Rename category",
            [{"key": "name", "label": "Category name", "value": current}],
            lambda values: products_service.rename_category(category_id, values["name"]),
        )
        if dialog.wait_result():
            self.reload()

    def _delete(self) -> None:
        category_id = self.table.selected_int()
        if category_id is None:
            return
        if ask_confirm(
            self,
            "Delete this category? Its products are kept and become uncategorised.",
            "Delete category",
        ):
            products_service.delete_category(category_id)
            self.reload()
