"""Screen lock and the forced first password change.

A till sits on a counter in a shop with the drawer open and the owner in the
back room. Signing out to protect it would throw away a half-built cart, so the
lock covers the screen with the shell left intact underneath: the same cart,
the same shift, waiting behind one password.
"""

from __future__ import annotations

import customtkinter as ctk

from app import auth, config, logs
from app.ui import theme
from app.ui.widgets import Card, show_info


class LockScreen(ctk.CTkFrame):
    """Covers the signed-in shell until the operator re-enters their password."""

    def __init__(self, parent, user, on_unlock, on_sign_out, reason: str = ""):
        super().__init__(parent, fg_color=theme.BG)
        self.user = user
        self.on_unlock = on_unlock
        self.on_sign_out = on_sign_out

        self.grid_rowconfigure((0, 2), weight=1)
        self.grid_columnconfigure((0, 2), weight=1)

        card = Card(self, width=400, height=380)
        card.grid(row=1, column=1, padx=20, pady=20)
        card.grid_columnconfigure(0, weight=1)
        card.grid_propagate(False)

        ctk.CTkLabel(
            card, text="Locked", font=theme.font(30, "bold"), text_color=theme.TEXT,
        ).grid(row=0, column=0, padx=36, pady=(44, 0))
        # Says what is safe as well as what happened: somebody coming back to a
        # locked till wants to know the half-built cart is still there.
        ctk.CTkLabel(
            card,
            text=(reason or "Nobody was here for a while, so the screen locked.")
            + " Your work is exactly where you left it.",
            font=theme.font(12), text_color=theme.TEXT_MUTED,
            wraplength=320, justify="center",
        ).grid(row=1, column=0, padx=36, pady=(6, 22))

        ctk.CTkLabel(
            card, text=user.display_name, font=theme.font(15, "bold"),
            text_color=theme.PRIMARY,
        ).grid(row=2, column=0, padx=36)
        ctk.CTkLabel(
            card, text=user.role, font=theme.font(11), text_color=theme.TEXT_MUTED,
        ).grid(row=3, column=0, padx=36, pady=(0, 16))

        self.password = ctk.StringVar()
        self.entry = ctk.CTkEntry(
            card, textvariable=self.password, show="*", height=38,
            placeholder_text="Password",
        )
        self.entry.grid(row=4, column=0, sticky="ew", padx=36)

        self.message = ctk.CTkLabel(
            card, text="", font=theme.font(12), text_color=theme.DANGER,
            wraplength=320, justify="center",
        )
        self.message.grid(row=5, column=0, sticky="ew", padx=36, pady=(10, 0))

        ctk.CTkButton(
            card, text="Unlock", height=40, font=theme.font(14, "bold"),
            command=self.attempt,
        ).grid(row=6, column=0, sticky="ew", padx=36, pady=(8, 0))
        ctk.CTkButton(
            card, text="Sign out instead", height=34, font=theme.font(12),
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_sign_out,
        ).grid(row=7, column=0, sticky="ew", padx=36, pady=(8, 24))

        self.entry.bind("<Return>", lambda _event: self.attempt())
        self.after(120, self._focus_entry)

    def _focus_entry(self) -> None:
        if self.winfo_exists():
            self.entry.focus_set()

    def attempt(self) -> None:
        self.message.configure(text="")
        if auth.verify_user_password(self.user.user_id, self.password.get()):
            self.password.set("")
            self.on_unlock()
            return
        self.password.set("")
        self.message.configure(text="That password does not match. Try again.")
        self.entry.focus_set()


