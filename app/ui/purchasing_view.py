"""Purchasing: suppliers, purchase orders and booking stock in."""

from __future__ import annotations

import customtkinter as ctk

from app import config, db
from app.money import D, fmt_usd, parse_amount, parse_int
from app.services import products as products_service
from app.services import purchases as purchases_service
from app.services import suppliers as suppliers_service
from app.ui import phrasing, theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    Modal,
    ask_confirm,
    debounce,
    show_error,
    show_info,
)

ORDER_COLUMNS = (
    ("po_no", "Order", 150, "w"),
    ("supplier_name", "Supplier", 180, "w"),
    ("created_at", "Raised", 140, "w"),
    ("expected_date", "Expected", 100, "w"),
    ("line_count", "Lines", 60, "e"),
    ("received_label", "Received", 90, "e"),
    ("total_cost_usd", "Value", 100, "e"),
    ("status", "Status", 130, "w"),
)

SUPPLIER_COLUMNS = (
    ("name", "Supplier", 200, "w"),
    ("contact_name", "Contact", 150, "w"),
    ("phone", "Phone", 130, "w"),
    ("email", "Email", 200, "w"),
    ("product_count", "Products", 80, "e"),
    ("order_count", "Orders", 70, "e"),
    ("purchased_usd", "Purchased", 110, "e"),
)

REORDER_COLUMNS = (
    ("sku", "SKU", 110, "w"),
    ("name", "Product", 220, "w"),
    ("supplier_name", "Supplier", 160, "w"),
    ("stock_qty", "In stock", 80, "e"),
    ("reorder_level", "Level", 70, "e"),
    ("suggested_qty", "Suggest", 80, "e"),
)

STATUS_FILTERS = ("All",) + config.PO_STATUSES


