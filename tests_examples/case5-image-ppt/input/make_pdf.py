"""Synthesize a tiny 4-page image-PPT PDF for case 5.

We're not testing PDF rendering quality — we're testing that the CLI can
take a multi-page PDF, ask the server to convert pages to images via
``files import --type image --from-pdf``, run image-question generation
and image-dataset generation, and end up with a text-only QA dataset.

Each page is a slide-style flat-color image with a title, a fake "chart"
(3 colored bars with numeric labels) and a footer caption — enough visual
structure for a vision LLM to make non-trivial observations about each
page individually.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

W, H = 1024, 768

PAGES = [
    {
        "title": "AI+EDU 2025: Market Size",
        "bars": [("2023", 120, (66, 133, 244)),
                 ("2024", 180, (52, 168, 83)),
                 ("2025", 270, (251, 188, 5))],
        "footer": "USD billions, projected",
        "bg": (245, 247, 250),
    },
    {
        "title": "Top 3 Use Cases",
        "bars": [("Tutoring", 220, (234, 67, 53)),
                 ("Grading",  140, (66, 133, 244)),
                 ("Content",  300, (52, 168, 83))],
        "footer": "Adoption index, 0-400",
        "bg": (255, 250, 240),
    },
    {
        "title": "Regional Adoption Rate",
        "bars": [("APAC",  85, (52, 168, 83)),
                 ("EMEA",  62, (66, 133, 244)),
                 ("AMER",  74, (251, 188, 5))],
        "footer": "Percent of K-12 schools",
        "bg": (240, 248, 255),
    },
    {
        "title": "Headwinds 2026",
        "bars": [("Privacy", 78, (234, 67, 53)),
                 ("Cost",    55, (251, 188, 5)),
                 ("Trust",   90, (66, 133, 244))],
        "footer": "Concern index, 0-100",
        "bg": (250, 245, 255),
    },
]


def draw_page(spec: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), spec["bg"])
    d = ImageDraw.Draw(img)
    # Title
    d.rectangle((0, 0, W, 90), fill=(40, 40, 40))
    d.text((40, 30), spec["title"], fill=(255, 255, 255))
    # Bars
    base_y = H - 180
    bar_w = 160
    gap = 80
    start_x = (W - (bar_w * 3 + gap * 2)) // 2
    max_v = max(v for _, v, _ in spec["bars"])
    chart_h = 380
    for i, (label, value, color) in enumerate(spec["bars"]):
        x0 = start_x + i * (bar_w + gap)
        h = int(chart_h * value / max_v)
        d.rectangle((x0, base_y - h, x0 + bar_w, base_y), fill=color)
        d.text((x0 + 20, base_y + 10), label, fill=(40, 40, 40))
        d.text((x0 + 20, base_y - h - 30), str(value), fill=(40, 40, 40))
    # Footer
    d.text((40, H - 40), spec["footer"], fill=(120, 120, 120))
    return img


def main() -> None:
    out = Path(__file__).parent / "ai-edu-report.pdf"
    pages = [draw_page(p) for p in PAGES]
    pages[0].save(out, save_all=True, append_images=pages[1:], format="PDF")
    print(f"wrote {out} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
