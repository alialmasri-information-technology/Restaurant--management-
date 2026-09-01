"""Signed-in application shell: sidebar navigation plus the content area."""

from __future__ import annotations

import datetime as dt

import customtkinter as ctk

from app import config
from app.ui import theme
from app.ui.widgets import ask_confirm

# (key, label, admin_only)
NAV_ITEMS = (
    ("dashboard", "Dashboard", False),
    ("pos", "New Sale", False),
    ("invoices", "Invoices", False),
    ("products", "Products", False),
    ("customers", "Customers", False),
    ("reports", "Reports", True),
    ("users", "Users", True),
    ("settings", "Settings", True),
)


class AppShell(ctk.CTkFrame):
    def __init__(self, parent, user, on_logout):
        super().__init__(parent, fg_color=theme.BG)
        self.user = user
        self.on_logout = on_logout
        self._views: dict[str, ctk.CTkFrame] = {}
        self.current_key: str | None = None

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.show("dashboard")

    # -- chrome ------------------------------------------------------------- #

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=theme.SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(len(NAV_ITEMS) + 2, weight=1)

        ctk.CTkLabel(
            sidebar, text=config.APP_NAME, font=theme.font(26, "bold"),
            text_color="#ffffff",
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(24, 0))
        ctk.CTkLabel(
            sidebar, text=f"v{config.APP_VERSION}", font=theme.font(11),
            text_color=theme.SIDEBAR_TEXT,
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 20))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for index, (key, label, admin_only) in enumerate(NAV_ITEMS, start=2):
            if admin_only and not self.user.is_admin:
                continue
            button = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                height=40,
                corner_radius=8,
                font=theme.font(13),
                fg_color="transparent",
                hover_color=theme.SIDEBAR_HOVER,
                text_color=theme.SIDEBAR_TEXT,
                command=lambda k=key: self.show(k),
            )
            button.grid(row=index, column=0, sticky="ew", padx=12, pady=2)
            self.nav_buttons[key] = button

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=len(NAV_ITEMS) + 3, column=0, sticky="ew", padx=12, pady=16)
        footer.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            footer, text=self.user.display_name, font=theme.font(13, "bold"),
            text_color="#ffffff", anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=10)
        ctk.CTkLabel(
            footer, text=self.user.role, font=theme.font(11),
            text_color=theme.SIDEBAR_TEXT, anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkButton(
            footer, text="Sign out", height=34, font=theme.font(12),
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._logout,
        ).grid(row=2, column=0, sticky="ew")

    def _logout(self) -> None:
        if ask_confirm(self, "Sign out of RE4?", "Sign out"):
            self.on_logout()

    # -- navigation --------------------------------------------------------- #

    def _build_view(self, key: str) -> ctk.CTkFrame:
        # Imported lazily so a screen's import cost is only paid when opened.
        from app.ui import (
            customers_view,
            dashboard_view,
            invoices_view,
            pos_view,
            products_view,
            reports_view,
            settings_view,
            users_view,
        )

        factories = {
            "dashboard": dashboard_view.DashboardView,
            "pos": pos_view.PosView,
            "invoices": invoices_view.InvoicesView,
            "products": products_view.ProductsView,
            "customers": customers_view.CustomersView,
            "reports": reports_view.ReportsView,
            "users": users_view.UsersView,
            "settings": settings_view.SettingsView,
        }
        return factories[key](self.content, self)

    def show(self, key: str) -> None:
        item = next((i for i in NAV_ITEMS if i[0] == key), None)
        if item is None or (item[2] and not self.user.is_admin):
            return

        if key not in self._views:
            self._views[key] = self._build_view(key)

        for name, view in self._views.items():
            if name != key:
                view.grid_remove()

        view = self._views[key]
        view.grid(row=0, column=0, sticky="nsew")
        if hasattr(view, "refresh"):
            view.refresh()

        self.current_key = key
        for name, button in self.nav_buttons.items():
            active = name == key
            button.configure(
                fg_color=theme.SIDEBAR_ACTIVE if active else "transparent",
                text_color="#ffffff" if active else theme.SIDEBAR_TEXT,
                font=theme.font(13, "bold" if active else "normal"),
            )

    def refresh_current(self) -> None:
        if self.current_key and self.current_key in self._views:
            view = self._views[self.current_key]
            if hasattr(view, "refresh"):
                view.refresh()

    def invalidate(self, *keys: str) -> None:
        """Drop cached views so they rebuild with fresh reference data."""
        for key in keys:
            view = self._views.pop(key, None)
            if view is not None:
                view.destroy()


class PageHeader(ctk.CTkFrame):
    """Title, subtitle and a slot for page-level action buttons."""

    def __init__(self, parent, title: str, subtitle: str = ""):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=title, font=theme.font(24, "bold"),
            text_color=theme.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        self.subtitle_label = ctk.CTkLabel(
            self, text=subtitle or dt.date.today().strftime("%A, %d %B %Y"),
            font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.subtitle_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        # An empty CTkFrame keeps its default 200x200 request, which would pad
        # the header out on pages that add no action buttons.
        self.actions = ctk.CTkFrame(self, fg_color="transparent", width=0, height=0)
        self.actions.grid(row=0, column=1, rowspan=2, sticky="e")

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.configure(text=text)
