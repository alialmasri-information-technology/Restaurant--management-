"""Is there a newer copy of RE4 to install?

A till is often offline, and a shop should never be interrupted by a question
it cannot answer — so every failure here is quiet, the answer is cached for
the day, and the check itself runs on the background worker. When a newer
release exists, the shell shows one strip with a button, and that is the
whole of the nagging anyone gets.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import urllib.error
import urllib.request

from app import config

#: Set in a process that must not phone home. The test suite drives real
#: screens, and a shell built in a test fires a real check on the background
#: worker — which lands whenever it lands, writing its cache rows into
#: whichever database happens to be current by then. The switch makes check()
#: a quiet no-op so a test process never talks to the network or races itself.
TEST_SWITCH = "RE4_SKIP_UPDATE_CHECK"

#: Ask at most once a day. A shop that opens once does not need to be told
#: twice that it is current.
CHECK_INTERVAL_DAYS = 1

CACHE_CHECKED_AT = "update_last_check"
CACHE_VERSION = "update_latest_version"
CACHE_URL = "update_latest_url"


def current() -> tuple[int, ...]:
    """This running version, as comparable numbers: 2.5.0 -> (2, 5, 0)."""
    parts = []
    for piece in config.APP_VERSION.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def parse(version: str) -> tuple[int, ...] | None:
    """A release tag as numbers, or None when it is not one."""
    cleaned = version.strip().lstrip("vV")
    parts = []
    for piece in cleaned.split("."):
        if not piece.isdigit():
            return None
        parts.append(int(piece))
    return tuple(parts) if parts else None


def is_newer(candidate: str) -> bool:
    """True when ``candidate`` is a release newer than the running version."""
    theirs = parse(candidate)
    return theirs is not None and theirs > current()


def latest_release() -> tuple[str, str] | None:
    """(version, page URL) of the newest published release, or None.

    None means no release, an unreachable network, a rate limit, or anything
    else a till cannot do anything about — every one of which is the same
    message: carry on.

    Being offline is the easy case and was the only one handled. The hard case
    is a connection that answers but not with a release: the captive portal in
    a mall or a shared building, which intercepts the request and replies with
    a login page, a truncated body or a malformed status line. Those raise from
    http.client, which is neither OSError nor ValueError, so they used to leave
    here as exceptions — and a check nobody asked for became a dialog on a till.
    """
    url = f"https://api.github.com/repos/{config.REPO_SLUG}/releases/latest"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.load(response)
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException):
        return None
    if not isinstance(data, dict):
        # Well-formed JSON that is not a release: a portal can answer with a
        # list or a bare string, and asking either one for a tag raises.
        return None
    tag = str(data.get("tag_name") or "")
    page = str(data.get("html_url") or "")
    if not is_newer(tag) or not page:
        return None
    return tag.lstrip("vV"), page


def check(settings_service) -> tuple[str, str] | None:
    """The newest release worth mentioning, from cache or the network.

    ``settings_service`` is passed rather than imported so tests can hand in a
    stub. The cache is a courtesy to the network, not to the shop: a machine
    that opens six times a day asks GitHub once.
    """
    if os.environ.get(TEST_SWITCH):
        return None

    today = dt.date.today().isoformat()
    if settings_service.get(CACHE_CHECKED_AT, "") == today:
        version = settings_service.get(CACHE_VERSION, "")
        page = settings_service.get(CACHE_URL, "")
        return (version, page) if version and page else None

    found = latest_release()
    settings_service.set_many({
        CACHE_CHECKED_AT: today,
        CACHE_VERSION: found[0] if found else "",
        CACHE_URL: found[1] if found else "",
    })
    return found
