"""One worker thread for the jobs that must not freeze the shop.

Tk repaints only between callbacks, so anything long-running done inside a
button press — a conversation with a printer that is asleep, a backup of a
database grown over years, PowerShell waking up to list printers — leaves the
window grey and the till dead for as long as it takes. The jobs here run on a
single daemon thread and hand their outcome back through a queue, which the
event loop drains a hundred times a second; the shop keeps answering the
scanner while the printer thinks.

Two boundaries keep this safe:

* **Only UI work happens on the UI thread.** The job function may touch the
  database — SQLite connections here are per-thread, so a worker simply opens
  its own — but it must never build a window, and the callbacks run *after*
  the outcome has crossed back.
* **Nothing that owns the main thread's connection goes on the worker.**
  Restoring a backup closes and reopens the caller's connection, which is
  thread-local by design; restore therefore stays on the UI thread, brief as
  it is.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk

from app import logs

#: How often the event loop looks for finished work. A tenth of a second is
#: invisible to a person reading the screen.
POLL_MS = 100

#: What the cursor reads while a background job is running.
BUSY_CURSOR = "watch"

_outcomes: queue.Queue = queue.Queue()
_pumping = False


def start(widget) -> None:
    """Begin draining finished work onto ``widget``'s event loop."""
    global _pumping
    if _pumping:
        return
    _pumping = True
    _pump(widget)


def run(job, on_done=None, on_error=None, *, parent=None) -> None:
    """Run ``job`` on the worker thread; deliver the outcome on the UI thread.

    ``on_done`` receives what the job returned; a failure is delivered to
    ``on_error``, or to a plain error dialog when none is given. The cursor
    reads as busy over ``parent``'s window until the outcome is delivered,
    which is the whole of the progress a person needs from a job that is
    simply *going*.
    """
    window = parent.winfo_toplevel() if parent is not None else None
    if window is not None:
        try:
            window.configure(cursor=BUSY_CURSOR)
        except tk.TclError:  # pragma: no cover - window already gone
            pass

    def deliver(handler, payload) -> None:
        def on_ui() -> None:
            if window is not None:
                try:
                    window.configure(cursor="")
                except tk.TclError:  # pragma: no cover
                    pass
            try:
                handler(payload)
            except Exception:  # noqa: BLE001 - a bad callback is logged, not fatal
                logs.exception("Background callback failed")
        _outcomes.put(on_ui)

    def work() -> None:
        try:
            result = job()
        except Exception as exc:  # noqa: BLE001 - delivered, never raised into Tcl
            deliver(on_error if on_error is not None else _report_error, exc)
        else:
            deliver(on_done if on_done is not None else _ignore, result)

    threading.Thread(target=work, name="re4-worker", daemon=True).start()


def _pump(widget) -> None:
    while True:
        try:
            handler = _outcomes.get_nowait()
        except queue.Empty:
            break
        try:
            handler()
        except Exception:  # noqa: BLE001 - never let the pump itself die
            logs.exception("Background delivery failed")
    try:
        widget.after(POLL_MS, lambda: _pump(widget))
    except tk.TclError:  # the window is gone; there is nothing left to deliver
        pass


def _ignore(_result) -> None:
    pass


def _report_error(exc: Exception) -> None:
    from app.ui.widgets import show_error

    show_error(None, exc, "That did not finish")


def busy(parent) -> None:
    """Mark ``parent``'s window busy for a job that stays on the UI thread.

    Two jobs stay here. Restoring a backup must own the calling thread,
    because it closes and reopens that thread's database connection; the CSV
    import reads and writes through a modal the person is looking at, and
    moving it off the thread would only let them press the button twice.
    """
    window = parent.winfo_toplevel()
    try:
        window.configure(cursor=BUSY_CURSOR)
        window.update_idletasks()
    except tk.TclError:  # pragma: no cover
        pass


def settled(parent) -> None:
    """Release a busy mark taken with :func:`busy`."""
    window = parent.winfo_toplevel()
    try:
        window.configure(cursor="")
    except tk.TclError:  # pragma: no cover
        pass
