"""Saying things the way a person would say them.

The data layer is precise and the screens were showing it raw:
``2026-09-04 17:42:11`` in a column headed *When*, ``3`` next to a word that
might or might not need an ``s``, and a page called *Dashboard* that never once
addressed the person reading it.

None of that is wrong. It is just written in the machine's voice, and a shop is
a room with people in it — somebody opening up at seven, somebody covering a
lunch rush, somebody locking the door and wanting to know whether the day went
well. This module is the translation layer between the two: it takes what the
services know and phrases it the way the person in that room would.

Two rules keep it honest:

* **Never lose precision that matters.** A relative time is friendlier at a
  glance and useless when reconciling, so anything older than a couple of days
  keeps its date and clock, and receipts and reports are untouched — they still
  print the exact timestamp, because that is what a document is for.
* **Never invent warmth.** "Nothing needs your attention" is only printed when
  nothing does. Cheerfulness that is not backed by the data is worse than a
  blank column.
"""

from __future__ import annotations

import datetime as dt

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #

#: Past this many days a relative phrase stops helping and starts hiding things.
RELATIVE_DAYS = 2


def _parse(value) -> dt.datetime | None:
    """Best effort: SQLite gives us strings, callers sometimes give us objects."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("T", " ")
    for width, pattern in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return dt.datetime.strptime(text[:width], pattern)
        except ValueError:
            continue
    return None


def relative_time(value, now=None, *, empty: str = "—") -> str:
    """``2026-09-04 17:42:11`` as "20 minutes ago", "Yesterday 17:42", "12 Aug 17:42".

    Anything in the future is described plainly rather than as a negative age;
    a clock a few seconds out of step should not produce "in 0 minutes".
    """
    moment = _parse(value)
    if moment is None:
        return empty
    now = now or dt.datetime.now()

    seconds = (now - moment).total_seconds()
    if seconds < -60:
        return moment.strftime("%d %b %H:%M")
    if seconds < 45:
        return "Just now"
    if seconds < 90:
        return "A minute ago"

    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} minutes ago"

    hours = minutes / 60
    if moment.date() == now.date():
        return "An hour ago" if hours < 2 else f"{int(hours)} hours ago"
    if moment.date() == now.date() - dt.timedelta(days=1):
        return f"Yesterday {moment:%H:%M}"
    if (now.date() - moment.date()).days <= RELATIVE_DAYS:
        return f"{moment:%A} {moment:%H:%M}"
    if moment.year == now.year:
        return f"{moment:%d %b %H:%M}"
    return f"{moment:%d %b %Y}"


def day_label(value, now=None, *, empty: str = "—") -> str:
    """A date on its own: "Today", "Yesterday", "Tuesday", "12 Aug"."""
    moment = _parse(value)
    if moment is None:
        return empty
    today = (now or dt.datetime.now()).date()
    delta = (today - moment.date()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if 0 < delta <= 6:
        return moment.strftime("%A")
    if moment.year == today.year:
        return moment.strftime("%d %b")
    return moment.strftime("%d %b %Y")


def elapsed(value, now=None, *, empty: str = "—") -> str:
    """How long something has been going on: "4 hours", "25 minutes"."""
    moment = _parse(value)
    if moment is None:
        return empty
    seconds = max(0.0, ((now or dt.datetime.now()) - moment).total_seconds())
    minutes = int(seconds // 60)
    if minutes < 1:
        return "a moment"
    if minutes < 60:
        return plural(minutes, "minute")
    hours = minutes // 60
    if hours < 24:
        return plural(hours, "hour")
    return plural(hours // 24, "day")


def greeting(name: str = "", now=None) -> str:
    """"Good morning, Ali" — the shop opens early and closes late, so all four."""
    hour = (now or dt.datetime.now()).hour
    if hour < 5:
        part = "You're up late"
    elif hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"
    first = (name or "").strip().split(" ")[0]
    return f"{part}, {first}" if first else part


# --------------------------------------------------------------------------- #
# Counting and listing
# --------------------------------------------------------------------------- #

#: Words whose plural is not just an "s".
IRREGULAR = {
    "is": "are",
    "has": "have",
    "was": "were",
    "this": "these",
    "it": "they",
    "does": "do",
}


def plural(count: int, singular: str, plural_form: str = "") -> str:
    """``3, "product"`` -> "3 products"; ``1, "product"`` -> "1 product"."""
    if count == 1:
        return f"1 {singular}"
    return f"{count:,} {plural_form or IRREGULAR.get(singular) or singular + 's'}"


def verb(count: int, singular: str) -> str:
    """The verb that agrees with a count: ``1, "is"`` -> "is"; ``2, "is"`` -> "are"."""
    return singular if count == 1 else IRREGULAR.get(singular, singular)


def listing(items, joiner: str = "and", empty: str = "") -> str:
    """``["a", "b", "c"]`` -> "a, b and c". No Oxford comma; this is not a spec."""
    items = [str(item) for item in items if str(item).strip()]
    if not items:
        return empty
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {joiner} {items[-1]}"


def truncate(text, limit: int = 60) -> str:
    """Shorten to fit a column without cutting a word in half."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def name_or(value, fallback: str = "Walk-in") -> str:
    """A missing customer is a walk-in, not a blank cell."""
    text = str(value or "").strip()
    return text or fallback
