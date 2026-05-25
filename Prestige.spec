# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Prestige — packages standalone.py into a desktop app.

  pyinstaller Prestige.spec --clean --noconfirm

On macOS this produces:
  dist/Prestige.app          - double-clickable .app bundle
  dist/Prestige/             - the underlying onedir layout

The .app bundles the Python interpreter, every Python dep, and every
static file under webapp/static/. External tools (Graphviz `dot`, the
`java` runtime for HermiT/Pellet) are NOT bundled; if absent they are
gracefully reported as unavailable inside the app.
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()

# ---- data files shipped inside the bundle -----------------------------
datas = [
    # webapp/static/** -> webapp/static/ (preserves the relative layout
    # so server.py finds index.html via Path(__file__).parent / "static")
    (str(ROOT / "webapp" / "static"), "webapp/static"),
]

# Pull *all* of owlready2 in (submodules + bundled HermiT.jar + pellet jars
# + the C extension). Without this PyInstaller's analysis only catches the
# top-level import, so reasoning silently fails at runtime.
_ow_datas, _ow_bin, _ow_hidden = collect_all("owlready2")
datas      += _ow_datas
binaries    = list(_ow_bin)
extra_hidden = list(_ow_hidden) + collect_submodules("owlready2")

# ---- modules PyInstaller's static analysis misses ---------------------
hiddenimports = [
    # Our own packages - uvicorn looks them up by string ("webapp.server:app")
    # so PyInstaller's static analysis can miss them without explicit help.
    "webapp",
    "webapp.server",
    "core",
    "core.ofn",
    "core.model",
    "core.examtools",
    "core.graphview",
    "core.reasoner",
    "core.runtime",
    # uvicorn workers / lifespans
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.logging",
    # multipart upload parser used by /api/load
    "python_multipart",
    "multipart",
    # owlready2 is optional but if installed PyInstaller must follow it.
    # The big collect_submodules() call above pulls in every submodule;
    # the bare top-level names below are belt-and-braces.
    "owlready2",
    "owlready2.driver",
    "owlready2_optimized",
    # pywebview platform back-ends. Cocoa on macOS, others harmless if unused.
    "webview",
    "webview.platforms.cocoa",
] + extra_hidden

block_cipher = None


a = Analysis(
    ["standalone.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",        # we don't use it
        "test",           # stdlib test package, bloats the bundle
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Prestige",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                  # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,               # current host arch (arm64 on Apple Silicon)
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "build_assets" / "Prestige.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Prestige",
)

# ---- macOS .app bundle (only built when running on Darwin) ------------
app = BUNDLE(
    coll,
    name="Prestige.app",
    icon=str(ROOT / "build_assets" / "Prestige.icns"),
    bundle_identifier="com.prestige.ontologyeditor",
    version="1.1.8",
    info_plist={
        "CFBundleName":              "Prestige",
        "CFBundleDisplayName":       "Prestige",
        "CFBundleShortVersionString": "1.1.8",
        "CFBundleVersion":           "1.1.8",
        "NSHighResolutionCapable":   True,
        "NSRequiresAquaSystemAppearance": False,   # respect dark mode
        "LSMinimumSystemVersion":    "11.0",
        "NSHumanReadableCopyright":  "MIT License",
        # Register the .owl extension as a document type the app handles
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName":      "OWL Ontology",
            "CFBundleTypeRole":      "Editor",
            "LSItemContentTypes":    ["public.data"],
            "CFBundleTypeExtensions": ["owl", "ofn"],
        }],
    },
)
