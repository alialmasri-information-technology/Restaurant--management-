# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for RE4.

The database is deliberately NOT bundled: `app/config.py` creates `re4.db` next
to the executable on first run. Bundling it would place it in PyInstaller's
temporary extraction directory, which is deleted on exit — taking every sale
with it.

CustomTkinter ships JSON theme files and assets that must be collected, or the
frozen app dies at import with a missing-theme error.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("customtkinter")

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
)
