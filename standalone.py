#!/usr/bin/env python3
"""
standalone.py - launch Prestige as a native desktop window.

Differences from `app.py`:
  * picks a free local port automatically (no clash with another tab)
  * starts uvicorn in a daemon thread
  * opens the UI in a native pywebview window (no browser, no chrome)
  * exits cleanly when the window is closed
  * resolves resource paths under PyInstaller (sys._MEIPASS), so this same
    script works in `python3 standalone.py` *and* in a packaged .app

If pywebview is unavailable (e.g. running headless or on an unsupported
platform) the script falls back to opening the default browser.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path


# ---------------------------------------------------------------------------
# Crash logging: if anything explodes before we have a console (or pywebview
# crashes silently), append the traceback to a file the user can find.
# ---------------------------------------------------------------------------
def _setup_crash_log(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def excepthook(exc_type, exc, tb):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("\n===== %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                fh.write("Platform: %s  Python: %s\n"
                         % (sys.platform, sys.version.split()[0]))
                traceback.print_exception(exc_type, exc, tb, file=fh)
        except Exception:
            pass
        # Also print to stderr so the (now-visible) console shows it.
        traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = excepthook
    # Thread crashes don't go through sys.excepthook by default - hook the
    # threading module's handler too.
    try:
        threading.excepthook = lambda args: excepthook(
            args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Resource directory resolution (dev mode + PyInstaller one-file bundle)
# ---------------------------------------------------------------------------
def _resource_root() -> Path:
    """Folder that contains `webapp/`, `core/`, etc.

    Inside a PyInstaller bundle the resources are unpacked to ``sys._MEIPASS``.
    In dev mode they sit next to this file."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent


def _writable_home() -> Path:
    """Per-user writable folder for working ontologies, backups, etc.

    Inside the bundle we cannot write to the read-only resource folder, so
    we keep working state under ``~/Library/Application Support/Prestige``
    (macOS), ``~/.local/share/Prestige`` (Linux), or
    ``%APPDATA%/Prestige`` (Windows)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Prestige"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Prestige"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME",
                                   str(Path.home() / ".local" / "share"))) / "Prestige"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _pick_free_port() -> int:
    """Bind ephemeral, then close, returning the OS-assigned port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Bootstrap module path + working directory before importing webapp.server
# ---------------------------------------------------------------------------
ROOT = _resource_root()
WORK = _writable_home()

# Start crash logging immediately - anything that explodes after this point
# (import errors, missing DLLs, pywebview crashes) gets written to:
#   macOS:   ~/Library/Application Support/Prestige/prestige_crash.log
#   Windows: %APPDATA%\Prestige\prestige_crash.log
#   Linux:   ~/.local/share/Prestige/prestige_crash.log
_setup_crash_log(WORK / "prestige_crash.log")

# Make sibling packages importable both in dev mode and from the bundle.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tell the server to keep working state (the active ontology, backups,
# uploaded files) in the per-user writable folder, not next to the .app.
os.environ["PRESTIGE_WORKDIR"] = str(WORK)

# Chdir into the writable folder so any relative paths inside the server
# resolve under it (e.g. the "ontology.owl" save target).
os.chdir(WORK)

# Augment PATH with common Homebrew / JDK install locations, then load any
# previously-saved Settings overrides for `dot` / `java`. Without this the
# .app (launched from Finder with a minimal PATH) cannot find Graphviz or
# Java, so the graph view + HermiT/Pellet would silently fail.
from core import runtime as _runtime           # noqa: E402
_runtime.configure(WORK / "runtime_paths.json")

import uvicorn  # noqa: E402

# Explicitly import the FastAPI app so PyInstaller's static analysis can
# follow the full dependency graph (uvicorn would otherwise look it up by
# string name at runtime, which doesn't help the packager).
import webapp.server as _server  # noqa: E402,F401
import core.ofn         # noqa: E402,F401
import core.model       # noqa: E402,F401
import core.examtools   # noqa: E402,F401
import core.graphview   # noqa: E402,F401
import core.reasoner    # noqa: E402,F401
import core.runtime     # noqa: E402,F401

