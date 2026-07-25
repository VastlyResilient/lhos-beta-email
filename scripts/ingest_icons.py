#!/usr/bin/env python3
"""Ingest GPT Image 2.0 icon PNGs -> transparent, normalized, multi-size IRIS icon set.

Usage:  python3 scripts/ingest_icons.py /path/to/folder-of-pngs
Expects files named: iris-eye.png pulse.png layers.png doc.png arrow-ahead.png
check-circle.png database.png link.png clock.png shield-cloud.png doc-spark.png
clock-poll.png plane.png bell.png refresh.png clock-key.png shield-heal.png info.png
"""
import sys, os
from pathlib import Path
from PIL import Image

NAMES = ["iris-eye","pulse","layers","doc","arrow-ahead","check-circle","database",
         "link","clock","shield-cloud","doc-spark","clock-poll","plane","bell",
         "refresh","clock-key","shield-heal","info"]
ROOT   = Path(__file__).resolve().parent.parent
RASTER = ROOT / "assets" / "icons" / "raster"
SIZES  = [64, 128, 256]
CANVAS = 256          # master normalized canvas
CONTENT = 0.78        # icon occupies 78% of canvas => consistent optical size

def dechroma(im: Image.Image) -> Image.Image:
    """Remove flat magenta chroma key, de-fringe edges."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            # magenta = high R, high B, low G
            if r > 90 and b > 90 and g < min(r, b) * 0.62:
                mag = (r + b) / 2.0
                alpha = max(0.0, min(1.0, (mag - g) / max(mag, 1)))
                if alpha > 0.90:
                    px[x, y] = (0, 0, 0, 0)
                else:
                    # partial edge: unmix the magenta spill, keep the line
                    k = 1.0 - alpha
                    px[x, y] = (int(r * k + g * alpha), g, int(b * k + g * alpha),
                                int(255 * (1.0 - alpha)))
    return im

def normalize(im: Image.Image) -> Image.Image:
    """Trim to content, scale to a consistent optical size, center on square canvas."""
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    target = int(CANVAS * CONTENT)
    w, h = im.size
    scale = target / max(w, h)
    im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(im, ((CANVAS - im.width) // 2, (CANVAS - im.height) // 2), im)
    return canvas

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = Path(sys.argv[1]).expanduser()
    RASTER.mkdir(parents=True, exist_ok=True)
    found, missing = [], []
    for name in NAMES:
        cands = [src / f"{name}.png"] + sorted(src.glob(f"*{name}*.png"))
        f = next((c for c in cands if c.exists()), None)
        if not f:
            missing.append(name); continue
        icon = normalize(dechroma(Image.open(f)))
        icon.save(RASTER / f"{name}.png")
        icon.save(RASTER / f"{name}.webp", "WEBP", quality=92, method=6)
        for s in SIZES:
            icon.resize((s, s), Image.LANCZOS).save(RASTER / f"{name}-{s}.png")
        found.append(name)
        print(f"  ok  {name}  <- {f.name}")
    print(f"\nprocessed {len(found)}/18 -> {RASTER}")
    if missing:
        print("MISSING:", ", ".join(missing))
        sys.exit(2)

if __name__ == "__main__":
    main()