class ForcedPasswordChange(ctk.CTkToplevel):
    """Blocks the app until a real password replaces a temporary one.

    Deliberately not cancellable: closing it signs the operator out rather than
    letting them carry on with a password somebody else knows.
    """

    def __init__(self, parent, user, on_done, on_abandon):
        super().__init__(parent)
        self.user = user
        self.on_done = on_done
        self.on_abandon = on_abandon
        self.settled = False

        self.title("Choose a new password")
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        width, height = 460, 430
        root = parent.winfo_toplevel()
        self.update_idletasks()
        x = root.winfo_rootx() + max((root.winfo_width() - width) // 2, 0)
        y = root.winfo_rooty() + max((root.winfo_height() - height) // 3, 0)
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=28, pady=24)
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body, text="Choose a new password", font=theme.font(20, "bold"),
            text_color=theme.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            body,
            text=(
                f"Welcome, {user.display_name.split(' ')[0]}. You signed in with a "
                "password somebody else set for you, which means somebody else "
                "knows it. Pick one of your own and it stays yours."
            ),
            font=theme.font(12), text_color=theme.TEXT_MUTED,
            wraplength=400, justify="left", anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 18))

        self.fields: dict[str, ctk.StringVar] = {}
        rows = (
            ("current", "Current password", ""),
            ("new", "New password",
             f"At least {auth.MIN_PASSWORD_LENGTH} characters, and different from "
             "the current one."),
            ("confirm", "Confirm new password", ""),
        )
        row = 2
        first_entry = None
        for key, label, hint in rows:
            ctk.CTkLabel(
                body, text=label, font=theme.font(12),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).grid(row=row, column=0, sticky="ew", pady=(6, 4))
            row += 1

            variable = ctk.StringVar()
            entry = ctk.CTkEntry(body, textvariable=variable, show="*", height=36)
            entry.grid(row=row, column=0, sticky="ew")
            entry.bind("<Return>", lambda _event: self.submit())
            first_entry = first_entry or entry
            self.fields[key] = variable
            row += 1

            if hint:
                ctk.CTkLabel(
                    body, text=hint, font=theme.font(11),
                    text_color=theme.TEXT_MUTED, anchor="w",
                    wraplength=400, justify="left",
                ).grid(row=row, column=0, sticky="ew", pady=(4, 0))
                row += 1

        self.message = ctk.CTkLabel(
            body, text="", font=theme.font(12), text_color=theme.DANGER,
            wraplength=400, justify="left", anchor="w",
        )
        self.message.grid(row=row, column=0, sticky="ew", pady=(12, 0))
        row += 1

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=row, column=0, sticky="ew", pady=(14, 0))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons, text="Sign out", width=120, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.abandon,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Set password", width=150, height=38, command=self.submit,
        ).grid(row=0, column=2)

        self.protocol("WM_DELETE_WINDOW", self.abandon)
        self.after(80, self._grab)
        if first_entry is not None:
            self.after(140, lambda: first_entry.winfo_exists() and first_entry.focus_set())

    def _grab(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001  # pragma: no cover - window gone
            pass

    def submit(self) -> None:
        self.message.configure(text="")
        values = {key: variable.get() for key, variable in self.fields.items()}
        if values["new"] != values["confirm"]:
            self.message.configure(text="The two new passwords do not match.")
            return
        try:
            auth.change_password(self.user.user_id, values["current"], values["new"])
        except auth.AuthError as exc:
            self.message.configure(text=str(exc))
            return
        self.settled = True
        self._release()
        show_info(
            self.master,
            "That's yours now — nobody else knows it. You are all set.",
            "Password changed",
        )
        self.on_done()

    def abandon(self) -> None:
        if self.settled:
            return
        self.settled = True
        self._release()
        self.on_abandon()

    def _release(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001  # pragma: no cover - window gone
            pass
        self.destroy()


def idle_lock_seconds() -> int:
    """Inactivity before the screen locks, in seconds. 0 means never."""
    from app.services import settings as settings_service

    try:
        minutes = int(str(settings_service.get("idle_lock_minutes")).strip() or 0)
    except (TypeError, ValueError):
        # This decides when an unattended till locks itself. A shop that set it
        # and is not getting it should not have to guess why.
        logs.error(
            "Setting 'idle_lock_minutes' is not a whole number; using the default"
        )
        minutes = int(config.DEFAULT_SETTINGS["idle_lock_minutes"])
    return max(0, minutes) * 60
