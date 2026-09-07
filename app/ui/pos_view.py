"""Point of sale: build a cart and turn it into an invoice."""

from __future__ import annotations

from decimal import Decimal

import customtkinter as ctk

from app import config, logs
from app.money import ZERO, D, fmt_lbp, fmt_usd, parse_amount, to_lbp, usd
from app.services import customers as customers_service
from app.services import giftcards as giftcards_service
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service
from app.ui import theme
from app.ui.receipt_actions import offer_receipt
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    Modal,
    SectionTitle,
    ask_confirm,
    debounce,
    show_error,
    show_info,
)

WALK_IN = "Walk-in customer"


class PosView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.cart = sales_service.Cart()
        self._customers: list = []
        self._categories: list = []
        self._thumbnails: dict = {}

        self.grid_columnconfigure(0, weight=3, uniform="pos")
        self.grid_columnconfigure(1, weight=2, uniform="pos")
        self.grid_rowconfigure(1, weight=1)

        self.header = PageHeader(
            self, "New Sale",
            "Scan or search, then press Enter. F2 parks the sale, F4 completes it.",
        )
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(20, 14))

        ctk.CTkButton(
            self.header.actions, text="Park sale", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._park_sale,
        ).grid(row=0, column=0, padx=(0, 8))
        self.parked_button = ctk.CTkButton(
            self.header.actions, text="Parked", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._open_parked,
        )
        self.parked_button.grid(row=0, column=1)
        ctk.CTkButton(
            self.header.actions, text="Layaway", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._hold_layaway,
        ).grid(row=0, column=2, padx=(8, 0))
        ctk.CTkButton(
            self.header.actions, text="Gift card", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._sell_gift_card,
        ).grid(row=0, column=3, padx=(8, 0))
        ctk.CTkButton(
            self.header.actions, text="Keys", width=80, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=lambda: KeysModal(self),
        ).grid(row=0, column=4, padx=(8, 0))

        self._build_catalogue()
        self._build_cart()
        self._bind_shortcuts()

    def _bind_shortcuts(self) -> None:
        """Till work is keyboard work; the mouse should be optional."""
        root = self.winfo_toplevel()
        for sequence, handler in (
            ("<F2>", self._park_sale),
            ("<F3>", self._open_parked),
            ("<F4>", self._complete_sale),
            ("<F6>", self._prompt_qty),
            ("<F7>", self._prompt_line_discount),
            ("<F8>", self._prompt_price),
            ("<Control-l>", lambda: self.search_entry.focus_set()),
        ):
            root.bind(sequence, lambda _event, run=handler: self._shortcut(run))

    def _shortcut(self, handler):
        """Only act when this screen is the one on show."""
        if self.winfo_ismapped():
            handler()
        return "break"

    # ------------------------------------------------------------------ #
    # Catalogue side
    # ------------------------------------------------------------------ #

    def _build_catalogue(self) -> None:
        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=(24, 12), pady=(0, 24))
        card.grid_rowconfigure(2, weight=1)
        card.grid_rowconfigure(3, minsize=64)
        card.grid_columnconfigure(0, weight=1)

        SectionTitle(card, "Products").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 8)
        )

        controls = ctk.CTkFrame(card, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        controls.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", debounce(self, 250, self._reload_products))
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
        self.products.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 6))
        self.products.on_double_click(self._add_selected)
        self.products.on_return(self._add_selected)
        self.products.on_select(self._show_preview)

        preview = ctk.CTkFrame(card, fg_color="transparent", height=64)
        preview.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        preview.grid_columnconfigure(1, weight=1)
        preview.grid_propagate(False)

        self.preview_image = ctk.CTkLabel(preview, text="", width=56, height=56)
        self.preview_image.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))
        self.preview_name = ctk.CTkLabel(
            preview, text="", font=theme.font(13, "bold"),
            text_color=theme.TEXT, anchor="w",
        )
        self.preview_name.grid(row=0, column=1, sticky="ew")
        self.preview_detail = ctk.CTkLabel(
            preview, text="Select a product to see its details.", font=theme.font(11),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.preview_detail.grid(row=1, column=1, sticky="ew")

    def _show_preview(self) -> None:
        product_id = self.products.selected_int()
        product = products_service.get_product(product_id) if product_id else None
        if product is None:
            self.preview_name.configure(text="")
            self.preview_detail.configure(text="Select a product to see its details.")
            self.preview_image.configure(image=None, text="")
            return

        self.preview_name.configure(text=product["name"])
        detail = f"{fmt_usd(product['price_usd'])}  ·  {product['stock_qty']} in stock"
        if product["barcode"]:
            detail += f"  ·  {product['barcode']}"
        self.preview_detail.configure(text=detail)
        self.preview_image.configure(image=self._thumbnail(product), text="")

    def _thumbnail(self, product):
        """A small picture of the product, or nothing if it has none.

        Pillow arrives with CustomTkinter, but a corrupt or exotic file must not
        take the till down, so any failure just leaves the slot empty.
        """
        path = products_service.image_file(product["image_path"])
        if path is None:
            return None
        cached = self._thumbnails.get(str(path))
        if cached is not None:
            return cached
        try:
            from PIL import Image

            image = Image.open(path)
            thumbnail = ctk.CTkImage(light_image=image, dark_image=image, size=(56, 56))
        except Exception:  # noqa: BLE001 - a bad image is not worth an error dialog
            # Quiet on screen, not quiet in the log: a missing thumbnail is a
            # support question, and the answer is in the reason it failed.
            logs.warning("Could not load the product image %s", path, exc_info=True)
            return None
        self._thumbnails[str(path)] = thumbnail
        return thumbnail

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
            empty_message="Nothing matches. Try part of the name, the SKU, or scan it.",
        )

    def _add_from_search(self) -> None:
        """Enter adds an exact code match (a scanner types, then presses Enter)."""
        term = self.search_var.get().strip()
        if term:
            product = products_service.get_by_code(term)
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
            show_error(
                self,
                f"There is none of {product['name']} left. Adjust the stock under "
                "Products if the shelf says otherwise.",
                "Out of stock",
            )
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
        self.cart_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 6))

        self.cart_table = DataTable(
            card,
            columns=[
                ("name", "Item", 130, "w"),
                ("qty", "Qty", 42, "center"),
                ("unit_price", "Unit", 64, "e"),
                ("discount", "Off", 52, "e"),
                ("line_total", "Total", 72, "e"),
            ],
            id_key="product_id",
            height=8,
            border_width=0,
            fg_color="transparent",
        )
        self.cart_table.set_formatter("unit_price", lambda value, _row: fmt_usd(value))
        self.cart_table.set_formatter("line_total", lambda value, _row: fmt_usd(value))
        self.cart_table.set_formatter(
            "discount", lambda value, _row: f"-{fmt_usd(value)}" if value else ""
        )
        self.cart_table.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 6))

        line_actions = ctk.CTkFrame(card, fg_color="transparent")
        line_actions.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
        for column in range(4):
            line_actions.grid_columnconfigure(column, weight=1)
        specs = [
            ("-", lambda: self._bump(-1), theme.NEUTRAL, 0),
            ("+", lambda: self._bump(1), theme.NEUTRAL, 0),
            ("Qty", self._prompt_qty, theme.NEUTRAL, 0),
            ("Remove", self._remove_line, theme.DANGER, 0),
            ("Price", self._prompt_price, theme.NEUTRAL, 1),
            ("Line off", self._prompt_line_discount, theme.NEUTRAL, 1),
            ("Note", self._prompt_note, theme.NEUTRAL, 1),
            ("Clear", self._clear_cart, theme.NEUTRAL, 1),
        ]
        # A second row of line actions costs height the cart card does not have
        # to spare, so everything below it is a notch tighter.
        for index, (label, command, colour, row) in enumerate(specs):
            ctk.CTkButton(
                line_actions, text=label, height=26, command=command,
                font=theme.font(12), fg_color=colour,
                hover_color=theme.DANGER_HOVER if colour == theme.DANGER else theme.NEUTRAL_HOVER,
            ).grid(row=row, column=index % 4, padx=2, pady=1, sticky="ew")
        for spec_row in (0, 1):
            line_actions.grid_rowconfigure(spec_row, minsize=30)

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

        gift_cell = self._field(form, 2, 0, "Pay with gift card — code, optional")
        self.gift_var = ctk.StringVar()
        self.gift_var.trace_add("write", debounce(self, 250, self._on_gift_code))
        ctk.CTkEntry(
            gift_cell, textvariable=self.gift_var, height=32, placeholder_text="GC-…"
        ).grid(row=1, column=0, sticky="ew")
        # A card being bought is not a card paying; the sell button lives on
        # the same row so the two jobs sit next to each other and stay distinct.
        self._gift_balance = None

        totals = Card(card, fg_color=theme.SURFACE_ALT, border_width=0)
        totals.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 0))
        totals.grid_columnconfigure(1, weight=1)

        self.total_labels = {}
        # (key, label, label size, value size, weight, value colour)
        rows = (
            ("subtotal", "Subtotal", 11, 11, "normal", theme.TEXT_MUTED),
            ("discount", "Discount", 11, 11, "normal", theme.TEXT_MUTED),
            ("tax", "Tax", 11, 11, "normal", theme.TEXT_MUTED),
            ("total", "TOTAL", 13, 20, "bold", theme.TEXT),
            ("gift", "Gift card", 11, 13, "bold", theme.PRIMARY),
            ("lbp", "In LBP", 11, 13, "bold", theme.TEXT_MUTED),
            ("change", "Change", 11, 13, "bold", theme.SUCCESS),
        )
        for index, (key, label, label_size, value_size, weight, colour) in enumerate(rows):
            top = 5 if index == 0 else 1
            bottom = 5 if index == len(rows) - 1 else 0
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
        buttons.grid(row=5, column=0, sticky="ew", padx=12, pady=(6, 8))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=2)

        ctk.CTkButton(
            buttons, text="Clear", height=38, command=self._clear_cart,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.complete_button = ctk.CTkButton(
            buttons, text="Complete Sale", height=38, font=theme.font(15, "bold"),
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
            padx=(0, 6) if column == 0 else (6, 0), pady=(0, 4),
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
            show_error(
                self, "Quantities are whole numbers — try 1, 2, 3.", "That is not a quantity"
            )
        except sales_service.SaleError as exc:
            show_error(self, exc, "Not enough stock")
        self._render_cart()

    def _prompt_price(self) -> None:
        """Override a line price. Recorded in the audit trail by the service."""
        line = self._selected_line()
        if line is None:
            return
        if not settings_service.allow_price_override():
            show_error(
                self,
                "Price overrides are switched off in Settings.",
                "Not allowed",
            )
            return

        dialog = FormModal(
            self,
            f"Price — {line.name}",
            [
                {"key": "list_price", "label": "Catalogue price", "type": "readonly",
                 "value": fmt_usd(line.list_price)},
                {"key": "price", "label": "Price to charge (USD)",
                 "value": f"{line.unit_price:.2f}",
                 "hint": "Every override is written to the audit log."},
            ],
            submit_text="Use this price",
        )
        values = dialog.wait_result()
        if not values:
            return
        try:
            self.cart.set_price(line.product_id, parse_amount(values["price"], "price"))
        except (ValueError, sales_service.SaleError) as exc:
            show_error(self, exc, "Could not change the price")
        self._render_cart()

    def _prompt_line_discount(self) -> None:
        line = self._selected_line()
        if line is None:
            return
        dialog = FormModal(
            self,
            f"Discount — {line.name}",
            [
                {"key": "line_value", "label": "Line before discount", "type": "readonly",
                 "value": fmt_usd(line.gross_total)},
                {"key": "amount", "label": "Amount off (USD)",
                 "value": f"{line.discount:.2f}" if line.discount else ""},
                {"key": "percent", "label": "or percent off",
                 "value": "", "hint": "Fill in one or the other, not both."},
            ],
            submit_text="Apply",
        )
        values = dialog.wait_result()
        if not values:
            return
        try:
            amount = values["amount"].strip()
            percent = values["percent"].strip()
            self.cart.set_line_discount(
                line.product_id,
                amount=parse_amount(amount, "amount off") if amount else None,
                percent=parse_amount(percent, "percent off") if percent else None,
            )
        except (ValueError, sales_service.SaleError) as exc:
            show_error(self, exc, "Could not apply the discount")
        self._render_cart()

    def _prompt_note(self) -> None:
        dialog = FormModal(
            self, "Note on this sale",
            [{"key": "note", "label": "Note (printed on the receipt)",
              "type": "text", "value": self.cart.note}],
            submit_text="Save note",
        )
        values = dialog.wait_result()
        if values is not None:
            self.cart.note = values["note"]

    # ------------------------------------------------------------------ #
    # Parking
    # ------------------------------------------------------------------ #

    def _park_sale(self) -> None:
        if self.cart.is_empty:
            show_error(
                self, "There is nothing in the cart to hold on to yet.", "Nothing to park"
            )
            return
        dialog = FormModal(
            self, "Park this sale",
            [{"key": "label", "label": "Name it so you can find it again",
              "placeholder": "e.g. blue jacket, back in 5"}],
            submit_text="Park",
        )
        values = dialog.wait_result()
        if values is None:
            return
        try:
            sales_service.park_sale(
                self.cart, self.shell.user.user_id, values["label"]
            )
        except sales_service.SaleError as exc:
            show_error(self, exc, "Could not park the sale")
            return

        self.cart = sales_service.Cart()
        self.discount_var.set("0")
        self.paid_var.set("")
        self._render_cart()
        self._refresh_parked_button()

    def _open_parked(self) -> None:
        parked = sales_service.list_parked()
        if not parked:
            show_info(
                self,
                "Nothing is parked. Press F2 during a sale to hold it and come "
                "back to it later.",
                "Nothing parked",
            )
            return
        if not self.cart.is_empty and not ask_confirm(
            self,
            "Opening a held sale clears what is in the cart now.\n\n"
            "Park this one first (F2) if you want to come back to it.\n\n"
            "Carry on?",
            "Resume a held sale",
        ):
            return

        modal = ParkedModal(self, parked)
        parked_id = modal.wait_result()
        if parked_id is None:
            self._refresh_parked_button()
            return

        try:
            cart = sales_service.resume_parked(parked_id)
        except sales_service.SaleError as exc:
            show_error(self, exc, "Could not resume the sale")
            self._refresh_parked_button()
            return

        self.cart = cart
        self.discount_var.set(f"{cart.discount:.2f}")
        self.paid_var.set("")
        if cart.customer_id is not None:
            label = next(
                (
                    _customer_label(customer) for customer in self._customers
                    if customer["customer_id"] == cart.customer_id
                ),
                None,
            )
            if label:
                self.customer_var.set(label)
        self._render_cart()
        self._refresh_parked_button()

        if cart.unavailable:
            show_info(
                self,
                "The rest of the sale came back, but these are no longer in "
                "the catalogue:\n\n"
                + "\n".join(f"  {name}" for name in cart.unavailable),
                "Some items were dropped",
            )

    def _remove_line(self) -> None:
        product_id = self.cart_table.selected_int()
        if product_id is not None:
            self.cart.remove(product_id)
            self._render_cart()

    def _clear_cart(self) -> None:
        if self.cart.is_empty or ask_confirm(
            self, "Empty the cart and start again?", "Clear sale"
        ):
            self.cart.clear()
            self.cart.note = ""
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
                "discount": line.discount,
                "line_total": line.line_total,
            }
            for line in self.cart.lines
        ]
        self.cart_table.set_rows(
            rows,
            tag_func=lambda row: "warning" if row["discount"] else (),
            empty_message="Nothing in the cart yet — scan or search to add the first item.",
        )
        self.cart_title.configure(
            text=f"Cart · {self.cart.item_count} item{'s' if self.cart.item_count != 1 else ''}"
        )
        self._render_totals()

    def _render_totals(self) -> None:
        subtotal, discount, tax, total = self._totals()
        rate = getattr(self, "_rate", None) or settings_service.exchange_rate()
        rounding = getattr(self, "_lbp_rounding", None) or settings_service.lbp_rounding()

        self.total_labels["subtotal"].configure(text=fmt_usd(subtotal))
        self.total_labels["discount"].configure(
            text=f"-{fmt_usd(discount)}" if discount else fmt_usd(0)
        )
        self.total_labels["tax"].configure(text=fmt_usd(tax))
        self.total_labels["total"].configure(text=fmt_usd(total))
        self.total_labels["lbp"].configure(text=fmt_lbp(to_lbp(total, rate, rounding)))

        gift = self._gift_towards(total)
        self.total_labels["gift"].configure(
            text=f"-{fmt_usd(gift)}" if gift else "—",
            text_color=theme.PRIMARY if gift else theme.TEXT_MUTED,
        )

        try:
            paid = parse_amount(self.paid_var.get(), "amount received")
        except ValueError:
            paid = ZERO
        paid_usd = usd(paid / rate) if self.currency_var.get() == "LBP" and rate else usd(paid)
        remaining = total - gift
        difference = paid_usd - remaining
        if paid <= ZERO and gift >= total:
            self.total_labels["change"].configure(
                text="covered", text_color=theme.SUCCESS
            )
        elif paid <= ZERO:
            self.total_labels["change"].configure(text="—", text_color=theme.TEXT_MUTED)
        elif difference >= ZERO:
            self.total_labels["change"].configure(
                text=fmt_usd(difference), text_color=theme.SUCCESS
            )
        else:
            self.total_labels["change"].configure(
                text=f"short {fmt_usd(-difference)}", text_color=theme.DANGER
            )

    def _gift_towards(self, total: Decimal) -> Decimal:
        """What the typed card code can put towards this total, or zero."""
        balance = self._gift_balance
        if balance is None or balance <= ZERO:
            return ZERO
        return min(balance, total)

    def _on_gift_code(self) -> None:
        """Look up the typed card's balance; the till shows what it can pay."""
        code = self.gift_var.get().strip()
        if not code:
            self._gift_balance = None
            self._render_totals()
            return
        try:
            card = giftcards_service.balance(code)
        except giftcards_service.GiftCardError:
            self._gift_balance = None
            self._render_totals()
            return
        self._gift_balance = (
            ZERO if card["status"] == giftcards_service.DISABLED
            else D(card["balance_usd"])
        )
        self._render_totals()

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

    def _hold_layaway(self) -> None:
        """Set the cart aside for a customer paying over time."""
        if self.cart.is_empty:
            show_error(
                self, "Put the goods in the cart first, then hold them.", "Nothing to hold"
            )
            return
        from app.services import layaways as layaways_service

        fields = [
            {"key": "deposit", "label": "Deposit taken now (USD, 0 for none)",
             "type": "number", "value": "0"},
            {"key": "deposit_method", "label": "How the deposit was taken",
             "type": "option", "values": ["Cash", "Card"], "value": "Cash"},
            {"key": "due_date", "label": "When they will collect (e.g. 2026-10-01)",
             "type": "entry"},
            {"key": "note", "label": "Note", "type": "entry",
             "placeholder": "Surname, phone, what was agreed…"},
        ]

        held: list[int] = []

        def submit(values):
            held.append(layaways_service.hold(
                self.cart,
                user_id=self.shell.user.user_id,
                customer_id=self._selected_customer_id(),
                deposit=values["deposit"],
                deposit_method=values["deposit_method"],
                due_date=values["due_date"],
                note=values["note"],
            ))

        if FormModal(self, "Hold as a layaway", fields, submit,
                     submit_text="Hold the goods").wait_result():
            layaway = layaways_service.get_layaway(held[0])
            self.cart.clear()
            self.cart.note = ""
            self.discount_var.set("0")
            self._render_cart()
            show_info(
                self,
                f"Held as {layaway['reference']}. The customer owes "
                f"{fmt_usd(layaways_service.outstanding(layaway['layaway_id']))} "
                "on collection.",
                "Layaway held",
            )

    def _sell_gift_card(self) -> None:
        """Sell a card like any other line: it is activated when the sale lands."""
        fields = [
            {"key": "amount", "label": "How much to load onto the card (USD)",
             "type": "number"},
            {"key": "note", "label": "Who it is for (optional)", "type": "entry"},
        ]

        def submit(values):
            code = giftcards_service.generate_code()
            cart_line = self.cart.add_gift_card(code, values["amount"])
            cart_line.name = f"Gift card {code}" + (
                f" — {values['note'].strip()}" if values["note"].strip() else ""
            )
            return code

        if FormModal(self, "Sell a gift card", fields, submit,
                     submit_text="Add to sale").wait_result():
            self._render_cart()

    def _complete_sale(self) -> None:
        if self.cart.is_empty:
            show_error(
                self, "Add something to the cart before taking payment.", "Nothing to sell"
            )
            return
        # The discount box is parsed on every keystroke to keep the running
        # total live, and a half-typed figure is not an error worth shouting
        # about, so that path quietly treats what it cannot read as nothing.
        # Taking the money is the other matter entirely: without this check a
        # discount of "1O" would be dropped on the floor and the customer
        # charged the full price, with nobody told. An empty box is still
        # zero -- parse_amount says so -- so this only stops real rubbish.
        try:
            self.cart.discount = parse_amount(self.discount_var.get(), "discount")
        except ValueError as exc:
            show_error(self, exc, "Invalid discount")
            return

        try:
            paid = parse_amount(self.paid_var.get(), "amount received")
        except ValueError as exc:
            show_error(self, exc, "Invalid amount")
            return

        gift_code = self.gift_var.get().strip()
        try:
            sale_id = sales_service.create_sale(
                user_id=self.shell.user.user_id,
                cart=self.cart,
                customer_id=self._selected_customer_id(),
                payment_method=self.method_var.get(),
                paid_currency=self.currency_var.get(),
                amount_paid=paid,
                note=self.cart.note,
                gift_card_code=gift_code,
            )
        except sales_service.SaleError as exc:
            show_error(self, exc, "Sale not completed")
            return
        except Exception as exc:  # noqa: BLE001 - never lose the cart on a surprise
            show_error(self, exc, "Sale not completed")
            return

        sale = sales_service.get_sale(sale_id)
        self.cart.clear()
        self.cart.note = ""
        self.discount_var.set("0")
        self.paid_var.set("")
        self.gift_var.set("")
        self._gift_balance = None
        self.search_var.set("")
        self._render_cart()
        self._reload_products()

        change = D(sale["change_usd"])
        message = f"Invoice {sale['invoice_no']} — {fmt_usd(sale['total_usd'])}"
        if change > ZERO:
            # The one number the cashier has to act on before the customer walks
            # away, so it goes on its own line and says what to do with it.
            message += f"\n\nGive {fmt_usd(change)} change."
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

        # Every customer has to be pickable from the menu, so this one is
        # deliberately not capped.
        self._customers = list(customers_service.list_customers(limit=None))
        self.customer_menu.configure(
            values=[WALK_IN] + [_customer_label(c) for c in self._customers]
        )

        # The exchange rate and LBP rounding are read by _render_totals, which
        # runs on every keystroke in the amount-received box. Reading them from
        # the database there means a settings query per character typed; the
        # figures only change on the Settings screen, so they are re-read here,
        # where the till is refreshed.
        self._rate = settings_service.exchange_rate()
        self._lbp_rounding = settings_service.lbp_rounding()

        self._reload_products()
        self._render_cart()
        self._refresh_parked_button()
        self._check_till()
        self.after(100, self.search_entry.focus_set)

    def _check_till(self) -> None:
        """Say so up front when the till is shut, rather than at checkout."""
        if not shifts_service.shift_required() or shifts_service.current_shift():
            self.complete_button.configure(state="normal")
            return
        self.complete_button.configure(state="disabled")
        self.header.set_subtitle(
            "The till is closed. Open a shift on the Till screen before selling."
        )

    def _refresh_parked_button(self) -> None:
        count = len(sales_service.list_parked())
        self.parked_button.configure(
            text=f"Parked ({count})" if count else "Parked",
            state="normal" if count else "disabled",
        )


