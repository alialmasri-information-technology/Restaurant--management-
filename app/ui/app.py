"""The root window: owns the database lifecycle and the login/shell swap."""

from __future__ import annotations

import sys
import traceback
from tkinter import messagebox

import customtkinter as ctk

from app import config, db
from app.ui import theme
from app.ui.login import LoginView
from app.ui.shell import AppShell


class RE4App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(config.APP_TITLE)
        self.minsize(1020, 680)
        self.configure(fg_color=theme.BG)
        self._size_to_screen(1180, 760)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.user = None
        self.current_frame: ctk.CTkFrame | None = None

        self._start_database()
        self.show_login()

        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.report_callback_exception = self._on_tk_error

    # ------------------------------------------------------------------ #

    def _size_to_screen(self, width: int, height: int) -> None:
        """Open centred, never larger than the display (laptop screens are short)."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(width, screen_width - 60)
        height = min(height, screen_height - 90)
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 3, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _start_database(self) -> None:
        try:
            db.init_db()
        except Exception as exc:  # noqa: BLE001 - nothing works without a database
            messagebox.showerror(
                "Database error",
                f"{config.APP_NAME} could not open its database.\n\n"
                f"Location: {db.database_path()}\n\n{exc}",
            )
            self.destroy()
            sys.exit(1)

    def _on_tk_error(self, exc_type, value, tb) -> None:
        """Surface unexpected callback errors instead of only printing them."""
        traceback.print_exception(exc_type, value, tb)
        messagebox.showerror(
            "Unexpected error",
            f"{value}\n\nThe action was cancelled. Your data has not been changed.",
        )

    # ------------------------------------------------------------------ #

    def _swap(self, frame: ctk.CTkFrame) -> None:
        if self.current_frame is not None:
            self.current_frame.destroy()
        self.current_frame = frame
        frame.grid(row=0, column=0, sticky="nsew")

    def show_login(self) -> None:
        self.user = None
        self.title(config.APP_TITLE)
        self._swap(LoginView(self, self.on_login))

    def on_login(self, user) -> None:
        self.user = user
        self.title(f"{config.APP_TITLE} — {user.display_name} ({user.role})")
        self._swap(AppShell(self, user, self.show_login))

    def quit_app(self) -> None:
        db.close_connection()
        self.destroy()


def run(seed_demo: bool = False) -> None:
    theme.apply_appearance("System")
    app = RE4App()
    if seed_demo:
        db.seed_demo_data()
    app.mainloop()
