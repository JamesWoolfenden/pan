"""One-off: render the app's PWA icons from the same accent color used in ui/style.css. Not a
runtime dependency - Pillow isn't in pyproject.toml, run this via `uv run --with pillow python
scripts/generate_icons.py` and commit the resulting PNGs instead of regenerating them on every
install."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (31, 138, 111)  # --accent from ui/style.css
TEXT = (255, 255, 255)

OUT_DIR = Path(__file__).resolve().parent.parent / "ui" / "icons"


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render(size: int, corner_radius_ratio: float = 0.22) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * corner_radius_ratio), fill=ACCENT)

    # "pan" centered, sized/positioned to stay within a maskable icon's ~80% safe zone.
    font = _font(int(size * 0.42))
    text = "pan"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - text_w) / 2 - bbox[0], (size - text_h) / 2 - bbox[1]), text, font=font, fill=TEXT)
    return img


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        render(size).save(OUT_DIR / f"icon-{size}.png")
        print(f"wrote {OUT_DIR / f'icon-{size}.png'}")
