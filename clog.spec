# -*- mode: python ; coding: utf-8 -*-
"""GUI build spec: clog executable (windowed, includes PySide6).

Onedir mode — hydrus-style layout:

    dist/clog/
    ├── clog.exe        # entry, ~6 MB
    └── lib/            # all bundled binaries / DLLs / data (renamed from _internal)
        ├── PySide6/...
        ├── gui/themes/light.qss, dark.qss
        └── ...

User data (db/, clog.json, clog.log) is written next to the main executable on
first run,
NOT bundled here. So `dist/clog/db/` only appears after first launch.

Run: pyinstaller clog.spec
"""
import os
import sys
from fnmatch import fnmatch

# make the in-tree packages importable so collect_submodules can see them
sys.path.insert(0, os.path.abspath('src'))

from PyInstaller.utils.hooks import collect_all, collect_submodules

WINDOWS_ICON = 'static/app.ico' if sys.platform == 'win32' else 'NONE'

PYSIDE6_EXCLUDES = [
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    'PySide6.QtAsyncio',
    'PySide6.QtAxContainer',
    'PySide6.QtBluetooth',
    'PySide6.QtCanvasPainter',
    'PySide6.QtCharts',
    'PySide6.QtConcurrent',
    'PySide6.QtDataVisualization',
    'PySide6.QtDBus',
    'PySide6.QtDesigner',
    'PySide6.QtGraphs',
    'PySide6.QtGraphsWidgets',
    'PySide6.QtHelp',
    'PySide6.QtHttpServer',
    'PySide6.QtLocation',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtNetwork',
    'PySide6.QtNetworkAuth',
    'PySide6.QtNfc',
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtPositioning',
    'PySide6.QtPrintSupport',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtQuick3D',
    'PySide6.QtQuickControls2',
    'PySide6.QtQuickTest',
    'PySide6.QtQuickWidgets',
    'PySide6.QtRemoteObjects',
    'PySide6.QtScxml',
    'PySide6.QtSensors',
    'PySide6.QtSerialBus',
    'PySide6.QtSerialPort',
    'PySide6.QtSpatialAudio',
    'PySide6.QtSql',
    'PySide6.QtStateMachine',
    'PySide6.QtSvgWidgets',
    'PySide6.QtTest',
    'PySide6.QtTextToSpeech',
    'PySide6.QtUiTools',
    'PySide6.QtWebChannel',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebSockets',
    'PySide6.QtWebView',
    'PySide6.QtXml',
]

PYSIDE6_FILE_EXCLUDE_PATTERNS = (
    '*.lib',
    '*.pyi',
    'assistant.exe',
    'balsam.exe',
    'balsamui.exe',
    'designer.exe',
    'linguist.exe',
    'lrelease.exe',
    'lupdate.exe',
    'qmlcachegen.exe',
    'qmlformat.exe',
    'qmlimportscanner.exe',
    'qmllint.exe',
    'qmlls.exe',
    'qmltyperegistrar.exe',
    'qsb.exe',
    'QtWebEngineProcess.exe',
    'rcc.exe',
    'svgtoqml.exe',
    'uic.exe',
    'OCI.dll',
    'py.typed',
    'PySide6_Addons.json',
    'PySide6_Essentials.json',
    'pyside6qml.abi3.dll',
    'Qt3D*.pyd',
    'QtAxContainer.pyd',
    'QtBluetooth.pyd',
    'QtCanvasPainter.pyd',
    'QtCharts.pyd',
    'QtConcurrent.pyd',
    'QtDataVisualization.pyd',
    'QtDBus.pyd',
    'QtDesigner.pyd',
    'QtGraphs*.pyd',
    'QtHelp.pyd',
    'QtHttpServer.pyd',
    'QtLocation.pyd',
    'QtMultimedia*.pyd',
    'QtNetwork.pyd',
    'QtNetworkAuth.pyd',
    'QtNfc.pyd',
    'QtOpenGL*.pyd',
    'QtPdf*.pyd',
    'QtPositioning.pyd',
    'QtPrintSupport.pyd',
    'QtQml*.pyd',
    'QtQuick*.pyd',
    'QtRemoteObjects.pyd',
    'QtScxml.pyd',
    'QtSensors.pyd',
    'QtSerial*.pyd',
    'QtSpatialAudio.pyd',
    'QtSql.pyd',
    'QtStateMachine.pyd',
    'QtSvgWidgets.pyd',
    'QtTest.pyd',
    'QtTextToSpeech.pyd',
    'QtUiTools.pyd',
    'QtWeb*.pyd',
    'QtXml.pyd',
    'Qt63D*',
    'Qt6Bluetooth*',
    'Qt6CanvasPainter*',
    'Qt6Charts*',
    'Qt6Concurrent*',
    'Qt6DataVisualization*',
    'Qt6DBus*',
    'Qt6Designer*',
    'Qt6Graphs*',
    'Qt6Help*',
    'Qt6HttpServer*',
    'Qt6Labs*',
    'Qt6Location*',
    'Qt6Multimedia*',
    'Qt6NetworkAuth*',
    'Qt6Nfc*',
    'Qt6OpenGL*',
    'Qt6Pdf*',
    'Qt6Positioning*',
    'Qt6PrintSupport*',
    'Qt6Qml*',
    'Qt6Quick*',
    'Qt6RemoteObjects*',
    'Qt6Scxml*',
    'Qt6Sensors*',
    'Qt6Serial*',
    'Qt6ShaderTools*',
    'Qt6SpatialAudio*',
    'Qt6Sql*',
    'Qt6StateMachine*',
    'Qt6SvgWidgets*',
    'Qt6Test*',
    'Qt6TextToSpeech*',
    'Qt6UiTools*',
    'Qt6VirtualKeyboard*',
    'Qt6WebChannel*',
    'Qt6WebEngine*',
    'Qt6WebSockets*',
    'Qt6WebView*',
    'avcodec-*.dll',
    'avformat-*.dll',
    'avutil-*.dll',
    'swresample-*.dll',
    'swscale-*.dll',
)

