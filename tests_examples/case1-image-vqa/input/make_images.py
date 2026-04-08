"""Synthesize 4 small flat-color "vehicle" images for the VQA test.

We're not testing image quality — we're testing that the CLI can wire a
directory of images through `files import --type image` and that a vision
model can answer free-form questions about them. Solid colors with text
labels are enough for the LLM to identify "red car", "blue truck", etc.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


VEHICLES = [
    ("red_sedan.png",   (220, 50, 47),  "RED SEDAN"),
    ("blue_truck.png",  (38, 139, 210), "BLUE TRUCK"),
    ("green_van.png",   (88, 161, 73),  "GREEN VAN"),
    ("yellow_taxi.png", (220, 200, 50), "YELLOW TAXI"),
]


def main() -> None:
    out_dir = Path(__file__).parent / "vehicles"
    out_dir.mkdir(exist_ok=True)
    for fname, color, label in VEHICLES:
        img = Image.new("RGB", (256, 192), color)
        draw = ImageDraw.Draw(img)
        # Crude car silhouette so the label isn't the only signal
        draw.rounded_rectangle((30, 90, 226, 150), radius=20, fill=(40, 40, 40))
        draw.ellipse((50, 130, 90, 170), fill=(20, 20, 20))
        draw.ellipse((166, 130, 206, 170), fill=(20, 20, 20))
        draw.text((30, 30), label, fill=(255, 255, 255))
        img.save(out_dir / fname)
        print(f"  wrote {out_dir / fname}")
    print(f"done: {len(VEHICLES)} images in {out_dir}")


if __name__ == "__main__":
    main()
