"""Regenerates app_icon.ico (run: python make_icon.py). Needs Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def build() -> Image.Image:
    # diagonal violet -> cyan gradient
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    a, b = (124, 108, 255), (34, 211, 238)
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.23), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = S // 2
    shaft_w = int(S * 0.17)
    d.rectangle((cx - shaft_w // 2, int(S * 0.17), cx + shaft_w // 2, int(S * 0.52)), fill=white)
    d.polygon([(cx - int(S * 0.27), int(S * 0.46)), (cx + int(S * 0.27), int(S * 0.46)), (cx, int(S * 0.72))], fill=white)
    bar_h = int(S * 0.095)
    d.rounded_rectangle((int(S * 0.2), int(S * 0.77), int(S * 0.8), int(S * 0.77) + bar_h), radius=bar_h // 2, fill=white)
    return img


if __name__ == "__main__":
    out = Path(__file__).with_name("app_icon.ico")
    master = build()
    master.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    master.resize((256, 256), Image.LANCZOS).save(out.with_name("app_icon_preview.png"))
    print("wrote", out)
