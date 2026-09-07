"""Reusable UI building blocks shared by every screen."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from app.ui import theme
from app.ui.phrasing import truncation_note

# --------------------------------------------------------------------------- #
# Message helpers
# --------------------------------------------------------------------------- #

def show_error(parent, message: str, title: str = "Something went wrong") -> None:
    messagebox.showerror(title, str(message), parent=parent)


def show_info(parent, message: str, title: str = "All done") -> None:
    messagebox.showinfo(title, str(message), parent=parent)


def ask_confirm(parent, message: str, title: str = "Just checking") -> bool:
    return bool(messagebox.askyesno(title, str(message), parent=parent))


# --------------------------------------------------------------------------- #
# Typing helpers
# --------------------------------------------------------------------------- #

def debounce(widget, milliseconds: int, fn):
    """Wrap ``fn`` so it runs once, ``milliseconds`` after typing goes quiet.

    A search box that re-queries the database on every keystroke does the
    query's work one letter at a time: type "stapler" and the catalogue is
    searched six times, five of them for prefixes nobody wanted. Waiting for a
    quiet moment costs nothing a person can feel and runs the query once.
    """
    after_id = None

    def schedule(*_args) -> None:
        nonlocal after_id
        if after_id is not None:
            widget.after_cancel(after_id)
        after_id = widget.after(milliseconds, fn)

    return schedule


# --------------------------------------------------------------------------- #
# Layout pieces
# --------------------------------------------------------------------------- #

class Card(ctk.CTkFrame):
    """A padded surface used to group related controls."""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("fg_color", theme.SURFACE)
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER)
        super().__init__(parent, **kwargs)


class SectionTitle(ctk.CTkLabel):
    def __init__(self, parent, text: str, **kwargs):
        super().__init__(
            parent,
            text=text,
            font=theme.font(15, "bold"),
            text_color=theme.TEXT,
            anchor="w",
            **kwargs,
        )


class StatCard(Card):
    """Headline number with a label above, an optional note below, and a trend.

    The trend chip is the part that makes the number mean something: $840 today
    only reads as good or bad next to what the same length of time made before.
    """

    def __init__(self, parent, title: str, value: str = "—", note: str = "", accent=None):
        super().__init__(parent)
        accent = accent or theme.TEXT
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=title.upper(),
            font=theme.font(11, "bold"),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))

        self.value_label = ctk.CTkLabel(
            self, text=value, font=theme.font(26, "bold"), text_color=accent, anchor="w"
        )
        self.value_label.grid(row=1, column=0, sticky="w", padx=16, pady=(2, 0))

        self.trend_label = ctk.CTkLabel(
            self, text="", font=theme.font(11, "bold"),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.trend_label.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(10, 0))

        self.note_label = ctk.CTkLabel(
            self,
            text=note,
            font=theme.font(11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        )
        self.note_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))

    def set(self, value: str, note: str | None = None, trend=None) -> None:
        """``trend`` is ``(text, direction)`` where direction is -1, 0 or 1."""
        self.value_label.configure(text=value)
        if note is not None:
            self.note_label.configure(text=note)
        if trend is None:
            self.trend_label.configure(text="")
            return
        text, direction = trend
        colour = (
            theme.SUCCESS if direction > 0
            else theme.DANGER if direction < 0
            else theme.TEXT_MUTED
        )
        self.trend_label.configure(text=text, text_color=colour)


class DataTable(ctk.CTkFrame):
    """``ttk.Treeview`` wrapped so callers deal in row dicts and record ids.

    ``columns`` is a list of ``(key, heading, width, anchor)`` tuples. Rows are
    any mapping (``sqlite3.Row`` included); ``id_key`` names the field used as
    the item identifier returned by :meth:`selected_id`.
    """

    def __init__(self, parent, columns, id_key: str = "id", height: int = 12, **kwargs):
        kwargs.setdefault("fg_color", theme.SURFACE)
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER)
        super().__init__(parent, **kwargs)

        self.columns = columns
        self.id_key = id_key
        self._formatters = {}
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        theme.style_treeview(self)
        keys = [column[0] for column in columns]
        self.tree = ttk.Treeview(
            self,
            columns=keys,
            show="headings",
            height=height,
            style="RE4.Treeview",
            selectmode="browse",
        )
        for key, heading, width, anchor in columns:
            self.tree.heading(key, text=heading, anchor="w")
            self.tree.column(key, width=width, anchor=anchor, stretch=(anchor == "w"))

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scrollbar = ctk.CTkScrollbar(self, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=6)
        self.tree.configure(yscrollcommand=scrollbar.set)

        mode = "dark" if ctk.get_appearance_mode() == "Dark" else "light"
        for tag, palette in theme.ROW_TAGS.items():
            background, foreground = palette[mode]
            self.tree.tag_configure(tag, background=background, foreground=foreground)

        self._empty_label = ctk.CTkLabel(
            self, text="", font=theme.font(12), text_color=theme.TEXT_MUTED
        )

        # Says so when the list is only the beginning of what matched. It sits
        # below the rows and takes no space until there is something to say,
        # so a shop whose lists never reach the cap never sees it.
        self._note_label = ctk.CTkLabel(
            self, text="", font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w"
        )

    # -- data --------------------------------------------------------------- #

    def set_formatter(self, key: str, func) -> None:
        """Register ``func(value, row) -> str`` for one column."""
        self._formatters[key] = func

    def set_rows(self, rows, tag_func=None, empty_message: str = "Nothing here yet.") -> None:
        selected = self.selected_id()
        self.tree.delete(*self.tree.get_children())

        for row in rows:
            values = []
            for key, *_ in self.columns:
                value = _row_value(row, key)
                formatter = self._formatters.get(key)
                values.append(formatter(value, row) if formatter else _default_text(value))
            tags = tag_func(row) if tag_func else ()
            if isinstance(tags, str):
                tags = (tags,)
            self.tree.insert(
                "", "end", iid=str(row[self.id_key]), values=values, tags=tags or ()
            )

        if rows:
            self._empty_label.place_forget()
            if selected and self.tree.exists(selected):
                self.tree.selection_set(selected)
        else:
            self._empty_label.configure(text=empty_message)
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

        self._show_note(truncation_note(rows))

    def _show_note(self, text: str) -> None:
        if text:
            self._note_label.configure(text=text)
            self._note_label.grid(row=1, column=0, columnspan=2, sticky="ew",
                                  padx=12, pady=(0, 8))
        else:
            self._note_label.grid_remove()

    def update_row(self, row, tag_func=None) -> bool:
        """Rewrite one row already on screen. False if it is not there.

        A stock take scans a barcode at a time into a sheet that can run to a
        thousand lines, and rebuilding every one of them to change a single
        number is work the person holding the scanner waits for. The caller
        falls back to :meth:`set_rows` when False comes back, which is what
        happens when a filter means the row should now appear or disappear.
        """
        iid = str(row[self.id_key])
        if not self.tree.exists(iid):
            return False

        values = []
        for key, *_ in self.columns:
            value = _row_value(row, key)
            formatter = self._formatters.get(key)
            values.append(formatter(value, row) if formatter else _default_text(value))
        tags = tag_func(row) if tag_func else ()
        if isinstance(tags, str):
            tags = (tags,)
        self.tree.item(iid, values=values, tags=tags or ())
        return True

    # -- selection ---------------------------------------------------------- #

    def selected_id(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def selected_int(self) -> int | None:
        value = self.selected_id()
        return int(value) if value is not None else None

    def select_first(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])

    def on_double_click(self, callback) -> None:
        self.tree.bind("<Double-1>", lambda _event: callback())

    def on_select(self, callback) -> None:
        self.tree.bind("<<TreeviewSelect>>", lambda _event: callback())

    def on_return(self, callback) -> None:
        self.tree.bind("<Return>", lambda _event: callback())


def _row_value(row, key):
    """Read ``key`` from a sqlite3.Row, a dict, or a plain object."""
    if hasattr(row, "keys"):
        # sqlite3.Row has no __contains__, so .keys() is the correct test.
        return row[key] if key in row.keys() else ""  # noqa: SIM118
    return getattr(row, key, "")


def _default_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


class LabeledEntry(ctk.CTkFrame):
    """Entry with a caption, used across the forms."""

    def __init__(self, parent, label: str, value: str = "", show: str | None = None,
                 width: int = 220, placeholder: str = ""):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=label, font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w"
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.variable = ctk.StringVar(value=str(value))
        self.entry = ctk.CTkEntry(
            self,
            textvariable=self.variable,
            width=width,
            height=34,
            show=show,
            placeholder_text=placeholder,
        )
        self.entry.grid(row=1, column=0, sticky="ew")

    def get(self) -> str:
        return self.variable.get()

    def set(self, value) -> None:
        self.variable.set("" if value is None else str(value))

    def focus(self) -> None:
        self.entry.focus_set()


# --------------------------------------------------------------------------- #
# Modal dialogs
# --------------------------------------------------------------------------- #

class GrabsKeyboard:
    """Claims the keyboard shortly after a window appears, and lets go cleanly.

    Mix in before the Tk base class. Six dialogs across four modules had each
    written this out by hand, so a fix to one reached none of the others.

    Grabbing the moment the window is created races the window manager on
    Windows, so it is deferred. Deferring it means the window can be closed
    before it fires, and that is where the care is needed: ``destroy`` deletes
    the Tcl command behind the callback but leaves the timer standing, so Tcl
    keeps the appointment, finds nothing there, and writes "invalid command
    name" to stderr. Nothing breaks, and in a windowed build nobody sees it --
    which is the trouble, because a genuine Tk error looks exactly the same.

    It also means the ``winfo_exists`` check below can never be what saves us:
    by the time the window is gone, so is the command, and Tcl never reaches
    Python to ask. Cancelling on the way out is what the check was reaching
    for; it is kept only for a caller that invokes :meth:`_grab` directly.
    """

    #: Held on the class so a window whose __init__ raises part-way through
    #: still answers destroy() -- it is in its parent's children by then, and
    #: destroying the parent will call it.
    _grab_job: str | None = None

    def claim_keyboard(self, delay_ms: int = 80) -> None:
        self._grab_job = self.after(delay_ms, self._grab)

    def _grab(self) -> None:
        self._grab_job = None
        if not self.winfo_exists():
            return  # closed before the grab fired
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:  # pragma: no cover - window already gone
            pass

    def destroy(self) -> None:
        if self._grab_job is not None:
            try:
                self.after_cancel(self._grab_job)
            except tk.TclError:  # pragma: no cover - fired a moment ago
                pass
            self._grab_job = None
        super().destroy()


class Modal(GrabsKeyboard, ctk.CTkToplevel):
    """Base modal: centred on its parent, application-modal, Esc to cancel."""

    def __init__(self, parent, title: str, width: int = 460, height: int = 420):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self.geometry(f"{width}x{height}")
        self.update_idletasks()
        root = parent.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 3
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Escape>", lambda _event: self.on_cancel())

        self.claim_keyboard()

    def on_cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()

    def wait_result(self):
        self.wait_window()
        return self.result


FIELD_TYPES = ("entry", "password", "number", "text", "option", "check", "readonly")


class FormModal(Modal):
    """Builds a form from a field spec and returns a ``{key: value}`` dict.

    Each field is a dict with ``key``, ``label``, optional ``type`` (see
    :data:`FIELD_TYPES`), ``value``, ``values`` (for ``option``) and ``hint``.
    ``on_submit`` receives the collected values and may raise an exception whose
    message is shown to the user without closing the dialog.
    """

    def __init__(self, parent, title: str, fields, on_submit=None,
                 submit_text: str = "Save", width: int = 480):
        rows = len(fields)
        height = min(150 + rows * 74, 720)
        super().__init__(parent, title, width=width, height=height)
        self.fields = fields
        self.on_submit = on_submit
        self._widgets: dict[str, object] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=18, pady=(18, 6))
        body.grid_columnconfigure(0, weight=1)

        for index, field in enumerate(fields):
            self._build_field(body, index, field)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            footer, text="Cancel", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer, text=submit_text, width=140, height=36, command=self.submit
        ).grid(row=0, column=2)

        self.bind("<Return>", lambda _event: self.submit())
        self.after(120, self._focus_first)

    def _build_field(self, body, index: int, field: dict) -> None:
        key = field["key"]
        kind = field.get("type", "entry")
        label = field.get("label", key.replace("_", " ").title())
        value = field.get("value", "")

        wrapper = ctk.CTkFrame(body, fg_color="transparent")
        wrapper.grid(row=index, column=0, sticky="ew", pady=(0, 12))
        wrapper.grid_columnconfigure(0, weight=1)

        if kind != "check":
            ctk.CTkLabel(
                wrapper, text=label, font=theme.font(12),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).grid(row=0, column=0, sticky="ew", pady=(0, 4))

        if kind == "text":
            widget = ctk.CTkTextbox(wrapper, height=80)
            widget.insert("1.0", str(value or ""))
        elif kind == "option":
            variable = ctk.StringVar(value=str(value) if value else "")
            values = [str(v) for v in field.get("values", [])]
            if variable.get() not in values and values:
                variable.set(values[0])
            widget = ctk.CTkOptionMenu(wrapper, values=values or [""], variable=variable, height=34)
            widget.variable = variable
        elif kind == "check":
            variable = ctk.BooleanVar(value=bool(value))
            widget = ctk.CTkCheckBox(wrapper, text=label, variable=variable)
            widget.variable = variable
        else:
            variable = ctk.StringVar(value="" if value is None else str(value))
            widget = ctk.CTkEntry(
                wrapper,
                textvariable=variable,
                height=34,
                show="*" if kind == "password" else None,
                placeholder_text=field.get("placeholder", ""),
            )
            widget.variable = variable
            if kind == "readonly":
                widget.configure(state="disabled")

        widget.grid(row=1 if kind != "check" else 0, column=0, sticky="ew")

        if field.get("hint"):
            ctk.CTkLabel(
                wrapper, text=field["hint"], font=theme.font(11),
                text_color=theme.TEXT_MUTED, anchor="w", wraplength=400, justify="left",
            ).grid(row=2, column=0, sticky="ew", pady=(4, 0))

        self._widgets[key] = (kind, widget)

    def _focus_first(self) -> None:
        if not self.winfo_exists():
            return
        for _key, (kind, widget) in self._widgets.items():
            if kind in ("entry", "password", "number"):
                try:
                    widget.focus_set()
                except tk.TclError:  # pragma: no cover
                    pass
                return

    def values(self) -> dict:
        collected = {}
        for key, (kind, widget) in self._widgets.items():
            if kind == "text":
                collected[key] = widget.get("1.0", "end").strip()
            elif kind in ("option", "check"):
                collected[key] = widget.variable.get()
            else:
                collected[key] = widget.variable.get().strip()
        return collected

    def submit(self) -> None:
        values = self.values()
        if self.on_submit is not None:
            try:
                self.on_submit(values)
            except Exception as exc:  # noqa: BLE001 - shown to the user, dialog stays open
                show_error(self, exc, "Could not save")
                return
        self.result = values
        self.grab_release()
        self.destroy()
