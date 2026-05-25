# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Prestige — cross-platform variant (Windows + Linux).

  pyinstaller Prestige-cross.spec --clean --noconfirm

The macOS .app bundle uses a different spec (`Prestige.spec`) that adds a
BUNDLE step. This file is identical otherwise but:

  * has no BUNDLE block (BUNDLE is macOS-only)
  * picks the icon by platform (.ico on Windows, none on Linux)

GitHub Actions (.github/workflows/release.yml) runs THIS spec on
windows-latest and ubuntu-latest; macos-* runners keep using
`Prestige.spec` as before.
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()
IS_WIN   = sys.platform.startswith("win")

# ---- data files shipped inside the bundle -----------------------------
datas = [
    (str(ROOT / "webapp" / "static"), "webapp/static"),
]

# Pull all of owlready2 (submodules + bundled HermiT.jar + pellet jars).
_ow_datas, _ow_bin, _ow_hidden = collect_all("owlready2")
datas       += _ow_datas
binaries     = list(_ow_bin)
extra_hidden = list(_ow_hidden) + collect_submodules("owlready2")

hiddenimports = [
    # Our packages — uvicorn references them by string at runtime.
    "webapp", "webapp.server",
    "core", "core.ofn", "core.model", "core.examtools",
    "core.graphview", "core.reasoner", "core.runtime",
    # uvicorn internals
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.logging",
    # multipart upload parser used by /api/load
    "python_multipart", "multipart",
    # owlready2 — collect_submodules above grabs the rest
    "owlready2", "owlready2.driver", "owlready2_optimized",
    # pywebview platform back-ends
    "webview",
    "webview.platforms.winforms",       # Windows
    "webview.platforms.edgechromium",   # Windows (Edge WebView2)
    "webview.platforms.gtk",            # Linux
    "webview.platforms.qt",             # Linux fallback
] + extra_hidden

# Icon: Windows wants .ico, Linux ignores the icon arg.
icon_path = str(ROOT / "build_assets" / "Prestige.ico") if IS_WIN else None

block_cipher = None

a = Analysis(
    ["standalone.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "test"],
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
    console=False,                # windowed app, no terminal pop-up
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
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
