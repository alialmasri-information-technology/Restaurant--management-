"""Product catalogue and stock control."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app import config, labels
from app.money import fmt_usd, parse_amount, parse_int
from app.services import catalog_io
from app.services import products as products_service
from app.services import settings as settings_service
from app.services import suppliers as suppliers_service
from app.ui import theme
from app.ui.receipt_actions import open_file
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
NO_SUPPLIER = NO_CATEGORY
ALL_CATEGORIES = "All categories"


class ProductsView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.is_admin = shell.user.is_admin
        self._categories: list = []
        self._suppliers: list = []

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
                actions, text="Import / export", height=36, width=140,
                fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
                command=self._catalogue_tools,
            ).grid(row=0, column=1, padx=(0, 8))
            ctk.CTkButton(
                actions, text="+ Add product", height=36, width=140,
                font=theme.font(13, "bold"), command=self._add_product,
            ).grid(row=0, column=2)

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
                ("sku", "SKU", 100, "w"),
                ("barcode", "Barcode", 120, "w"),
                ("category_name", "Category", 130, "w"),
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
                 ("Stock history", self._history, theme.NEUTRAL),
                 ("Print labels", self._print_labels, theme.NEUTRAL)]
        if self.is_admin:
            specs = [("Edit", self._edit_product, theme.NEUTRAL)] + specs
            specs.append(("Image", self._manage_image, theme.NEUTRAL))
            specs.append(("Delete", self._delete_product, theme.DANGER))

        for column, (label, command, colour) in enumerate(specs):
            ctk.CTkButton(
                buttons, text=label, height=36, width=116, command=command,
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

    def _supplier_values(self) -> list[str]:
        return [NO_SUPPLIER] + [row["name"] for row in self._suppliers]

    def _supplier_id(self, label: str) -> int | None:
        return next(
            (row["supplier_id"] for row in self._suppliers if row["name"] == label), None
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
        supplier_label = NO_SUPPLIER
        if product is not None and product["supplier_name"]:
            supplier_label = product["supplier_name"]
        fields = [
            {"key": "name", "label": "Product name",
             "value": product["name"] if product else ""},
            {"key": "sku", "label": "SKU",
             "value": product["sku"] if product else "",
             "hint": "Your own code for the product. Must be unique."},
            {"key": "barcode", "label": "Barcode (optional)",
             "value": product["barcode"] if product else "",
             "hint": "Scanned at the till. Left blank, the SKU is scanned instead."},
            {"key": "category", "label": "Category", "type": "option",
             "values": self._category_values(), "value": category_label},
            {"key": "supplier", "label": "Supplier", "type": "option",
             "values": self._supplier_values(), "value": supplier_label},
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
            fields.insert(8, {"key": "stock_qty", "label": "Opening stock", "value": "0"})
        else:
            fields.append({"key": "is_active", "label": "Active (sellable)",
                           "type": "check", "value": bool(product["is_active"])})
        return fields

    def _add_product(self) -> None:
        def submit(values):
            products_service.create_product(
                sku=values["sku"],
                barcode=values["barcode"],
                name=values["name"],
                price_usd=parse_amount(values["price_usd"], "selling price"),
                cost_usd=parse_amount(values["cost_usd"], "cost price"),
                category_id=self._category_id(values["category"]),
                supplier_id=self._supplier_id(values["supplier"]),
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
                barcode=values["barcode"],
                name=values["name"],
                price_usd=parse_amount(values["price_usd"], "selling price"),
                cost_usd=parse_amount(values["cost_usd"], "cost price"),
                category_id=self._category_id(values["category"]),
                supplier_id=self._supplier_id(values["supplier"]),
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

    def _print_labels(self) -> None:
        product = self._selected()
        if product is None:
            return
        LabelModal(self, product)

    def _manage_image(self) -> None:
        product = self._selected()
        if product is None:
            return

        current = products_service.image_file(product["image_path"])
        if current is not None and not ask_confirm(
            self,
            f"{product['name']} already has an image.\n\nReplace it?\n\n"
            f"Choose No to remove the picture instead.",
            "Product image",
        ):
            products_service.clear_image(product["product_id"])
            self.reload()
            self.shell.invalidate("pos")
            return

        chosen = filedialog.askopenfilename(
            parent=self,
            title=f"Choose a picture for {product['name']}",
            filetypes=[
                ("Images", " ".join(f"*{ext}" for ext in config.IMAGE_EXTENSIONS)),
                ("All files", "*.*"),
            ],
        )
        if not chosen:
            return
        try:
            products_service.set_image(product["product_id"], chosen)
        except products_service.ProductError as exc:
            show_error(self, exc, "Could not attach the image")
            return
        self.reload()
        self.shell.invalidate("pos")

    def _catalogue_tools(self) -> None:
        CatalogueModal(self, self.shell).wait_window()
        self.refresh()
        self.shell.invalidate("pos", "dashboard")

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
        self._suppliers = list(suppliers_service.list_suppliers())
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


class LabelModal(Modal):
    """Print shelf or shelf-edge labels for one product."""

    def __init__(self, parent, product):
        super().__init__(parent, f"Labels — {product['name']}", 520, 430)
        self.product = product
        self.grid_columnconfigure(0, weight=1)

        code = labels.code_for(product)
        SectionTitle(self, product["name"]).grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 2)
        )
        ctk.CTkLabel(
            self,
            text=(
                f"Will print {code}"
                + ("" if product["barcode"] else "  (the SKU, as there is no barcode)")
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=2, column=0, sticky="ew", padx=18)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            form, text="How many", font=theme.font(12), text_color=theme.TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=6)
        self.count = ctk.CTkEntry(form, width=100, height=34)
        self.count.insert(0, str(max(product["stock_qty"], 1)))
        self.count.grid(row=0, column=1, sticky="w", pady=6)

        ctk.CTkLabel(
            form, text="Label size", font=theme.font(12), text_color=theme.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=6)
        self._titles = {title: key for key, title in labels.sheet_choices()}
        self.sheet = ctk.CTkOptionMenu(
            form, values=list(self._titles), width=300, height=34
        )
        self.sheet.grid(row=1, column=1, sticky="ew", pady=6)

        self.show_price = ctk.CTkCheckBox(self, text="Print the price")
        self.show_price.select()
        self.show_price.grid(row=3, column=0, sticky="w", padx=18, pady=(12, 0))

        self.show_lbp = ctk.CTkCheckBox(self, text="Show the price in LBP as well")
        self.show_lbp.grid(row=4, column=0, sticky="w", padx=18, pady=(6, 0))

        self.show_store = ctk.CTkCheckBox(self, text="Print the shop name")
        self.show_store.grid(row=5, column=0, sticky="w", padx=18, pady=(6, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", padx=18, pady=(18, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer, text="Create labels", width=150, height=36, command=self.submit
        ).grid(row=0, column=2)

    def submit(self) -> None:
        try:
            count = parse_int(self.count.get(), "number of labels", minimum=1)
        except ValueError as exc:
            show_error(self, exc, "Check the quantity")
            return

        try:
            path = labels.generate_labels(
                {self.product["product_id"]: count},
                sheet=self._titles[self.sheet.get()],
                show_price=bool(self.show_price.get()),
                show_lbp=bool(self.show_lbp.get()),
                show_store=bool(self.show_store.get()),
            )
        except labels.LabelError as exc:
            show_error(self, exc, "Could not create the labels")
            return

        self.result = path
        self.grab_release()
        self.destroy()
        open_file(path)


class CatalogueModal(Modal):
    """Import a spreadsheet of products, or export the catalogue.

    The import is deliberately two-step: the file is analysed and the outcome
    of every row shown before anything is written.
    """

    def __init__(self, parent, shell):
        super().__init__(parent, "Import and export", 720, 540)
        self.shell = shell
        self.plan = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        SectionTitle(self, "Product catalogue").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 2)
        )
        ctk.CTkLabel(
            self,
            text=(
                "A CSV needs at least sku and name. Existing SKUs are updated, "
                "new ones are added, and stock is set to the number in the file."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left", wraplength=660,
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 10))
        for column, (label, command, colour) in enumerate((
            ("Choose a file…", self.choose_file, theme.PRIMARY),
            ("Export catalogue", self.export, theme.NEUTRAL),
            ("Save a template", self.template, theme.NEUTRAL),
        )):
            buttons.grid_columnconfigure(column, weight=1)
            ctk.CTkButton(
                buttons, text=label, height=36, command=command, fg_color=colour,
                hover_color=(
                    theme.PRIMARY_HOVER if colour == theme.PRIMARY else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=3, sticky="ew")

        self.preview = DataTable(
            self,
            columns=[
                ("line_no", "Line", 60, "center"),
                ("action", "Action", 90, "w"),
                ("sku", "SKU", 110, "w"),
                ("name", "Product", 200, "w"),
                ("message", "What happens", 220, "w"),
            ],
            id_key="line_no",
            height=10,
        )
        self.preview.grid(row=3, column=0, sticky="nsew", padx=18, pady=(0, 10))

        self.summary = ctk.CTkLabel(
            self, text="No file chosen yet.", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.summary.grid(row=4, column=0, sticky="ew", padx=18)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=18, pady=(10, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Close", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        self.import_button = ctk.CTkButton(
            footer, text="Import", width=150, height=36, command=self.run_import,
            state="disabled",
        )
        self.import_button.grid(row=0, column=2)

    # ------------------------------------------------------------------ #

    def choose_file(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self, title="Choose a product CSV",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            self.plan = catalog_io.analyse(chosen)
        except catalog_io.ImportError_ as exc:
            self.plan = None
            self.import_button.configure(state="disabled")
            self.preview.set_rows([])
            show_error(self, exc, "Could not read the file")
            return

        self.preview.set_rows(
            [
                {
                    "line_no": row.line_no, "action": row.action, "sku": row.sku,
                    "name": row.name, "message": row.message,
                }
                for row in self.plan.rows
            ],
            tag_func=lambda row: {
                "error": "danger", "create": "success", "update": "warning",
            }.get(row["action"], ()),
            empty_message="The file has no rows.",
        )

        note = self.plan.summary()
        if self.plan.unknown_columns:
            note += f"  ·  ignored columns: {', '.join(self.plan.unknown_columns)}"
        self.summary.configure(text=f"{Path(chosen).name}  —  {note}")
        self.import_button.configure(
            state="normal" if self.plan.applicable else "disabled"
        )

    def run_import(self) -> None:
        if self.plan is None:
            return
        errors = len(self.plan.errors)
        if not ask_confirm(
            self,
            f"Import {self.plan.summary()}?"
            + (f"\n\n{errors} unusable row(s) will be skipped." if errors else "")
            + "\n\nNothing is written unless every applicable row succeeds.",
            "Import catalogue",
        ):
            return
        try:
            result = catalog_io.apply(self.plan, self.shell.user.user_id)
        except Exception as exc:  # noqa: BLE001 - the import rolled back
            show_error(
                self, f"{exc}\n\nNothing was imported.", "Import failed"
            )
            return

        show_info(
            self,
            f"{result['created']} product(s) added, {result['updated']} updated, "
            f"{result['stock_moved']} unit(s) of stock corrected.",
            "Import complete",
        )
        self.result = result
        self.grab_release()
        self.destroy()

    def export(self) -> None:
        target = filedialog.asksaveasfilename(
            parent=self, title="Export the catalogue", defaultextension=".csv",
            initialfile="products.csv", filetypes=[("CSV file", "*.csv")],
        )
        if not target:
            return
        try:
            count = catalog_io.export_products(target)
        except catalog_io.ImportError_ as exc:
            show_error(self, exc, "Could not export")
            return
        show_info(self, f"{count} product(s) written to:\n{target}", "Catalogue exported")

    def template(self) -> None:
        target = filedialog.asksaveasfilename(
            parent=self, title="Save an example file", defaultextension=".csv",
            initialfile="product-template.csv", filetypes=[("CSV file", "*.csv")],
        )
        if not target:
            return
        catalog_io.write_template(target)
        show_info(self, f"Example saved to:\n{target}", "Template saved")
