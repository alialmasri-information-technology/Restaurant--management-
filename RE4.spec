# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for RE4.

The database is deliberately NOT bundled: `app/config.py` creates `re4.db` next
to the executable on first run. Bundling it would place it in PyInstaller's
temporary extraction directory, which is deleted on exit — taking every sale
with it.

CustomTkinter ships JSON theme files and assets that must be collected, or the
frozen app dies at import with a missing-theme error.
"""

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("customtkinter")


def _app_version() -> str:
    """Read APP_VERSION out of app/config.py without importing the app.

    Importing it would drag in the whole package during the build for the sake
    of one string. A regex over the source is enough, and it fails loudly if
    the constant is ever renamed rather than quietly stamping the wrong number
    on the executable.
    """
    source = Path(SPECPATH, "app", "config.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"$', source, re.MULTILINE)
    if match is None:
        raise SystemExit("RE4.spec: could not find APP_VERSION in app/config.py")
    return match.group(1)


def _version_resource():
    """The Windows version resource, built from APP_VERSION.

    Without it the file's Properties -> Details tab is blank, which is where
    anyone deploying to more than one till looks first, and where any tool that
    decides whether an update is needed reads from. Writing the number here
    rather than in a checked-in template is what keeps it from drifting: there
    is one place to bump, and it is the same place the application reads when
    it reports its own version.

    Only Windows carries such a resource. PyInstaller ignores the argument
    elsewhere, but the module that builds it is Windows-only, so the import has
    to be guarded too.
    """
    if sys.platform != "win32":
        return None

    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version = _app_version()
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(
            f"RE4.spec: APP_VERSION is {version!r}; the version resource needs "
            "three numbers separated by dots"
        )
    # Windows counts to four. The fourth is a build number this project does
    # not keep, so it stays at zero rather than being invented.
    numbers = tuple(int(part) for part in parts) + (0,)

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,      # NT, Windows32
            fileType=0x1,    # an application
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                # 0409 is US English, 04B0 is Unicode: the pair the strings
                # below are declared in, and the pair VarFileInfo repeats.
                StringTable("040904B0", [
                    StringStruct("CompanyName", "RE4"),
                    # Task Manager shows this beside the process, so it is the
                    # line that has to make sense to a shop, not to a packager.
                    StringStruct("FileDescription", "RE4 — Business & Retail Management"),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", "RE4"),
                    StringStruct("OriginalFilename", "RE4.exe"),
                    StringStruct("ProductName", "RE4"),
                    StringStruct("ProductVersion", version),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [0x409, 1200])]),
        ],
    )
    # LegalCopyright is deliberately absent: the repository states no licence
    # and names no holder, and a version resource is the wrong place to invent
    # one.

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "customtkinter",
        "reportlab",
        "reportlab.pdfgen",
        "reportlab.pdfbase._fontdata",
        # Barcode symbologies are looked up by name at runtime, so PyInstaller
        # cannot see the import and would leave label printing broken.
        "reportlab.graphics.barcode",
        "reportlab.graphics.barcode.code128",
        "reportlab.graphics.barcode.common",
        "sqlite3",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
        # Product thumbnails come through Pillow, which CustomTkinter also uses.
        "PIL.Image",
        "PIL.ImageTk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "matplotlib", "PIL.ImageQt"],
    # Backups, images and logs are written next to the executable at runtime for
    # the same reason as the database, so none of those folders are bundled.
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RE4",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_resource(),
)
