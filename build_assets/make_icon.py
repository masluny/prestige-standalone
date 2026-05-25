#!/usr/bin/env python3
"""
Render the Prestige app icon as a macOS-style squircle on a navy
Ayu-Mirage background, with the trinity logo centered inside.

Produces:
  build_assets/Prestige_icon_1024.png  (square 1024x1024, RGBA)

`build_macos.sh` then feeds that PNG to sips + iconutil to make the .icns.

macOS uses a superellipse ("squircle") with exponent ~5 for app icons. We
approximate it with a rounded rectangle whose corner radius ~22% of the
canvas - visually identical at icon sizes.
"""

from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path


CANVAS    = 1024
# macOS leaves transparent margin around the squircle so the OS doesn't
# blow it up bigger than other Dock icons. Apple's official template
# spec is 824/1024 (≈80%), but in practice most third-party apps render
# closer to ~880-900 px - and at 824 our icon looked visibly smaller
# than the neighbours. 900 px (with 62 px margin) lands the icon at
# the same visual size as the rest of the Dock.
SQUIRCLE  = 900
INSET     = (CANVAS - SQUIRCLE) // 2       # 62 px on each side
RADIUS    = round(SQUIRCLE * 0.2235)        # ~201 px corner radius
LOGO_PCT  = 1.00         # the source logo already has a dark frame around
                         # the trinity, so we let it fill the whole
                         # squircle area and just clip to the rounded shape
BG_COLOR  = (22, 26, 34, 255)        # Ayu Mirage desk  #161a22 (fallback)

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SOURCE_LOGO = HERE / "logo_source.png"     # original square art (pristine)
APP_LOGO    = PROJECT / "webapp" / "static" / "logo.png"  # shown in-app
OUT_PNG     = HERE / "Prestige_icon_1024.png"             # feeds the .icns


def _squircle_mask(size: int, radius: int) -> Image.Image:
    """Anti-aliased rounded-rectangle alpha mask."""
    # Draw 4x larger then downsample for cleaner edges.
    big = size * 4
    rad = radius * 4
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=rad, fill=255)
    return m.resize((size, size), Image.LANCZOS)


def main() -> None:
    # Use the pristine source if present (kept stable across rebuilds);
    # otherwise fall back to the current in-app logo as the source. The
    # first time this runs we'll seed `logo_source.png` from the in-app
    # logo so we never lose the original square artwork.
    if SOURCE_LOGO.is_file():
        src = SOURCE_LOGO
    elif APP_LOGO.is_file():
        src = APP_LOGO
        try:
            Image.open(APP_LOGO).save(SOURCE_LOGO, "PNG")
        except Exception:
            pass
    else:
        raise SystemExit("No logo found: %s or %s" % (SOURCE_LOGO, APP_LOGO))

    logo = Image.open(src).convert("RGBA")

    # 1) Render the squircle at SQUIRCLE x SQUIRCLE (smaller than canvas):
    #    a) navy background tile sized to the squircle
    #    b) trinity composited over it (fills the squircle)
    #    c) clipped with a rounded-rect mask of the same size
    bg = Image.new("RGBA", (SQUIRCLE, SQUIRCLE), BG_COLOR)
    target = int(SQUIRCLE * LOGO_PCT)
    logo_resized = logo.resize((target, target), Image.LANCZOS)
    bx = (SQUIRCLE - target) // 2
    by = (SQUIRCLE - target) // 2
    bg.alpha_composite(logo_resized, (bx, by))

    mask = _squircle_mask(SQUIRCLE, RADIUS)
    squircle_layer = Image.new("RGBA", (SQUIRCLE, SQUIRCLE), (0, 0, 0, 0))
    squircle_layer.paste(bg, (0, 0), mask)

    # 2) Soft inner shadow along the squircle edge for a touch of depth
    shadow = Image.new("RGBA", (SQUIRCLE, SQUIRCLE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((0, 0, SQUIRCLE - 1, SQUIRCLE - 1),
                         radius=RADIUS, outline=(0, 0, 0, 90), width=4)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
    squircle_layer.alpha_composite(shadow)

    # 3) Paste the smaller squircle into a transparent 1024 canvas with
    #    the standard macOS margin on every side. This is what makes the
    #    Dock render it at the same size as every other app icon.
    rounded = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    rounded.alpha_composite(squircle_layer, (INSET, INSET))

    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    rounded.save(OUT_PNG, "PNG")
    print("Wrote %s (%dx%d), squircle %dpx centred with %dpx margin"
          % (OUT_PNG, CANVAS, CANVAS, SQUIRCLE, INSET))

    # Mirror the squircle into the in-app static asset so the brand logo,
    # the Dock running icon (pywebview's start(icon=...)) and the .icns
    # all show identical artwork.
    APP_LOGO.parent.mkdir(parents=True, exist_ok=True)
    rounded.save(APP_LOGO, "PNG")
    print("Mirrored to %s" % APP_LOGO)


if __name__ == "__main__":
    main()
