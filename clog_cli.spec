# -*- mode: python ; coding: utf-8 -*-
"""CLI build spec: clog-cli.exe (console, no PySide6).

Run: pyinstaller clog_cli.spec
Output: dist/clog-cli.exe
"""
import os
import sys

# make the in-tree `clog` package importable so collect_submodules can see it
sys.path.insert(0, os.path.abspath('src'))

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []
for pkg in ('core', 'db', 'cli'):
    hiddenimports += collect_submodules(pkg)

tmp = collect_all('gallery_dl')
datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]


a = Analysis(
    ['clog_cli.py'],
    pathex=['.', 'src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'shiboken6'],
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
    name='clog-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
