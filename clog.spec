# -*- mode: python ; coding: utf-8 -*-
"""GUI build spec: clog.exe (windowed, includes PySide6).

Onedir mode — hydrus-style layout:

    dist/clog/
    ├── clog.exe        # entry, ~6 MB
    └── lib/            # all bundled binaries / DLLs / data (renamed from _internal)
        ├── PySide6/...
        ├── gui/themes/light.qss, dark.qss
        └── ...

User data (db/, clog.json, clog.log) is written next to clog.exe on first run,
NOT bundled here. So `dist/clog/db/` only appears after first launch.

Run: pyinstaller clog.spec
"""
import os
import sys

# make the in-tree packages importable so collect_submodules can see them
sys.path.insert(0, os.path.abspath('src'))

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []
for pkg in ('core', 'db', 'cli', 'gui'):
    hiddenimports += collect_submodules(pkg)

for pkg in ('gallery_dl', 'PySide6', 'shiboken6'):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# bundle the qss themes alongside the executable
datas += [('src/gui/themes/light.qss', 'gui/themes'),
          ('src/gui/themes/dark.qss',  'gui/themes')]


a = Analysis(
    ['clog_gui.py'],
    pathex=['.', 'src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],                          # no binaries here → onedir
    exclude_binaries=True,
    contents_directory='lib',    # hydrus-style: dist/clog/lib/ (default would be _internal)
    name='clog',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # UPX on PySide6 DLLs slows build a lot, helps little
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='clog',              # → dist/clog/
)