class PurchasingView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.user = shell.user

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = PageHeader(self, "Purchasing", "Suppliers, orders and goods in")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        ctk.CTkButton(
            header.actions, text="New order", width=130, height=38,
            command=self.new_order,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            header.actions, text="New supplier", width=130, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.new_supplier,
        ).grid(row=0, column=1)

        self.tabs = ctk.CTkTabview(self, fg_color=theme.SURFACE, corner_radius=10)
        self.tabs.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 20))
        self.orders_tab = self.tabs.add("Orders")
        self.suppliers_tab = self.tabs.add("Suppliers")
        self.reorder_tab = self.tabs.add("To reorder")

        self._build_orders()
        self._build_suppliers()
        self._build_reorder()

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #

    def _build_orders(self) -> None:
        tab = self.orders_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(6, 10))
        bar.grid_columnconfigure(0, weight=1)

        self.order_search = ctk.CTkEntry(
            bar, placeholder_text="Search orders by number, supplier or note", height=36
        )
        self.order_search.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.order_search.bind("<KeyRelease>", debounce(self, 250, self.refresh_orders))

        self.status_filter = ctk.CTkOptionMenu(
            bar, values=list(STATUS_FILTERS), width=160, height=36,
            command=lambda _value: self.refresh_orders(),
        )
        self.status_filter.set("All")
        self.status_filter.grid(row=0, column=1, padx=(0, 8))

        ctk.CTkButton(
            bar, text="Receive stock", width=140, height=36,
            fg_color=theme.SUCCESS, hover_color=theme.SUCCESS_HOVER,
            command=self.receive_order,
        ).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            bar, text="Cancel order", width=130, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.cancel_order,
        ).grid(row=0, column=3)

        self.orders = DataTable(tab, ORDER_COLUMNS, id_key="po_id", height=14)
        self.orders.set_formatter("total_cost_usd", lambda value, row: fmt_usd(value))
        self.orders.set_formatter(
            "expected_date", lambda value, _row: phrasing.day_label(value, empty="No date")
        )
        self.orders.set_formatter("supplier_name", lambda value, row: value or "-")
        self.orders.grid(row=1, column=0, sticky="nsew")
        self.orders.on_double_click(self.receive_order)

    def refresh_orders(self) -> None:
        rows = [
            {**dict(row), "received_label": f"{row['units_received']}/{row['units_ordered']}"}
            for row in purchases_service.list_pos(
                search=self.order_search.get(), status=self.status_filter.get()
            )
        ]
        self.orders.set_rows(
            rows, tag_func=self._order_tag,
            empty_message="Nothing here. Raise an order when stock is running low.",
        )

    @staticmethod
    def _order_tag(row):
        return {
            config.PO_RECEIVED: "success",
            config.PO_PARTIAL: "warning",
            config.PO_CANCELLED: "muted",
        }.get(row["status"], ())

    def new_order(self) -> None:
        suppliers = suppliers_service.list_suppliers()
        if not suppliers:
            show_error(
                self, "Add a supplier before raising a purchase order.", "No suppliers"
            )
            return
        modal = OrderModal(self, self.user, suppliers)
        if modal.wait_result() is not None:
            self.refresh()

    def receive_order(self) -> None:
        po_id = self.orders.selected_int()
        if po_id is None:
            show_error(self, "Select a purchase order first.", "Nothing selected")
            return
        order = purchases_service.get_po(po_id)
        if order is None:
            return
        if order["status"] in (config.PO_RECEIVED, config.PO_CANCELLED):
            show_error(
                self, f"{order['po_no']} is {order['status'].lower()}.", "Nothing to receive"
            )
            return

        modal = ReceiveModal(self, self.user, order)
        result = modal.wait_result()
        if result is not None:
            self.refresh()
            self.shell.invalidate("products", "dashboard")
            show_info(
                self,
                f"{result['units']} unit(s) worth {fmt_usd(result['value_usd'])} "
                f"booked in.\n{result['po_no']} is now {result['status'].lower()}.",
                "Stock received",
            )

    def cancel_order(self) -> None:
        po_id = self.orders.selected_int()
        if po_id is None:
            show_error(self, "Select a purchase order first.", "Nothing selected")
            return
        order = purchases_service.get_po(po_id)
        if order is None:
            return
        if not ask_confirm(
            self, f"Cancel {order['po_no']}?\n\nStock already received is not affected.",
            "Cancel order",
        ):
            return
        try:
            purchases_service.set_status(po_id, config.PO_CANCELLED)
        except purchases_service.PurchaseError as exc:
            show_error(self, exc, "Could not cancel")
            return
        self.refresh_orders()

    # ------------------------------------------------------------------ #
    # Suppliers
    # ------------------------------------------------------------------ #

    def _build_suppliers(self) -> None:
        tab = self.suppliers_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(6, 10))
        bar.grid_columnconfigure(0, weight=1)

        self.supplier_search = ctk.CTkEntry(
            bar, placeholder_text="Search suppliers", height=36
        )
        self.supplier_search.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.supplier_search.bind("<KeyRelease>", debounce(self, 250, self.refresh_suppliers))

        self.show_archived = ctk.CTkCheckBox(
            bar, text="Show archived", command=self.refresh_suppliers
        )
        self.show_archived.grid(row=0, column=1, padx=(0, 8))

        ctk.CTkButton(
            bar, text="Edit", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.edit_supplier,
        ).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            bar, text="Delete", width=100, height=36,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self.delete_supplier,
        ).grid(row=0, column=3)

        self.suppliers = DataTable(tab, SUPPLIER_COLUMNS, id_key="supplier_id", height=14)
        self.suppliers.set_formatter("purchased_usd", lambda value, row: fmt_usd(value))
        self.suppliers.grid(row=1, column=0, sticky="nsew")
        self.suppliers.on_double_click(self.edit_supplier)

    def refresh_suppliers(self) -> None:
        rows = suppliers_service.list_suppliers(
            search=self.supplier_search.get(),
            include_inactive=bool(self.show_archived.get()),
        )
        self.suppliers.set_rows(
            rows,
            tag_func=lambda row: () if row["is_active"] else "muted",
            empty_message="No suppliers yet. Add one to raise purchase orders.",
        )

    def _supplier_form(self, supplier=None) -> None:
        existing = dict(supplier) if supplier is not None else {}

        def submit(values):
            payload = {
                "name": values["name"],
                "contact_name": values["contact_name"],
                "phone": values["phone"],
                "email": values["email"],
                "address": values["address"],
                "payment_terms": values["payment_terms"],
                "notes": values["notes"],
            }
            if supplier is None:
                suppliers_service.create_supplier(**payload)
            else:
                suppliers_service.update_supplier(
                    supplier["supplier_id"], is_active=bool(values["is_active"]), **payload
                )

        fields = [
            {"key": "name", "label": "Supplier name", "value": existing.get("name", "")},
            {"key": "contact_name", "label": "Contact", "value": existing.get("contact_name", "")},
            {"key": "phone", "label": "Phone", "value": existing.get("phone", "")},
            {"key": "email", "label": "Email", "value": existing.get("email", "")},
            {"key": "address", "label": "Address", "value": existing.get("address", "")},
            {
                "key": "payment_terms", "label": "Payment terms",
                "value": existing.get("payment_terms", ""),
                "placeholder": "e.g. 30 days",
            },
            {"key": "notes", "label": "Notes", "type": "text", "value": existing.get("notes", "")},
        ]
        if supplier is not None:
            fields.append({
                "key": "is_active", "label": "Active", "type": "check",
                "value": bool(existing.get("is_active", 1)),
            })

        modal = FormModal(
            self, "Add supplier" if supplier is None else f"Edit {existing.get('name', '')}",
            fields=fields, on_submit=submit,
        )
        if modal.wait_result() is not None:
            self.refresh_suppliers()
            self.shell.invalidate("products")

    def new_supplier(self) -> None:
        self._supplier_form()

    def edit_supplier(self) -> None:
        supplier_id = self.suppliers.selected_int()
        if supplier_id is None:
            show_error(self, "Select a supplier first.", "Nothing selected")
            return
        self._supplier_form(suppliers_service.get_supplier(supplier_id))

    def delete_supplier(self) -> None:
        supplier_id = self.suppliers.selected_int()
        if supplier_id is None:
            show_error(self, "Select a supplier first.", "Nothing selected")
            return
        supplier = suppliers_service.get_supplier(supplier_id)
        if supplier is None:
            return
        if not ask_confirm(self, f"Delete {supplier['name']}?", "Delete supplier"):
            return
        try:
            suppliers_service.delete_supplier(supplier_id)
        except suppliers_service.SupplierError as exc:
            # Archiving instead of deleting is reported through this same path.
            show_info(self, exc, "Supplier archived")
        self.refresh_suppliers()

    # ------------------------------------------------------------------ #
    # Reorder suggestions
    # ------------------------------------------------------------------ #

    def _build_reorder(self) -> None:
        tab = self.reorder_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(6, 10))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            bar,
            text="Products at or below their reorder level.",
            font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            bar, text="Order everything below level", width=230, height=36,
            command=self.order_suggestions,
        ).grid(row=0, column=1)

        self.reorder = DataTable(tab, REORDER_COLUMNS, id_key="product_id", height=14)
        self.reorder.set_formatter("supplier_name", lambda value, row: value or "-")
        self.reorder.grid(row=1, column=0, sticky="nsew")

    def refresh_reorder(self) -> None:
        self.reorder.set_rows(
            purchases_service.suggested_reorder(),
            tag_func=lambda row: "danger" if row["stock_qty"] == 0 else "warning",
            empty_message="Nothing needs reordering — every shelf is above its level.",
        )

    def order_suggestions(self) -> None:
        rows = purchases_service.suggested_reorder()
        if not rows:
            show_info(self, "Nothing is below its reorder level.", "Nothing to order")
            return

        # One order per supplier: a purchase order goes to a single supplier.
        by_supplier: dict[int | None, list] = {}
        for row in rows:
            by_supplier.setdefault(row["supplier_id"], []).append(row)

        unassigned = by_supplier.pop(None, [])
        if not by_supplier:
            show_error(
                self,
                "None of the products below their reorder level have a supplier.\n\n"
                "Set a supplier on the product first.",
                "No supplier",
            )
            return

        summary = ", ".join(
            f"{len(items)} line(s) for {items[0]['supplier_name']}"
            for items in by_supplier.values()
        )
        if not ask_confirm(
            self,
            f"Raise {len(by_supplier)} draft order(s)?\n\n{summary}"
            + (f"\n\n{len(unassigned)} product(s) have no supplier and are skipped."
               if unassigned else ""),
            "Raise orders",
        ):
            return

        # All the drafts go up in one transaction: a failure part-way must not
        # leave half the suppliers with an order raised and half without.
        created = 0
        try:
            with db.transaction():
                for supplier_id, items in by_supplier.items():
                    purchases_service.create_po(
                        supplier_id=supplier_id, user_id=self.user.user_id,
                        lines=[
                            (row["product_id"], row["suggested_qty"], row["cost_usd"])
                            for row in items
                        ],
                        note="Raised from reorder suggestions",
                    )
                    created += 1
        except purchases_service.PurchaseError as exc:
            show_error(self, exc, "Could not raise an order")
            return
        if created:
            self.refresh()
            self.tabs.set("Orders")
            show_info(self, f"{created} draft order(s) raised.", "Orders raised")

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self.refresh_orders()
        self.refresh_suppliers()
        self.refresh_reorder()


