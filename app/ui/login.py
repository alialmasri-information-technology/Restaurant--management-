"""Sign-in screen."""

from __future__ import annotations

import customtkinter as ctk

from app import auth, config, db
from app.ui import theme
from app.ui.widgets import Card


class LoginView(ctk.CTkFrame):
    def __init__(self, parent, on_success):
        super().__init__(parent, fg_color=theme.BG)
        self.on_success = on_success

        self.grid_rowconfigure((0, 2), weight=1)
        self.grid_columnconfigure((0, 2), weight=1)

        card = Card(self, width=400)
        card.grid(row=1, column=1, padx=20, pady=20)
        card.grid_columnconfigure(0, weight=1)
        card.grid_propagate(False)
        card.configure(height=460)

        ctk.CTkLabel(
            card, text=config.APP_NAME, font=theme.font(34, "bold"),
            text_color=theme.PRIMARY,
        ).grid(row=0, column=0, padx=36, pady=(40, 0))
        ctk.CTkLabel(
            card, text="Business & Retail Management", font=theme.font(13),
            text_color=theme.TEXT_MUTED,
        ).grid(row=1, column=0, padx=36, pady=(2, 26))

        self.username = ctk.StringVar()
        self.password = ctk.StringVar()

        ctk.CTkLabel(
            card, text="Username", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=36, pady=(0, 4))
        self.username_entry = ctk.CTkEntry(card, textvariable=self.username, height=38)
        self.username_entry.grid(row=3, column=0, sticky="ew", padx=36)

        ctk.CTkLabel(
            card, text="Password", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=4, column=0, sticky="ew", padx=36, pady=(14, 4))
        self.password_entry = ctk.CTkEntry(
            card, textvariable=self.password, show="*", height=38
        )
        self.password_entry.grid(row=5, column=0, sticky="ew", padx=36)

        self.message = ctk.CTkLabel(
            card, text="", font=theme.font(12), text_color=theme.DANGER,
            wraplength=320, justify="left",
        )
        self.message.grid(row=6, column=0, sticky="ew", padx=36, pady=(12, 0))

        ctk.CTkButton(
            card, text="Sign in", height=40, font=theme.font(14, "bold"),
            command=self.attempt_login,
        ).grid(row=7, column=0, sticky="ew", padx=36, pady=(10, 0))

        hint = self._first_run_hint()
        ctk.CTkLabel(
            card, text=hint, font=theme.font(11), text_color=theme.TEXT_MUTED,
            wraplength=320, justify="center",
        ).grid(row=8, column=0, sticky="ew", padx=36, pady=(16, 0))

        for widget in (self, self.username_entry, self.password_entry):
            widget.bind("<Return>", lambda _event: self.attempt_login())
        self.after(150, self.username_entry.focus_set)

    def _first_run_hint(self) -> str:
        """Only reveal the bootstrap credentials while they are still in place."""
        users = db.query("SELECT username FROM users")
        if len(users) == 1 and users[0]["username"].lower() == "admin":
            try:
                auth.authenticate("admin", "admin123")
            except auth.AuthError:
                return ""
            return "First run — sign in as admin / admin123, then change the password in Settings."
        return ""

    def attempt_login(self) -> None:
        self.message.configure(text="")
        try:
            user = auth.authenticate(self.username.get(), self.password.get())
        except auth.AuthError as exc:
            self.message.configure(text=str(exc))
            self.password.set("")
            self.password_entry.focus_set()
            return
        self.on_success(user)
