"""Colour tokens, fonts and ttk styling.

CustomTkinter accepts ``(light, dark)`` tuples for every colour option, so the
tokens below carry both variants and the app follows the operating system's
appearance automatically. ``ttk.Treeview`` is not a CustomTkinter widget and
has to be restyled by hand, which :func:`style_treeview` does.
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk

# (light, dark)
BG = ("#f2f4f7", "#15181d")
SURFACE = ("#ffffff", "#1e222a")
SURFACE_ALT = ("#e9edf3", "#262b34")
BORDER = ("#d3d9e2", "#333a45")
TEXT = ("#111827", "#e8eaef")
TEXT_MUTED = ("#5c6675", "#98a2b3")

PRIMARY = ("#2563eb", "#3b82f6")
PRIMARY_HOVER = ("#1d4ed8", "#60a5fa")
SUCCESS = ("#15803d", "#22c55e")
SUCCESS_HOVER = ("#166534", "#4ade80")
WARNING = ("#b45309", "#f59e0b")
DANGER = ("#b91c1c", "#ef4444")
DANGER_HOVER = ("#991b1b", "#f87171")
NEUTRAL = ("#64748b", "#475569")
NEUTRAL_HOVER = ("#475569", "#64748b")

SIDEBAR = ("#111827", "#0f1319")
SIDEBAR_ACTIVE = ("#2563eb", "#2563eb")
SIDEBAR_HOVER = ("#1f2937", "#1c2230")
SIDEBAR_TEXT = ("#e5e7eb", "#cbd5e1")

# Treeview row tags -> (background, foreground) per appearance mode.
ROW_TAGS = {
    "danger": {"light": ("#fee2e2", "#7f1d1d"), "dark": ("#3b1d1d", "#fca5a5")},
    "warning": {"light": ("#fef3c7", "#78350f"), "dark": ("#3a2f14", "#fcd34d")},
    "muted": {"light": ("#f3f4f6", "#6b7280"), "dark": ("#232830", "#8b95a5")},
    "success": {"light": ("#dcfce7", "#14532d"), "dark": ("#16301f", "#86efac")},
}


def _mode() -> str:
    return "dark" if ctk.get_appearance_mode() == "Dark" else "light"


def pick(token) -> str:
    """Resolve a ``(light, dark)`` token to the colour for the current mode."""
    if isinstance(token, (tuple, list)):
        return token[1] if _mode() == "dark" else token[0]
    return token


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """Fonts must be built after the Tk root exists, so this is a function."""
    return ctk.CTkFont(size=size, weight=weight)


def apply_appearance(mode: str = "System") -> None:
    ctk.set_appearance_mode(mode)
    ctk.set_default_color_theme("blue")


def style_treeview(widget) -> None:
    """Make ttk.Treeview match the surrounding CustomTkinter surfaces."""
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except Exception:  # pragma: no cover - only on exotic Tk builds
        pass

    surface = pick(SURFACE)
    surface_alt = pick(SURFACE_ALT)
    text = pick(TEXT)
    border = pick(BORDER)
    primary = pick(PRIMARY)

    style.configure(
        "RE4.Treeview",
        background=surface,
        fieldbackground=surface,
        foreground=text,
        bordercolor=border,
        borderwidth=0,
        rowheight=30,
        font=("Segoe UI", 10),
    )
    style.configure(
        "RE4.Treeview.Heading",
        background=surface_alt,
        foreground=text,
        relief="flat",
        borderwidth=0,
        padding=(8, 8),
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "RE4.Treeview.Heading",
        background=[("active", surface_alt)],
    )
    style.map(
        "RE4.Treeview",
        background=[("selected", primary)],
        foreground=[("selected", "#ffffff")],
    )
    style.layout(
        "RE4.Treeview",
        [("RE4.Treeview.treearea", {"sticky": "nswe"})],  # drop the default border
    )