class OrderModal(Modal):
    """Build a purchase order line by line."""

    def __init__(self, parent, user, suppliers):
        super().__init__(parent, "New purchase order", width=760, height=560)
        self.user = user
        self.suppliers = suppliers
        self.lines: list[dict] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top, text="Supplier", font=theme.font(12), text_color=theme.TEXT_MUTED
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.supplier_menu = ctk.CTkOptionMenu(
            top, values=[row["name"] for row in suppliers], width=240, height=34
        )
        self.supplier_menu.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            top, text="Expected", font=theme.font(12), text_color=theme.TEXT_MUTED
        ).grid(row=0, column=2, sticky="w", padx=(16, 8))
        self.expected = ctk.CTkEntry(top, width=130, height=34, placeholder_text="YYYY-MM-DD")
        self.expected.grid(row=0, column=3)

        picker = Card(self)
        picker.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
        picker.grid_columnconfigure(0, weight=1)

        self.search = ctk.CTkEntry(
            picker, placeholder_text="Search a product by name, SKU or barcode", height=34
        )
        self.search.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=12)
        self.search.bind("<Return>", lambda _event: self.add_line())
        self.qty = ctk.CTkEntry(picker, width=70, height=34, placeholder_text="Qty")
        self.qty.grid(row=0, column=1, padx=(0, 8), pady=12)
        self.cost = ctk.CTkEntry(picker, width=90, height=34, placeholder_text="Unit cost")
        self.cost.grid(row=0, column=2, padx=(0, 8), pady=12)
        ctk.CTkButton(
            picker, text="Add line", width=110, height=34, command=self.add_line
        ).grid(row=0, column=3, padx=(0, 12), pady=12)

        self.table = DataTable(
            self,
            (
                ("sku", "SKU", 110, "w"),
                ("name", "Product", 240, "w"),
                ("qty", "Qty", 70, "e"),
                ("unit_cost", "Unit cost", 100, "e"),
                ("line_total", "Line total", 110, "e"),
            ),
            id_key="product_id", height=8,
        )
        for key in ("unit_cost", "line_total"):
            self.table.set_formatter(key, lambda value, row: fmt_usd(value))
        self.table.grid(row=2, column=0, sticky="nsew", padx=18)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(10, 16))
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            footer, text="Remove line", width=120, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.remove_line,
        ).grid(row=0, column=0)
        self.total_label = ctk.CTkLabel(
            footer, text="Total $0.00", font=theme.font(16, "bold"), text_color=theme.TEXT
        )
        self.total_label.grid(row=0, column=1, sticky="e", padx=12)
        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            footer, text="Raise order", width=140, height=36, command=self.submit
        ).grid(row=0, column=3)

    def add_line(self) -> None:
        term = self.search.get().strip()
        if not term:
            return
        product = products_service.get_by_code(term)
        if product is None:
            matches = products_service.list_products(search=term)
            if len(matches) != 1:
                show_error(
                    self,
                    "No single product matched that search. Use the exact SKU or barcode."
                    if matches else f"Nothing matched '{term}'.",
                    "Product not found",
                )
                return
            product = matches[0]

        try:
            qty = parse_int(self.qty.get() or "1", "quantity")
            cost = (
                parse_amount(self.cost.get(), "unit cost")
                if self.cost.get().strip() else D(product["cost_usd"])
            )
        except ValueError as exc:
            show_error(self, exc, "Check the line")
            return
        if qty <= 0:
            show_error(self, "Quantity must be at least 1.", "Check the line")
            return

        existing = next(
            (line for line in self.lines if line["product_id"] == product["product_id"]), None
        )
        if existing is not None:
            existing["qty"] += qty
            existing["unit_cost"] = cost
        else:
            self.lines.append({
                "product_id": product["product_id"],
                "sku": product["sku"],
                "name": product["name"],
                "qty": qty,
                "unit_cost": cost,
            })

        self.search.delete(0, "end")
        self.qty.delete(0, "end")
        self.cost.delete(0, "end")
        self.search.focus_set()
        self._redraw()

    def remove_line(self) -> None:
        product_id = self.table.selected_int()
        if product_id is None:
            return
        self.lines = [line for line in self.lines if line["product_id"] != product_id]
        self._redraw()

    def _redraw(self) -> None:
        rows = []
        total = D(0)
        for line in self.lines:
            line_total = D(line["qty"]) * D(line["unit_cost"])
            total += line_total
            rows.append({**line, "line_total": line_total})
        self.table.set_rows(rows, empty_message="Add the products you are ordering.")
        self.total_label.configure(text=f"Total {fmt_usd(total)}")

    def submit(self) -> None:
        if not self.lines:
            show_error(self, "Add at least one product.", "Nothing to order")
            return
        name = self.supplier_menu.get()
        supplier = next((row for row in self.suppliers if row["name"] == name), None)
        try:
            po_id = purchases_service.create_po(
                supplier_id=supplier["supplier_id"] if supplier else None,
                user_id=self.user.user_id,
                lines=[
                    (line["product_id"], line["qty"], line["unit_cost"])
                    for line in self.lines
                ],
                expected_date=self.expected.get(),
                status=config.PO_ORDERED,
            )
        except purchases_service.PurchaseError as exc:
            show_error(self, exc, "Could not raise the order")
            return
        self.result = po_id
        self.grab_release()
        self.destroy()


