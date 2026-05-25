"""
core/runtime.py - external-tool path overrides.

The editor needs two external binaries to provide everything it offers:

  * `dot`  (Graphviz)  - the graph view
  * `java` (any JDK)   - HermiT and Pellet reasoners

In dev mode they're picked up from $PATH. In the standalone .app, the user
might have them installed but the .app's PATH doesn't see them (Finder /
Dock launches strip $PATH down to /usr/bin:/bin etc.). This module:

  * stores user-supplied overrides for each tool;
  * resolves a tool path = override -> PATH lookup -> common Homebrew
    locations;
  * persists the overrides to a JSON file (so the user only sets them
    once);
  * applies `JAVA_EXE` to owlready2 so HermiT / Pellet pick it up.

The module is import-safe even when owlready2 is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional


# Auto-PATH augmentation: locations where users commonly install dot/java
# but which aren't on $PATH for GUI-launched apps on macOS.
COMMON_BIN_DIRS = [
    "/opt/homebrew/bin",                       # Apple Silicon Homebrew
    "/opt/homebrew/sbin",
    "/usr/local/bin",                          # Intel Homebrew / MacPorts
    "/usr/local/sbin",
    "/opt/homebrew/opt/openjdk/bin",           # `brew install openjdk`
    "/Library/Java/JavaVirtualMachines",       # macOS .pkg JDK installs (we
                                               # auto-discover Contents/Home/bin)
    "/opt/local/bin",                          # MacPorts
]


# In-memory overrides set via set_override()/load_from()/configure().
_OVERRIDES: Dict[str, Optional[str]] = {"dot": None, "java": None}


# ---------------------------------------------------------------------------
# PATH augmentation
# ---------------------------------------------------------------------------

def augment_path() -> None:
    """Add common Homebrew/system bin dirs to $PATH if they exist.

    Idempotent; safe to call multiple times. Also walks
    /Library/Java/JavaVirtualMachines/* and adds each JDK's Contents/Home/bin
    so a vanilla .pkg-installed Temurin/Adoptium works without configuration.
    """
    parts = os.environ.get("PATH", "").split(os.pathsep)
    parts_set = set(parts)
    additions = []
    for d in COMMON_BIN_DIRS:
        p = Path(d)
        if d == "/Library/Java/JavaVirtualMachines":
            if p.is_dir():
                for jdk in p.glob("*/Contents/Home/bin"):
                    s = str(jdk)
                    if jdk.is_dir() and s not in parts_set:
                        additions.append(s); parts_set.add(s)
            continue
        if p.is_dir() and d not in parts_set:
            additions.append(d); parts_set.add(d)
    if additions:
        os.environ["PATH"] = os.pathsep.join(parts + additions)


# ---------------------------------------------------------------------------
# Overrides + resolution
# ---------------------------------------------------------------------------

def set_override(name: str, path: Optional[str]) -> None:
    """Set or clear a tool override. Empty/None clears it."""
    if name not in _OVERRIDES:
        raise ValueError("unknown tool override %r" % name)
    p = (path or "").strip()
    _OVERRIDES[name] = p or None
    if name == "java":
        _apply_java_to_owlready()


def get_override(name: str) -> Optional[str]:
    return _OVERRIDES.get(name)


def get_overrides() -> Dict[str, Optional[str]]:
    return dict(_OVERRIDES)


def resolve(name: str) -> Optional[str]:
    """Return the best path for `name`, or None if nothing works.

    Tried in order:
      1. user override (if it points at an executable file)
      2. shutil.which() against current PATH (which may have been augmented)
    """
    ov = _OVERRIDES.get(name)
    if ov:
        p = Path(ov).expanduser()
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p)
    return shutil.which(name)


def sibling_in_same_dir(name: str, sibling: str) -> Optional[str]:
    """Resolve `sibling` assuming it lives next to the override for `name`.

    Used for Graphviz: if the user supplied `/opt/homebrew/bin/dot`, then
    `sfdp`, `neato`, etc. are at the same path with a different filename.
    Falls back to PATH lookup if no override is set.
    """
    ov = _OVERRIDES.get(name)
    if ov:
        candidate = Path(ov).expanduser().with_name(sibling)
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return shutil.which(sibling)


# ---------------------------------------------------------------------------
# Status check (used by the Settings UI to show ✓ / ✗ next to each path)
# ---------------------------------------------------------------------------

def status(name: str) -> dict:
    """Diagnose a tool: which path is in effect, is it actually runnable."""
    info = {
        "override": _OVERRIDES.get(name),
        "resolved": resolve(name),
        "working":  False,
        "version":  None,
        "error":    None,
    }
    path = info["resolved"]
    if not path:
        info["error"] = "not found on PATH"
        return info
    try:
        if name == "dot":
            r = subprocess.run([path, "-V"], capture_output=True, timeout=8)
            out = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
        elif name == "java":
            r = subprocess.run([path, "-version"], capture_output=True, timeout=15)
            out = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
            out = out.splitlines()[0] if out else ""
        else:
            return info
        if r.returncode == 0:
            info["working"] = True
            info["version"] = out
        else:
            info["error"] = out or "exit %d" % r.returncode
    except Exception as exc:
        info["error"] = str(exc)
    return info


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_from(json_path: os.PathLike) -> None:
    """Load overrides from a JSON file. Missing file = no-op."""
    p = Path(json_path)
    if not p.is_file():
        return
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return
    for k in list(_OVERRIDES.keys()):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            _OVERRIDES[k] = v.strip()
    _apply_java_to_owlready()


def save_to(json_path: os.PathLike) -> None:
    """Persist current overrides to a JSON file."""
    p = Path(json_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in _OVERRIDES.items() if v}, fh, indent=2)


# ---------------------------------------------------------------------------
# owlready2 wiring
# ---------------------------------------------------------------------------

def _apply_java_to_owlready() -> None:
    """If owlready2 is imported, point its JAVA_EXE at our resolved java.

    Reasoners (HermiT, Pellet) spawn `java` via owlready2.JAVA_EXE; the
    default is the string "java", which fails inside the standalone .app
    because Finder-launched apps don't see Homebrew's PATH."""
    try:
        import owlready2          # noqa
    except Exception:
        return
    j = resolve("java")
    if j:
        try:
            owlready2.JAVA_EXE = j
        except Exception:
            pass


def configure(json_path: Optional[os.PathLike] = None) -> None:
    """Convenience: augment PATH, load saved overrides, push to owlready2.

    Called by the standalone launcher (and by the web server's lifespan)."""
    augment_path()
    if json_path is not None:
        load_from(json_path)
    _apply_java_to_owlready()
