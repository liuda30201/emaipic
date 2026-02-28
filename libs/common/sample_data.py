from __future__ import annotations

from pathlib import Path

from libs.common.config import SAMPLES_ROOT


def _draw_invoice_like_ppm(path: Path, width: int, height: int, accent: tuple[int, int, int]) -> None:
    pixels = [[[245, 247, 250] for _ in range(width)] for _ in range(height)]

    def fill_rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        for y in range(max(y0, 0), min(y1, height)):
            row = pixels[y]
            for x in range(max(x0, 0), min(x1, width)):
                row[x] = [color[0], color[1], color[2]]

    def stroke_rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int], thickness: int = 2) -> None:
        fill_rect(x0, y0, x1, y0 + thickness, color)
        fill_rect(x0, y1 - thickness, x1, y1, color)
        fill_rect(x0, y0, x0 + thickness, y1, color)
        fill_rect(x1 - thickness, y0, x1, y1, color)

    def bar_row(start_y: int, count: int, color: tuple[int, int, int]) -> None:
        for index in range(count):
            y = start_y + index * 38
            fill_rect(90, y, 860, y + 6, color)
            fill_rect(90, y + 14, 560, y + 18, (180, 188, 198))
            fill_rect(650, y + 14, 820, y + 18, (210, 216, 223))

    fill_rect(0, 0, width, height, (247, 249, 252))
    fill_rect(50, 40, width - 50, 130, accent)
    fill_rect(80, 165, width - 80, height - 90, (255, 255, 255))
    stroke_rect(80, 165, width - 80, height - 90, (84, 97, 120), 3)
    fill_rect(110, 200, 320, 230, (225, 231, 239))
    fill_rect(110, 250, 650, 274, (198, 208, 222))
    fill_rect(690, 250, 850, 274, accent)
    fill_rect(110, 300, 850, 306, (120, 133, 156))
    fill_rect(110, 355, 850, 361, (120, 133, 156))
    bar_row(390, 4, (195, 202, 213))
    fill_rect(580, 570, 850, 640, (233, 247, 239))
    stroke_rect(580, 570, 850, 640, (63, 127, 86), 2)
    fill_rect(110, 590, 420, 610, (186, 194, 205))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii") as handle:
        handle.write("P3\n")
        handle.write(f"{width} {height}\n255\n")
        for row in pixels:
            handle.write(" ".join(f"{r} {g} {b}" for r, g, b in row))
            handle.write("\n")


def ensure_mock_mailbox_assets(base_dir: Path | None = None) -> list[Path]:
    mailbox_root = (base_dir or SAMPLES_ROOT / "mailbox").resolve()
    attachments_dir = mailbox_root / "attachments"
    outputs = [
        attachments_dir / "invoice_alpha.ppm",
        attachments_dir / "invoice_beta.ppm",
    ]
    colors = [(31, 111, 235), (200, 94, 32)]
    for output, color in zip(outputs, colors):
        if not output.exists():
            _draw_invoice_like_ppm(output, 960, 720, color)
    return outputs
