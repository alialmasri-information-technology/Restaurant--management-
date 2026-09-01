"""User accounts (Admin only)."""

from __future__ import annotations

import customtkinter as ctk

from app import auth, config
from app.ui import theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    ask_confirm,
    show_error,
    show_info,
)


class UsersView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = PageHeader(
            self, "Users", "Who can sign in, and what they are allowed to do"
        )
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        ctk.CTkButton(
            header.actions, text="+ Add user", height=36, width=130,
            font=theme.font(13, "bold"), command=self._add,
        ).grid(row=0, column=0)

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 12))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        self.table = DataTable(
            card,
            columns=[
                ("username", "Username", 180, "w"),
                ("full_name", "Full name", 240, "w"),
                ("role", "Role", 140, "w"),
                ("is_active", "Active", 100, "center"),
            ],
            id_key="user_id",
            height=14,
            border_width=0,
            fg_color="transparent",
        )
        self.table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.table.on_double_click(self._edit)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 20))
        for column, (label, command, colour) in enumerate((
            ("Edit", self._edit, theme.NEUTRAL),
            ("Reset password", self._reset_password, theme.PRIMARY),
            ("Activate / deactivate", self._toggle_active, theme.DANGER),
        )):
            ctk.CTkButton(
                buttons, text=label, height=36, width=170, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=(0, 8))

        ctk.CTkLabel(
            self,
            text="Admins see every screen. Employees can make sales, view invoices, "
                 "manage customers, and adjust stock — but not edit the catalogue, "
                 "reports, users or settings.",
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=900, justify="left",
        ).grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))

    # ------------------------------------------------------------------ #

    def _selected(self):
        user_id = self.table.selected_int()
        if user_id is None:
            show_error(self, "Select a user first.", "Nothing selected")
            return None
        return auth.get_user(user_id)

    def _add(self) -> None:
        fields = [
            {"key": "username", "label": "Username"},
            {"key": "full_name", "label": "Full name"},
            {"key": "role", "label": "Role", "type": "option", "values": list(config.ROLES)},
            {"key": "password", "label": "Password", "type": "password",
             "hint": f"At least {auth.MIN_PASSWORD_LENGTH} characters."},
        ]
        dialog = FormModal(
            self, "Add user", fields,
            lambda values: auth.create_user(
                username=values["username"], password=values["password"],
                role=values["role"], full_name=values["full_name"],
            ),
            submit_text="Add user",
        )
        if dialog.wait_result():
            self.refresh()

    def _edit(self) -> None:
        user = self._selected()
        if user is None:
            return
        fields = [
            {"key": "username", "label": "Username", "value": user.username},
            {"key": "full_name", "label": "Full name", "value": user.full_name},
            {"key": "role", "label": "Role", "type": "option",
             "values": list(config.ROLES), "value": user.role},
        ]
        dialog = FormModal(
            self, f"Edit — {user.username}", fields,
            lambda values: auth.update_user(
                user.user_id, username=values["username"], role=values["role"],
                full_name=values["full_name"],
            ),
        )
        if dialog.wait_result():
            self.refresh()
            if user.user_id == self.shell.user.user_id:
                show_info(
                    self,
                    "You changed your own account. Sign out and back in for the "
                    "change to take full effect.",
                    "Sign in again",
                )

    def _reset_password(self) -> None:
        user = self._selected()
        if user is None:
            return
        dialog = FormModal(
            self, f"Reset password — {user.username}",
            [{"key": "password", "label": "New password", "type": "password",
              "hint": f"At least {auth.MIN_PASSWORD_LENGTH} characters."}],
            lambda values: auth.set_password(user.user_id, values["password"]),
            submit_text="Set password",
        )
        if dialog.wait_result():
            show_info(self, f"Password updated for {user.username}.", "Password reset")

    def _toggle_active(self) -> None:
        user = self._selected()
        if user is None:
            return
        if user.user_id == self.shell.user.user_id:
            show_error(self, "You cannot deactivate your own account.", "Not allowed")
            return

        action = "Deactivate" if user.is_active else "Activate"
        if not ask_confirm(self, f"{action} {user.username}?", f"{action} user"):
            return
        try:
            auth.set_active(user.user_id, not user.is_active)
        except auth.AuthError as exc:
            show_error(self, exc, "Not allowed")
            return
        self.refresh()

    def refresh(self) -> None:
        rows = [
            {
                "user_id": user.user_id,
                "username": user.username,
                "full_name": user.full_name or "—",
                "role": user.role,
                "is_active": user.is_active,
            }
            for user in auth.list_users()
        ]
        self.table.set_rows(
            rows, tag_func=lambda row: () if row["is_active"] else "muted"
        )