HOST = "127.0.0.1"
PORT = _pick_free_port()


def _wait_until_ready(timeout: float = 8.0) -> bool:
    """Poll the loopback socket until uvicorn accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _serve():
    """uvicorn target — runs forever in a daemon thread."""
    # The standard `uvicorn.run("module:app")` re-imports the module by
    # string name, which works inside the bundle as long as the resource
    # root is on sys.path (we set that up above).
    config = uvicorn.Config(
        "webapp.server:app",
        host=HOST, port=PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


# ---------------------------------------------------------------------------
# JS-side API: opens a native macOS Save panel and writes the bytes the
# browser is trying to download. Exposed to JS as window.pywebview.api.*.
#
# Needed because WKWebView (which pywebview uses on macOS) can't trigger
# real downloads from <a download> links - the click silently opens a new
# WebView window showing the binary, and closing that window kills the app.
# ---------------------------------------------------------------------------
class _JSApi:
    @staticmethod
    def _file_type_filter(filename):
        ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
        return {
            "png":  "PNG image (*.png)",
            "svg":  "SVG vector image (*.svg)",
            "pdf":  "PDF document (*.pdf)",
            "owl":  "OWL ontology (*.owl)",
            "ofn":  "OWL Functional Syntax (*.ofn)",
            "txt":  "Text file (*.txt)",
            "md":   "Markdown file (*.md)",
            "csv":  "CSV file (*.csv)",
            "json": "JSON file (*.json)",
        }.get(ext, "All files (*.*)")

    @staticmethod
    def _open_save_dialog(filename):
        """Show NSSavePanel; return the chosen absolute path or "" if
        the user cancelled."""
        import webview
        try:
            win = webview.windows[0]
        except (IndexError, AttributeError):
            return ""
        result = win.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename,
            file_types=(_JSApi._file_type_filter(filename),
                        "All files (*.*)"))
        if not result:
            return ""
        return result[0] if isinstance(result, (list, tuple)) else result

    def save_file(self, filename, b64_data):
        """Show a Save panel and write `b64_data` (base-64) to whatever
        path the user picks. Returns the path, or "" on cancel."""
        import base64
        path = self._open_save_dialog(filename)
        if not path:
            return ""
        try:
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(b64_data))
        except Exception as exc:                          # pragma: no cover
            print("save_file failed:", exc, file=sys.stderr)
            return ""
        return str(path)

    def pick_save_path(self, filename):
        """Show a Save panel and return the chosen path WITHOUT writing
        anything. Used by the Save button so the server side can handle
        the actual write (and create a proper .bak of any existing file
        at that location)."""
        return self._open_save_dialog(filename)


# ---------------------------------------------------------------------------
# Cocoa titlebar tweak: make the macOS title bar (with the traffic-light
# buttons) the same colour as the app background, so the whole window
# looks like one continuous dark island.
# ---------------------------------------------------------------------------
def _patch_cocoa_titlebar():
    """Monkey-patch pywebview's BrowserView so every new NSWindow gets a
    navy, draggable title bar that matches our app background.

    Implementation choice: we do NOT set NSFullSizeContentView. If we did,
    the WKWebView would extend over the title bar area and *eat all mouse
    events there* (WKWebView consumes events; `-webkit-app-region: drag` is
    a Chromium-only CSS, not a Safari WebKit feature; and
    `setMovableByWindowBackground` only helps when the window background
    is actually visible - which it isn't under the WebView). The cleanest
    way to keep a draggable title bar is to let it live as its own native
    strip ABOVE the WebView, and just colour-match it to the app.

    Steps:
      1) Make the title bar appear transparent so the window's own bg
         colour shows through.
      2) Hide the title text ("Prestige - OWL ontology editor").
      3) Set the NSWindow background colour to navy.
      4) Re-tint the title bar subview to navy too, because pywebview's
         own __init__ explicitly sets it to NSColor.windowBackgroundColor()
         (system grey) right after the window is built.

    Safe to call on non-macOS platforms (it just no-ops)."""
    if sys.platform != "darwin":
        return
    try:
        from webview.platforms import cocoa
        import AppKit
        from AppKit import NSColor
    except Exception:                                     # pragma: no cover
        return

    NS_WINDOW_TITLE_HIDDEN = 1                            # NSWindowTitleHidden
    # Ayu Mirage desk colour  #161a22
    NAVY = NSColor.colorWithCalibratedRed_green_blue_alpha_(
        0x16 / 255.0, 0x1a / 255.0, 0x22 / 255.0, 1.0)

    def _restyle(nswin):
        try:
            nswin.setTitlebarAppearsTransparent_(True)
            nswin.setTitleVisibility_(NS_WINDOW_TITLE_HIDDEN)
            nswin.setBackgroundColor_(NAVY)
            # Re-tint the actual titlebar subview - pywebview's own __init__
            # set this to NSColor.windowBackgroundColor() (system grey).
            try:
                titlebar_view = (nswin.contentView().superview()
                                       .subviews().lastObject())
                if titlebar_view is not None \
                        and titlebar_view.respondsToSelector_(
                            b"setBackgroundColor:"):
                    titlebar_view.setBackgroundColor_(NAVY)
            except Exception:
                pass
        except Exception as exc:                          # pragma: no cover
            print("titlebar restyle failed:", exc, file=sys.stderr)

    original_init = cocoa.BrowserView.__init__

    def patched_init(self, window):
        original_init(self, window)
        # `window.frameless` already strips the titlebar - nothing to retint.
        if getattr(window, "frameless", False):
            return
        ns = getattr(self, "window", None)
        if ns is not None:
            _restyle(ns)

    cocoa.BrowserView.__init__ = patched_init


def main() -> None:
    print("Prestige  ->  http://%s:%d/" % (HOST, PORT))
    print("Working directory: %s" % WORK)
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    if not _wait_until_ready():
        print("ERROR: backend never came up", file=sys.stderr)
        sys.exit(1)

    url = "http://%s:%d/" % (HOST, PORT)

    # Prefer the native window. Fall back to the browser if pywebview is
    # missing or fails (e.g. in CI or headless containers).
    try:
        import webview
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        # block on the server thread so the process stays alive
        t.join()
        return

    # Apply the unified-titlebar styling *before* creating the window so
    # the patched __init__ catches it.
    _patch_cocoa_titlebar()

    # Per-platform window icon for `webview.start(icon=...)`:
    #   macOS   -> pass our squircle PNG; pywebview/NSApp.setApplicationIconImage
    #              uses it for the Dock running-icon (so the live Dock icon
    #              matches the bundle .icns instead of flipping to a square)
    #   Windows -> pass NOTHING; pywebview tries to convert the file to a
    #              System.Drawing.Icon which only accepts .ico and CRASHES
    #              on PNG. The window still inherits the .exe's embedded
    #              icon (set in the spec via icon=Prestige.ico), which IS
    #              already our squircle.
    #   Linux   -> pass NOTHING; pywebview's GTK backend gets the icon from
    #              the .desktop file or window manager.
    icon_arg = None
    if sys.platform == "darwin":
        squircle = ROOT / "build_assets" / "Prestige_icon_1024.png"
        png = squircle if squircle.exists() else (
            ROOT / "webapp" / "static" / "logo.png")
        if png.exists():
            icon_arg = str(png)

    webview.create_window(
        "Prestige - OWL ontology editor",
        url=url,
        width=1400, height=900,
        min_size=(960, 640),
        background_color="#161a22",
        js_api=_JSApi(),
    )
    # `webview.start()` blocks until the window is closed by the user.
    try:
        webview.start(icon=icon_arg, private_mode=False)
    except TypeError:
        # Older pywebview versions don't accept the icon argument here.
        webview.start(private_mode=False)


if __name__ == "__main__":
    main()
