"""
Makes the app icons from the artwork in static/icons/icebox-source.jpg (a blue rounded square on white: a snowflake over an
open box with a gear and a tick). The files a phone / browser needs are different from the picture: the system rounds the corners
itself, so the home-screen icons must be a FULL square of blue (no white corners), the artwork smaller inside it so a round or
squircle mask never cuts it, and the tab icon can be bigger and simpler.

    .venv/Scripts/python.exe scripts/make_icons.py

Writes static/icons/*.png and favicon.ico. Safe to run again (e.g. after replacing the source picture; keep the same layout, or
change ART below to the new artwork's box).
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent / "static" / "icons"
SRC = HERE / "icebox-source.jpg"
ART = (292, 244, 732, 780)          # the snowflake + box in the 1024 px source (left, top, right, bottom)
BLUE = (62, 150, 215)               # the flat blue of the source


def icon(size: int, art_share: float, *, rounded: float = 0.0) -> Image.Image:
    """A size x size icon: the blue field with the artwork `art_share` of its height, centred. `rounded` = corner radius share (0 = full square)."""
    src = Image.open(SRC).convert("RGB").crop(ART)
    h = round(size * art_share)
    w = round(src.width * h / src.height)
    art = src.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), BLUE)
    canvas.paste(art, ((size - w) // 2, (size - h) // 2))
    if not rounded:
        return canvas
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=int(size * 4 * rounded), fill=255)
    out = canvas.convert("RGBA")
    out.putalpha(mask.resize((size, size), Image.LANCZOS))
    return out


def main() -> None:
    icon(512, 0.66).save(HERE / "icon-512.png")
    icon(192, 0.66).save(HERE / "icon-192.png")
    icon(512, 0.54).save(HERE / "icon-maskable-512.png")       # Android may cut a circle: keep the picture well inside
    icon(180, 0.70).save(HERE / "apple-touch-icon.png")        # iPhone: full square, iOS rounds it
    icon(32, 0.86, rounded=0.22).save(HERE / "favicon-32.png")
    icon(16, 0.90, rounded=0.2).save(HERE / "favicon-16.png")
    icon(48, 0.86, rounded=0.22).save(HERE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("icons written to", HERE)


if __name__ == "__main__":
    main()