class ReceiveModal(Modal):
    """Enter what actually turned up against an order."""

    def __init__(self, parent, user, order):
        super().__init__(parent, f"Receive {order['po_no']}", width=680, height=520)
        self.user = user
        self.order = order
        self.entries: dict[int, ctk.CTkEntry] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=f"{order['supplier_name'] or 'No supplier'}  -  {order['status']}",
            font=theme.font(13), text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18)
        body.grid_columnconfigure(1, weight=1)

        headings = ("SKU", "Product", "Ordered", "Already in", "Arriving now")
        for column, text in enumerate(headings):
            ctk.CTkLabel(
                body, text=text, font=theme.font(11, "bold"),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).grid(row=0, column=column, sticky="w", padx=6, pady=(0, 6))

        for index, item in enumerate(purchases_service.po_items(order["po_id"]), start=1):
            outstanding = item["qty_ordered"] - item["qty_received"]
            ctk.CTkLabel(
                body, text=item["sku"], font=theme.font(12), anchor="w"
            ).grid(row=index, column=0, sticky="w", padx=6, pady=3)
            ctk.CTkLabel(
                body, text=item["name"], font=theme.font(12), anchor="w"
            ).grid(row=index, column=1, sticky="ew", padx=6, pady=3)
            ctk.CTkLabel(
                body, text=str(item["qty_ordered"]), font=theme.font(12), anchor="e"
            ).grid(row=index, column=2, sticky="e", padx=6, pady=3)
            ctk.CTkLabel(
                body, text=str(item["qty_received"]), font=theme.font(12), anchor="e"
            ).grid(row=index, column=3, sticky="e", padx=6, pady=3)

            entry = ctk.CTkEntry(body, width=90, height=32)
            entry.insert(0, str(outstanding))
            entry.grid(row=index, column=4, padx=6, pady=3)
            if outstanding <= 0:
                entry.configure(state="disabled")
            self.entries[item["po_item_id"]] = entry

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(12, 16))
        footer.grid_columnconfigure(1, weight=1)

        self.update_cost = ctk.CTkCheckBox(
            footer, text="Update product cost (weighted average)"
        )
        self.update_cost.select()
        self.update_cost.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            footer, text="Book in", width=140, height=36, command=self.submit
        ).grid(row=0, column=3)

    def submit(self) -> None:
        quantities = {}
        for po_item_id, entry in self.entries.items():
            text = entry.get().strip()
            if not text:
                continue
            try:
                quantities[po_item_id] = parse_int(text, "quantity")
            except ValueError as exc:
                show_error(self, exc, "Check the quantities")
                return

        try:
            result = purchases_service.receive(
                self.order["po_id"], self.user.user_id, quantities,
                update_cost=bool(self.update_cost.get()),
            )
        except purchases_service.PurchaseError as exc:
            show_error(self, exc, "Could not receive the stock")
            return

        self.result = result
        self.grab_release()
        self.destroy()
