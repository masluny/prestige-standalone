#!/usr/bin/env bash
# Build Prestige.app for macOS.
#
# Prerequisites (run once):
#   python3 -m venv venv
#   source venv/bin/activate
#   pip install -r requirements.txt
#   pip install pywebview pyinstaller
#
# Then any time you want to rebuild:
#   ./build_macos.sh
#
# Output: dist/Prestige.app  (double-clickable)

set -euo pipefail

cd "$(dirname "$0")"

# Activate venv if it exists, otherwise rely on whatever Python is on PATH.
if [ -f venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# Regenerate the .icns from the PNG logo. We first composite the trinity onto
# a macOS-style squircle (Ayu Mirage navy background) via Pillow so the Dock /
# Finder show the proper rounded shape, not a transparent square.
if [ -f webapp/static/logo.png ] && command -v iconutil >/dev/null 2>&1; then
  python3 build_assets/make_icon.py
  SQUIRCLE=build_assets/Prestige_icon_1024.png
  mkdir -p build_assets/Prestige.iconset
  for size in 16 32 64 128 256 512 1024; do
    sips -z "$size" "$size" "$SQUIRCLE" \
      --out "build_assets/Prestige.iconset/icon_${size}x${size}.png" >/dev/null
    half=$((size / 2))
    if [ "$size" -ge 32 ]; then
      sips -z "$half" "$half" "$SQUIRCLE" \
        --out "build_assets/Prestige.iconset/icon_${half}x${half}@2x.png" >/dev/null
    fi
  done
  iconutil -c icns -o build_assets/Prestige.icns build_assets/Prestige.iconset
fi

echo ">>> Building Prestige.app with PyInstaller..."
rm -rf build dist
pyinstaller Prestige.spec --clean --noconfirm

# Aggressively flush every layer of macOS icon caching. Without this,
# the Dock keeps showing the first version of the icon it ever indexed
# (regardless of how many times you rebuild the .app).
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREGISTER" ] && [ -d "dist/Prestige.app" ]; then
  echo ">>> Flushing macOS icon caches..."
  # 1) Per-user IconServices cache (the main culprit on Sequoia)
  rm -rf "$HOME/Library/Caches/com.apple.iconservices.store" 2>/dev/null || true
  # 2) The dock's own image cache
  find "$HOME/Library/Caches" -maxdepth 3 -name "com.apple.dock.iconcache" \
    -delete 2>/dev/null || true
  # 3) Bundle-level touch so Finder re-reads metadata
  touch "dist/Prestige.app"
  # 4) Re-register the bundle with LaunchServices (forces icon re-read)
  "$LSREGISTER" -f -r -u "$PWD/dist/Prestige.app" >/dev/null 2>&1 || true
  # 5) Restart Dock + Finder so they pick up the fresh icons
  killall Dock 2>/dev/null || true
  killall Finder 2>/dev/null || true
fi

echo
echo ">>> Done."
echo "    dist/Prestige.app  (open or drag to /Applications)"
echo
echo "If the Dock STILL shows the old square icon after all that,"
echo "drag the .app to a different folder (e.g. /Applications) once -"
echo "macOS keys icons by bundle path, so a new path forces a fresh read."
ls -la dist/Prestige.app/Contents/MacOS/ 2>/dev/null | head
