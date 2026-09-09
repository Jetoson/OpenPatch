"""Regenerates every derived logo asset from assets/op_logo.png.
Run after changing the master artwork:
    python assets/generate_logo_assets.py
"""

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "op_logo.png"
DASH = ROOT / "server" / "dashboard" / "assets"
AGENT = ROOT / "agent" / "assets"

DASHBOARD_BANNER_WIDTH = 340
FAVICON_SIZE = 128

ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def save_width(img: Image.Image, width: int, path: Path) -> None:
    height = round(img.height * width / img.width)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((width, height), Image.LANCZOS).save(path, optimize=True)
    print(f"  {path.relative_to(ROOT)}  {width}x{height}  {path.stat().st_size // 1024}KB")


def square(img: Image.Image, size: int) -> Image.Image:
    fitted = img.copy()
    fitted.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return canvas


def main() -> int:
    if not MASTER.exists():
        print(f"master artwork not found: {MASTER}")
        return 1

    src = Image.open(MASTER).convert("RGBA")
    src = src.crop(src.getbbox())  # strip the transparent padding
    w, h = src.size
    print(f"master content: {w}x{h}")

    mark = src.crop((int(w * 0.22), int(h * 0.13), int(w * 0.82), h))
    print(f"shield mark:    {mark.size[0]}x{mark.size[1]}")

    print("dashboard:")
    save_width(src, DASHBOARD_BANNER_WIDTH, DASH / "logo_full.png")
    favicon = DASH / "logo_mark.png"
    favicon.parent.mkdir(parents=True, exist_ok=True)
    square(mark, FAVICON_SIZE).save(favicon, optimize=True)
    print(f"  {favicon.relative_to(ROOT)}  {FAVICON_SIZE}x{FAVICON_SIZE}  {favicon.stat().st_size // 1024}KB")

    print("agent:")
    ico = AGENT / "logo.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    square(mark, 256).save(ico, sizes=ICO_SIZES)
    print(f"  {ico.relative_to(ROOT)}  multi-res  {ico.stat().st_size // 1024}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