def _customer_label(customer) -> str:
    phone = customer["phone"]
    return f"{customer['name']} ({phone})" if phone else customer["name"]


class ParkedModal(Modal):
    """Pick a held sale to bring back, or throw one away."""

    def __init__(self, parent, parked):
        super().__init__(parent, "Held sales", 560, 420)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        SectionTitle(self, "Sales being held").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 8)
        )

        self.table = DataTable(
            self,
            columns=[
                ("label", "Name", 220, "w"),
                ("username", "Parked by", 110, "w"),
                ("parked_at", "When", 150, "w"),
            ],
            id_key="parked_id",
            height=9,
        )
        self.table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        self.table.on_double_click(self.submit)
        self.reload(parked)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Discard", width=110, height=36,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self.discard,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer, text="Resume", width=130, height=36, command=self.submit
        ).grid(row=0, column=2)

    def reload(self, parked=None) -> None:
        rows = sales_service.list_parked() if parked is None else parked
        self.table.set_rows(
            rows, empty_message="Nothing is parked. Press F2 to hold a sale for later."
        )
        self.table.select_first()

    def discard(self) -> None:
        parked_id = self.table.selected_int()
        if parked_id is None:
            return
        if not ask_confirm(self, "Throw this held sale away?", "Discard held sale"):
            return
        sales_service.delete_parked(parked_id)
        self.reload()

    def submit(self) -> None:
        parked_id = self.table.selected_int()
        if parked_id is None:
            return
        self.result = parked_id
        self.grab_release()
        self.destroy()