PYSIDE6_PATH_EXCLUDE_PATTERNS = (
    'PySide6/doc/*',
    'PySide6/glue/*',
    'PySide6/include/*',
    'PySide6/lib/*',
    'PySide6/metatypes/*',
    'PySide6/qml/*',
    'PySide6/QtAsyncio/*',
    'PySide6/resources/*',
    'PySide6/scripts/*',
    'PySide6/support/*',
    'PySide6/translations/qtwebengine*',
    'PySide6/typesystems/*',
)

PYSIDE6_PLUGIN_DIR_EXCLUDES = {
    'assetimporters',
    'canbus',
    'designer',
    'generic',
    'geometryloaders',
    'geoservices',
    'multimedia',
    'networkinformation',
    'platforminputcontexts',
    'position',
    'qmllint',
    'qmltooling',
    'renderers',
    'renderplugins',
    'sceneparsers',
    'scxmldatamodel',
    'sensors',
    'sqldrivers',
    'texttospeech',
    'webview',
}

PYSIDE6_PLUGIN_FILE_EXCLUDES = {
    'qsqlibase.dll',
    'qsqlmimer.dll',
    'qsqlmysql.dll',
    'qsqloci.dll',
    'qsqlodbc.dll',
    'qsqlpsql.dll',
}


def _normalize_path(value: str) -> str:
    return value.replace('\\', '/')


def _should_exclude_pyside6_entry(entry) -> bool:
    first = _normalize_path(entry[0])
    second = _normalize_path(entry[1]) if len(entry) > 1 else ''

    if first.startswith('PySide6/'):
        target = first
        src = second
    else:
        src = first
        dest = second
        target = f'{dest}/{os.path.basename(src)}' if dest else os.path.basename(src)

    basename = os.path.basename(target)

    if not target.startswith('PySide6/'):
        return False

    if any(fnmatch(target, pattern) for pattern in PYSIDE6_PATH_EXCLUDE_PATTERNS):
        return True

    if '/plugins/' in target:
        plugin_rel = target.split('/plugins/', 1)[1]
        plugin_dir = plugin_rel.split('/', 1)[0]
        if plugin_dir in PYSIDE6_PLUGIN_DIR_EXCLUDES:
            return True
        if basename.lower() in PYSIDE6_PLUGIN_FILE_EXCLUDES:
            return True

    return any(fnmatch(basename, pattern) for pattern in PYSIDE6_FILE_EXCLUDE_PATTERNS)


def _filter_pyside6_entries(entries):
    return [entry for entry in entries if not _should_exclude_pyside6_entry(entry)]

datas = []
binaries = []
hiddenimports = []
for pkg in ('core', 'db', 'cli', 'gui'):
    hiddenimports += collect_submodules(pkg)

for pkg in ('gallery_dl', 'shiboken6'):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

pyside6_datas, pyside6_binaries, _ = collect_all(
    'PySide6',
    include_py_files=False,
    filter_submodules=lambda name: False,
    on_error='ignore',
)
datas += _filter_pyside6_entries(pyside6_datas)
binaries += _filter_pyside6_entries(pyside6_binaries)

# bundle the qss themes alongside the executable
datas += [('src/gui/themes/light.qss', 'gui/themes'),
          ('src/gui/themes/dark.qss',  'gui/themes')]

# bundle the icon resource set (see static/spec.txt)
datas += [
    ('static/app.ico',         'static'),
    ('static/app.png',         'static'),
    ('static/app-dark.svg',    'static'),
    ('static/app-light.svg',   'static'),
    ('static/app-splash.png',  'static'),
]


a = Analysis(
    ['clog_gui.py'],
    pathex=['.', 'src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=PYSIDE6_EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = _filter_pyside6_entries(a.binaries)
a.datas = _filter_pyside6_entries(a.datas)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],                          # no binaries here → onedir
    exclude_binaries=True,
    contents_directory='lib',    # hydrus-style: dist/clog/lib/ (default would be _internal)
    name='clog',
    icon=WINDOWS_ICON,
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
