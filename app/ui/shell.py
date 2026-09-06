"""Signed-in application shell: sidebar navigation plus the content area."""

from __future__ import annotations

import datetime as dt
import webbrowser

import customtkinter as ctk

from app import config
from app.ui import background, theme
from app.ui.widgets import ask_confirm

# (key, label, admin_only)
NAV_ITEMS = (
    ("dashboard", "Dashboard", False),
    ("pos", "New Sale", False),
    ("till", "Till", False),
    ("invoices", "Invoices", False),
    ("products", "Products", False),
    # Staff count the shelves; only an admin may post the variance, which the
    # screen enforces itself rather than hiding the whole page from them.
    ("stocktake", "Stock take", False),
    ("purchasing", "Purchasing", True),
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

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.show("dashboard")
        self._check_for_update()

    # -- update banner ------------------------------------------------------ #

    def _check_for_update(self) -> None:
        """Ask once a day whether a newer copy exists; say so quietly if so.

        The question travels on the background worker — the network takes what
        it takes, and the till must answer the scanner while it waits. An
        offline shop gets no banner and no error, which is the correct answer.
        """
        from app.services import settings as settings_service
        from app.services import updates

        def job():
            return updates.check(settings_service)

        background.run(job, self._show_update_banner, parent=self)

    def _show_update_banner(self, found) -> None:
        if not found:
            return
        version, page = found

        banner = ctk.CTkFrame(self, fg_color=theme.WARNING, corner_radius=0, height=40)
        banner.grid(row=0, column=1, sticky="ew")
        banner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            banner,
            text=f"Version {version} is available.",
            font=theme.font(12, "bold"), text_color="#1a1a1a", anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(16, 0), pady=8)
        ctk.CTkButton(
            banner, text="See what's new", width=130, height=26,
            fg_color="#00000000", hover_color=theme.NEUTRAL_HOVER,
            text_color="#1a1a1a", border_width=1, border_color="#1a1a1a",
            command=lambda: webbrowser.open(page),
        ).grid(row=0, column=1, padx=8, pady=6)
        ctk.CTkButton(
            banner, text="Dismiss", width=90, height=26,
            fg_color="#00000000", hover_color=theme.NEUTRAL_HOVER,
            text_color="#1a1a1a", border_width=1, border_color="#1a1a1a",
            command=banner.destroy,
        ).grid(row=0, column=2, padx=(0, 12), pady=6)

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
        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1, uniform="footer")
        ctk.CTkButton(
            actions, text="Lock", height=34, font=theme.font(12),
            fg_color=theme.SIDEBAR_HOVER, hover_color=theme.NEUTRAL,
            command=self.lock,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            actions, text="Sign out", height=34, font=theme.font(12),
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._logout,
        ).grid(row=0, column=1, sticky="ew")

        # Ctrl+L belongs to the till's search box, so the lock takes the shifted
        # chord — near enough to be muscle memory, far enough not to collide.
        self.winfo_toplevel().bind("<Control-Shift-L>", lambda _event: self.lock())

    def lock(self) -> None:
        """Cover the screen, keeping whatever is half-finished underneath."""
        root = self.winfo_toplevel()
        if hasattr(root, "lock_screen"):
            root.lock_screen("You locked the screen.")

    def _logout(self) -> None:
        # Signing out tears the shell down, so a half-built cart goes with it —
        # worth saying, since Lock is right next to this button and does not.
        first = self.user.display_name.split(" ")[0]
        if ask_confirm(
            self,
            f"Sign out, {first}?\n\nAnything half-finished on screen will be "
            "lost. Use Lock instead if you are coming back.",
            "Sign out",
        ):
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
            purchasing_view,
            reports_view,
            settings_view,
            stocktake_view,
            till_view,
            users_view,
        )

        factories = {
            "dashboard": dashboard_view.DashboardView,
            "pos": pos_view.PosView,
            "till": till_view.TillView,
            "invoices": invoices_view.InvoicesView,
            "products": products_view.ProductsView,
            "stocktake": stocktake_view.StockTakeView,
            "purchasing": purchasing_view.PurchasingView,
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

        self.title_label = ctk.CTkLabel(
            self, text=title, font=theme.font(24, "bold"),
            text_color=theme.TEXT, anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="ew")
        self.subtitle_label = ctk.CTkLabel(
            self, text=subtitle or dt.date.today().strftime("%A, %d %B %Y"),
            font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.subtitle_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        # An empty CTkFrame keeps its default 200x200 request, which would pad
        # the header out on pages that add no action buttons.
        self.actions = ctk.CTkFrame(self, fg_color="transparent", width=0, height=0)
        self.actions.grid(row=0, column=1, rowspan=2, sticky="e")

    def set_title(self, text: str) -> None:
        """Some titles are not constants — a greeting changes with the hour."""
        self.title_label.configure(text=text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.configure(text=text)