class KeysModal(Modal):
    """The keyboard, on one card, for the person at the till."""

    SHORTCUTS = (
        ("F2", "Park this sale and start another"),
        ("F3", "Bring back a parked sale"),
        ("F4", "Complete the sale"),
        ("F6", "Change a line's quantity"),
        ("F7", "Discount one line"),
        ("F8", "Change a price - ask an administrator"),
        ("Ctrl+L", "Jump to the search box"),
        ("Enter", "Add the searched or scanned product"),
        ("Ctrl+Shift+L", "Lock the screen"),
        ("Esc", "Close any window like this one"),
    )

    def __init__(self, parent):
        super().__init__(parent, "The keyboard", 420, 470)
        self.grid_columnconfigure(1, weight=1)

        SectionTitle(self, "The keyboard").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(18, 10)
        )

        for row, (key, does) in enumerate(self.SHORTCUTS, start=1):
            ctk.CTkLabel(
                self, text=key, font=theme.font(13, "bold"),
                text_color=theme.PRIMARY, anchor="w", width=130,
            ).grid(row=row, column=0, sticky="w", padx=(18, 8), pady=3)
            ctk.CTkLabel(
                self, text=does, font=theme.font(12), anchor="w",
                text_color=theme.TEXT,
            ).grid(row=row, column=1, sticky="w", pady=3)

        ctk.CTkButton(
            self, text="Close", height=36, width=120, command=self.on_cancel
        ).grid(row=len(self.SHORTCUTS) + 1, columnspan=2, pady=(14, 16))

        # Escape and the keyboard grab both come from Modal. This class used to
        # repeat them, and the second grab it scheduled was one the inherited
        # cleanup knew nothing about and so could not cancel.
