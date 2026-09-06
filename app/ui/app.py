"""The root window: owns the database lifecycle, the login/shell swap and the lock."""

from __future__ import annotations

import sys
import time
import tkinter as tk
import traceback
from tkinter import messagebox

import customtkinter as ctk

from app import auth, config, db, instance, logs
from app.services import audit, backups, housekeeping
from app.ui import background, security, theme
from app.ui.login import LoginView
from app.ui.shell import AppShell

#: How often the idle timer looks at the clock. Coarse on purpose — the lock is
#: measured in minutes, and a tighter tick would wake the process for nothing.
IDLE_POLL_MS = 15_000


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
        self._lock_screen: ctk.CTkFrame | None = None
        self._last_activity = time.monotonic()

        self._start_database()
        self._start_backup()
        background.start(self)
        self.show_login()

        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.report_callback_exception = self._on_tk_error

        # add="+" so these never displace a screen's own bindings.
        for sequence in ("<Any-KeyPress>", "<Any-Button>", "<MouseWheel>"):
            self.bind_all(sequence, self._note_activity, add="+")
        self.after(IDLE_POLL_MS, self._idle_tick)

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
        # Tidying before anybody signs in: it touches nothing a person is
        # looking at, and a slip in it must never keep the shop from opening.
        try:
            housekeeping.tidy()
        except Exception:  # noqa: BLE001
            logs.exception("Housekeeping failed")

    def _start_backup(self) -> None:
        """Snapshot the database at launch. Never fatal - the shop must open."""
        path = backups.run_startup_backup()
        if path is not None:
            logs.info("Startup backup: %s", path.name)

    def _on_tk_error(self, exc_type, value, tb) -> None:
        """Surface unexpected callback errors instead of only printing them."""
        traceback.print_exception(exc_type, value, tb)
        logs.get().error(
            "Unhandled UI error", exc_info=(exc_type, value, tb)
        )
        messagebox.showerror(
            "Unexpected error",
            f"{value}\n\nThe action was cancelled. Your data has not been changed.",
        )

    # ------------------------------------------------------------------ #

    def _swap(self, frame: ctk.CTkFrame) -> None:
        self._clear_lock()
        if self.current_frame is not None:
            self.current_frame.destroy()
        self.current_frame = frame
        frame.grid(row=0, column=0, sticky="nsew")

    # ------------------------------------------------------------------ #
    # Idle lock
    # ------------------------------------------------------------------ #

    def _note_activity(self, _event=None) -> None:
        self._last_activity = time.monotonic()

    @property
    def is_locked(self) -> bool:
        return self._lock_screen is not None

    def _idle_tick(self) -> None:
        try:
            timeout = security.idle_lock_seconds()
            if (
                timeout
                and self.user is not None
                and not self.is_locked
                and time.monotonic() - self._last_activity >= timeout
            ):
                self.lock_screen("Nobody was here for a while, so the screen locked.")
        except Exception:  # noqa: BLE001 - a bad setting must not stop the timer
            logs.exception("Idle lock check failed")
        finally:
            self.after(IDLE_POLL_MS, self._idle_tick)

    def lock_screen(self, reason: str = "") -> None:
        """Cover the shell without tearing it down, so the cart survives."""
        if self.user is None or self.is_locked or self.current_frame is None:
            return
        self.current_frame.grid_remove()
        self._lock_screen = security.LockScreen(
            self, self.user, self._unlock, self.show_login, reason=reason
        )
        self._lock_screen.grid(row=0, column=0, sticky="nsew")
        audit.record("Screen locked", "user", self.user.user_id, reason)

    def _unlock(self) -> None:
        self._clear_lock()
        if self.current_frame is not None:
            self.current_frame.grid(row=0, column=0, sticky="nsew")
        self._note_activity()
        if self.user is not None:
            audit.record("Screen unlocked", "user", self.user.user_id)

    def _clear_lock(self) -> None:
        if self._lock_screen is not None:
            self._lock_screen.destroy()
            self._lock_screen = None

    def show_login(self) -> None:
        if self.user is not None:
            audit.record("Signed out", "user", self.user.user_id)
        self.user = None
        audit.clear_actor()
        self.title(config.APP_TITLE)
        self._swap(LoginView(self, self.on_login))

    def on_login(self, user) -> None:
        self.user = user
        # Set here rather than in authenticate() so every service call made from
        # this point on is attributed without threading a user through it.
        audit.set_actor(user)
        self.title(f"{config.APP_TITLE} — {user.display_name} ({user.role})")
        self._note_activity()
        self._swap(AppShell(self, user, self.show_login))
        if user.must_change_password:
            self.after(200, self._force_password_change)

    def _force_password_change(self) -> None:
        """A temporary password gets one screen and no way past it."""
        user = self.user
        if user is None:
            return

        def done() -> None:
            refreshed = auth.get_user(user.user_id)
            if refreshed is not None:
                self.user = refreshed
                audit.set_actor(refreshed)

        security.ForcedPasswordChange(self, user, done, self.show_login)

    def quit_app(self) -> None:
        self._clear_lock()
        if self.user is not None:
            audit.record("Signed out", "user", self.user.user_id)
        # A snapshot of the day as the shop locks up, taken while the
        # connection can still see a consistent picture. Never fatal.
        path = backups.run_shutdown_backup()
        if path is not None:
            logs.info("Shutdown backup: %s", path.name)
        db.checkpoint()
        logs.info("%s closing", config.APP_NAME)
        db.close_connection()
        self.destroy()


def run(seed_demo: bool = False) -> None:
    logs.setup()
    if not instance.acquire():
        _say_already_running()
        return
    theme.apply_appearance("System")
    app = RE4App()
    if seed_demo:
        db.seed_demo_data()
    app.mainloop()
    instance.release()


def _say_already_running() -> None:
    """A frozen, windowed build has no console; the message needs a window."""
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            config.APP_NAME,
            f"{config.APP_NAME} is already running on this computer.\n\n"
            "Two copies must not work the same till and the same database at "
            "once. Use the copy that is already open.",
        )
        root.destroy()
    except Exception:  # noqa: BLE001 - say it however we can, then leave
        print(f"{config.APP_NAME} is already running.")
