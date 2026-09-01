"""Point of sale: build a cart and turn it into an invoice."""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import D, ZERO, fmt_lbp, fmt_usd, parse_amount, to_lbp, usd
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.ui import theme
from app.ui.receipt_actions import offer_receipt
from app.ui.shell import PageHeader
from app.ui.widgets import Card, DataTable, SectionTitle, ask_confirm, show_error

WALK_IN = "Walk-in customer"


class PosView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.cart = sales_service.Cart()
        self._customers: list = []
        self._categories: list = []

        self.grid_columnconfigure(0, weight=3, uniform="pos")
        self.grid_columnconfigure(1, weight=2, uniform="pos")
        self.grid_rowconfigure(1, weight=1)

        header = PageHeader(self, "New Sale", "Search a product, then press Enter or double-click to add it")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(20, 14))

        self._build_catalogue()
        self._build_cart()

    # ------------------------------------------------------------------ #
    # Catalogue side
    # ------------------------------------------------------------------ #

    def _build_catalogue(self) -> None:
        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=(24, 12), pady=(0, 24))
        card.grid_rowconfigure(2, weight=1)
        card.grid_columnconfigure(0, weight=1)

        SectionTitle(card, "Products").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 8)
        )

        controls = ctk.CTkFrame(card, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        controls.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._reload_products())
        self.search_entry = ctk.CTkEntry(
            controls, textvariable=self.search_var, height=36,
            placeholder_text="Search by name or scan a SKU…",
        )
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.bind("<Return>", lambda _event: self._add_from_search())

        self.category_var = ctk.StringVar(value="All categories")
        self.category_menu = ctk.CTkOptionMenu(
            controls, variable=self.category_var, values=["All categories"],
            width=170, height=36, command=lambda _value: self._reload_products(),
        )
        self.category_menu.grid(row=0, column=1, padx=(8, 0))

        self.products = DataTable(
            card,
            columns=[
                ("name", "Product", 220, "w"),
                ("sku", "SKU", 110, "w"),
                ("price_usd", "Price", 90, "e"),
                ("stock_qty", "Stock", 70, "center"),
            ],
            id_key="product_id",
            height=16,
            border_width=0,
            fg_color="transparent",
        )
        self.products.set_formatter("price_usd", lambda value, _row: fmt_usd(value))
        self.products.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.products.on_double_click(self._add_selected)
        self.products.on_return(self._add_selected)

    def _reload_products(self) -> None:
        category_id = None
        label = self.category_var.get()
        if label != "All categories":
            category_id = next(
                (c["category_id"] for c in self._categories if c["name"] == label), None
            )
        rows = products_service.list_products(
            search=self.search_var.get(), category_id=category_id, in_stock_only=False
        )
        self.products.set_rows(
            rows,
            tag_func=lambda row: "muted" if row["stock_qty"] <= 0 else (),
            empty_message="No products match this search.",
        )

    def _add_from_search(self) -> None:
        """Enter adds an exact SKU match (barcode scanners type + press Enter)."""
        term = self.search_var.get().strip()
        if term:
            product = products_service.get_by_sku(term)
            if product is not None:
                self._add_product(product)
                self.search_var.set("")
                return
        self._add_selected()

    def _add_selected(self) -> None:
        product_id = self.products.selected_int()
        if product_id is None:
            return
        product = products_service.get_product(product_id)
        if product is not None:
            self._add_product(product)

    def _add_product(self, product) -> None:
        if product["stock_qty"] <= 0:
            show_error(self, f"{product['name']} is out of stock.", "Out of stock")
            return
        try:
            self.cart.add_product(product, 1)
        except sales_service.SaleError as exc:
            show_error(self, exc, "Cannot add item")
            return
        self._render_cart()

    # ------------------------------------------------------------------ #
    # Cart side
    # ------------------------------------------------------------------ #

    def _build_cart(self) -> None:
        card = Card(self)
        card.grid(row=1, column=1, sticky="nsew", padx=(0, 24), pady=(0, 24))
        card.grid_rowconfigure(1, weight=1, minsize=90)
        card.grid_columnconfigure(0, weight=1)

        self.cart_title = SectionTitle(card, "Cart")
        self.cart_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))

        self.cart_table = DataTable(
            card,
            columns=[
                ("name", "Item", 140, "w"),
                ("qty", "Qty", 45, "center"),
                ("unit_price", "Unit", 68, "e"),
                ("line_total", "Total", 78, "e"),
            ],
            id_key="product_id",
            height=8,
            border_width=0,
            fg_color="transparent",
        )
        self.cart_table.set_formatter("unit_price", lambda value, _row: fmt_usd(value))
        self.cart_table.set_formatter("line_total", lambda value, _row: fmt_usd(value))
        self.cart_table.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 6))

        line_actions = ctk.CTkFrame(card, fg_color="transparent")
        line_actions.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        for column in range(4):
            line_actions.grid_columnconfigure(column, weight=1)
        for column, (label, command, colour) in enumerate((
            ("−", lambda: self._bump(-1), theme.NEUTRAL),
            ("+", lambda: self._bump(1), theme.NEUTRAL),
            ("Set qty", self._prompt_qty, theme.NEUTRAL),
            ("Remove", self._remove_line, theme.DANGER),
        )):
            ctk.CTkButton(
                line_actions, text=label, height=30, command=command,
                fg_color=colour,
                hover_color=theme.DANGER_HOVER if colour == theme.DANGER else theme.NEUTRAL_HOVER,
            ).grid(row=0, column=column, padx=2, sticky="ew")

        # Two columns rather than four stacked rows: the cart table needs the height.
        form = ctk.CTkFrame(card, fg_color="transparent")
        form.grid(row=3, column=0, sticky="ew", padx=12)
        form.grid_columnconfigure((0, 1), weight=1, uniform="field")

        customer_cell = self._field(form, 0, 0, "Customer")
        self.customer_var = ctk.StringVar(value=WALK_IN)
        self.customer_menu = ctk.CTkOptionMenu(
            customer_cell, variable=self.customer_var, values=[WALK_IN], height=32
        )
        self.customer_menu.grid(row=1, column=0, sticky="ew")

        payment_cell = self._field(form, 0, 1, "Payment")
        self.method_var = ctk.StringVar(value=config.PAYMENT_METHODS[0])
        ctk.CTkOptionMenu(
            payment_cell, variable=self.method_var, values=list(config.PAYMENT_METHODS),
            height=32, command=lambda _value: self._on_method_change(),
        ).grid(row=1, column=0, sticky="ew")

        discount_cell = self._field(form, 1, 0, "Discount (USD)")
        self.discount_var = ctk.StringVar(value="0")
        self.discount_var.trace_add("write", lambda *_: self._render_totals())
        ctk.CTkEntry(
            discount_cell, textvariable=self.discount_var, height=32
        ).grid(row=1, column=0, sticky="ew")

        paid_cell = self._field(form, 1, 1, "Amount received")
        pay_row = ctk.CTkFrame(paid_cell, fg_color="transparent")
        pay_row.grid(row=1, column=0, sticky="ew")
        pay_row.grid_columnconfigure(0, weight=1)
        self.paid_var = ctk.StringVar(value="")
        self.paid_var.trace_add("write", lambda *_: self._render_totals())
        self.paid_entry = ctk.CTkEntry(
            pay_row, textvariable=self.paid_var, height=32, placeholder_text="0.00"
        )
        self.paid_entry.grid(row=0, column=0, sticky="ew")
        self.currency_var = ctk.StringVar(value="USD")
        ctk.CTkSegmentedButton(
            pay_row, values=list(config.CURRENCIES), variable=self.currency_var,
            command=lambda _value: self._render_totals(), width=104, height=32,
        ).grid(row=0, column=1, padx=(6, 0))

        totals = Card(card, fg_color=theme.SURFACE_ALT, border_width=0)
        totals.grid(row=4, column=0, sticky="ew", padx=12, pady=(6, 0))
        totals.grid_columnconfigure(1, weight=1)

        self.total_labels = {}
        # (key, label, label size, value size, weight, value colour)
        rows = (
            ("subtotal", "Subtotal", 11, 11, "normal", theme.TEXT_MUTED),
            ("discount", "Discount", 11, 11, "normal", theme.TEXT_MUTED),
            ("tax", "Tax", 11, 11, "normal", theme.TEXT_MUTED),
            ("total", "TOTAL", 13, 20, "bold", theme.TEXT),
            ("lbp", "In LBP", 11, 13, "bold", theme.TEXT_MUTED),
            ("change", "Change", 11, 13, "bold", theme.SUCCESS),
        )
        for index, (key, label, label_size, value_size, weight, colour) in enumerate(rows):
            top = 8 if index == 0 else 1
            bottom = 8 if index == len(rows) - 1 else 0
            ctk.CTkLabel(
                totals, text=label, font=theme.font(label_size, weight),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).grid(row=index, column=0, sticky="w", padx=(14, 0), pady=(top, bottom))
            value_label = ctk.CTkLabel(
                totals, text="—", font=theme.font(value_size, weight),
                text_color=colour, anchor="e",
            )
            value_label.grid(
                row=index, column=1, sticky="e", padx=(0, 14), pady=(top, bottom)
            )
            self.total_labels[key] = value_label

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=5, column=0, sticky="ew", padx=12, pady=(10, 12))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=2)

        ctk.CTkButton(
            buttons, text="Clear", height=42, command=self._clear_cart,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.complete_button = ctk.CTkButton(
            buttons, text="Complete Sale", height=42, font=theme.font(15, "bold"),
            fg_color=theme.SUCCESS, hover_color=theme.SUCCESS_HOVER,
            command=self._complete_sale,
        )
        self.complete_button.grid(row=0, column=1, sticky="ew")

    @staticmethod
    def _field(parent, row: int, column: int, label: str) -> ctk.CTkFrame:
        """A labelled form cell; the caller grids its widget at row 1, column 0."""
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(
            row=row, column=column, sticky="ew",
            padx=(0, 6) if column == 0 else (6, 0), pady=(0, 6),
        )
        cell.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cell, text=label, font=theme.font(11),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 2))
        return cell

    # ------------------------------------------------------------------ #
    # Cart operations
    # ------------------------------------------------------------------ #

    def _selected_line(self):
        product_id = self.cart_table.selected_int()
        return self.cart.find(product_id) if product_id is not None else None

    def _bump(self, delta: int) -> None:
        line = self._selected_line()
        if line is None:
            return
        try:
            self.cart.set_qty(line.product_id, line.qty + delta)
        except sales_service.SaleError as exc:
            show_error(self, exc, "Not enough stock")
        self._render_cart()

    def _prompt_qty(self) -> None:
        from app.ui.widgets import FormModal

        line = self._selected_line()
        if line is None:
            return
        dialog = FormModal(
            self,
            f"Quantity — {line.name}",
            [{
                "key": "qty",
                "label": f"Quantity (max {line.stock_available})",
                "value": line.qty,
            }],
            submit_text="Update",
        )
        values = dialog.wait_result()
        if not values:
            return
        try:
            self.cart.set_qty(line.product_id, int(values["qty"]))
        except ValueError:
            show_error(self, "Quantity must be a whole number.", "Invalid quantity")
        except sales_service.SaleError as exc:
            show_error(self, exc, "Not enough stock")
        self._render_cart()

    def _remove_line(self) -> None:
        product_id = self.cart_table.selected_int()
        if product_id is not None:
            self.cart.remove(product_id)
            self._render_cart()

    def _clear_cart(self) -> None:
        if self.cart.is_empty or ask_confirm(self, "Empty the cart?", "Clear sale"):
            self.cart.clear()
            self.discount_var.set("0")
            self.paid_var.set("")
            self._render_cart()

    def _on_method_change(self) -> None:
        """Non-cash payments settle the exact amount, so prefill it."""
        if self.method_var.get() != "Cash" and not self.cart.is_empty:
            _sub, _disc, _tax, total = self._totals()
            if self.currency_var.get() == "LBP":
                rate = settings_service.exchange_rate()
                self.paid_var.set(str(to_lbp(total, rate, settings_service.lbp_rounding())))
            else:
                self.paid_var.set(f"{total:.2f}")
        self._render_totals()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _totals(self):
        try:
            self.cart.discount = parse_amount(self.discount_var.get(), "discount")
        except ValueError:
            self.cart.discount = ZERO
        return self.cart.totals()

    def _render_cart(self) -> None:
        rows = [
            {
                "product_id": line.product_id,
                "name": line.name,
                "qty": line.qty,
                "unit_price": line.unit_price,
                "line_total": line.line_total,
            }
            for line in self.cart.lines
        ]
        self.cart_table.set_rows(rows, empty_message="Cart is empty.")
        self.cart_title.configure(
            text=f"Cart · {self.cart.item_count} item{'s' if self.cart.item_count != 1 else ''}"
        )
        self._render_totals()

    def _render_totals(self) -> None:
        subtotal, discount, tax, total = self._totals()
        rate = settings_service.exchange_rate()
        rounding = settings_service.lbp_rounding()

        self.total_labels["subtotal"].configure(text=fmt_usd(subtotal))
        self.total_labels["discount"].configure(
            text=f"-{fmt_usd(discount)}" if discount else fmt_usd(0)
        )
        self.total_labels["tax"].configure(text=fmt_usd(tax))
        self.total_labels["total"].configure(text=fmt_usd(total))
        self.total_labels["lbp"].configure(text=fmt_lbp(to_lbp(total, rate, rounding)))

        try:
            paid = parse_amount(self.paid_var.get(), "amount received")
        except ValueError:
            paid = ZERO
        paid_usd = usd(paid / rate) if self.currency_var.get() == "LBP" and rate else usd(paid)
        difference = paid_usd - total
        if paid <= ZERO:
            self.total_labels["change"].configure(text="—", text_color=theme.TEXT_MUTED)
        elif difference >= ZERO:
            self.total_labels["change"].configure(
                text=fmt_usd(difference), text_color=theme.SUCCESS
            )
        else:
            self.total_labels["change"].configure(
                text=f"short {fmt_usd(-difference)}", text_color=theme.DANGER
            )

    # ------------------------------------------------------------------ #
    # Checkout
    # ------------------------------------------------------------------ #

    def _selected_customer_id(self) -> int | None:
        label = self.customer_var.get()
        if label == WALK_IN:
            return None
        return next(
            (c["customer_id"] for c in self._customers if _customer_label(c) == label),
            None,
        )

    def _complete_sale(self) -> None:
        if self.cart.is_empty:
            show_error(self, "Add at least one product first.", "Nothing to sell")
            return
        try:
            paid = parse_amount(self.paid_var.get(), "amount received")
        except ValueError as exc:
            show_error(self, exc, "Invalid amount")
            return

        try:
            sale_id = sales_service.create_sale(
                user_id=self.shell.user.user_id,
                cart=self.cart,
                customer_id=self._selected_customer_id(),
                payment_method=self.method_var.get(),
                paid_currency=self.currency_var.get(),
                amount_paid=paid,
            )
        except sales_service.SaleError as exc:
            show_error(self, exc, "Sale not completed")
            return
        except Exception as exc:  # noqa: BLE001 - never lose the cart on a surprise
            show_error(self, exc, "Sale not completed")
            return

        sale = sales_service.get_sale(sale_id)
        self.cart.clear()
        self.discount_var.set("0")
        self.paid_var.set("")
        self.search_var.set("")
        self._render_cart()
        self._reload_products()

        change = D(sale["change_usd"])
        message = f"Invoice {sale['invoice_no']} — {fmt_usd(sale['total_usd'])}"
        if change > ZERO:
            message += f"\nChange due: {fmt_usd(change)}"
        offer_receipt(self, sale_id, message)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._categories = list(products_service.list_categories())
        self.category_menu.configure(
            values=["All categories"] + [c["name"] for c in self._categories]
        )
        if self.category_var.get() not in ["All categories"] + [
            c["name"] for c in self._categories
        ]:
            self.category_var.set("All categories")

        self._customers = list(customers_service.list_customers())
        self.customer_menu.configure(
            values=[WALK_IN] + [_customer_label(c) for c in self._customers]
        )

        self._reload_products()
        self._render_cart()
        self.after(100, self.search_entry.focus_set)


def _customer_label(customer) -> str:
    phone = customer["phone"]
    return f"{customer['name']} ({phone})" if phone else customer["name"]
