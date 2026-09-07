"""One shop, one copy of the register.

Two copies of RE4 against one database is not a concurrency feature, it is a
corruption lottery: both tills selling the last unit, both backups pruning each
other's files, one restore running under the other's sale. Windows is told with
a named mutex, which the operating system releases even if the process dies
ugly; everywhere else an advisory lock on a file next to the database does the
same job.
"""

from __future__ import annotations

import sys

_mutex = None


def acquire() -> bool:
    """Mark this process as the only copy allowed to run. False if one already is."""
    global _mutex
    if _mutex is not None:  # this process already holds the door
        return True

    from app import config, logs

    if sys.platform.startswith("win"):
        import ctypes

        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\RE4-SingleInstance")
        if not handle:  # pragma: no cover - cannot refuse what we cannot test
            return True
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _mutex = handle
        return True

    import fcntl

    path = config.DATA_DIR / "re4.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("a+")
    except OSError:  # pragma: no cover - unwritable data dir fails later anyway
        logs.warning(
            "Could not open the lock file in %s, so a second copy of RE4 cannot "
            "be detected", path.parent,
        )
        return True
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _mutex = handle
    return True


def release() -> None:
    """Give the door up. Windows does this by itself when the process exits."""
    global _mutex
    if _mutex is None:
        return
    if sys.platform.startswith("win"):
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_mutex)
    else:
        import fcntl

        try:
            fcntl.flock(_mutex, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover - already gone with the process
            pass
        _mutex.close()
    _mutex = None
