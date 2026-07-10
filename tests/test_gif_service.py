from __future__ import annotations

import pytest

Image = pytest.importorskip("PIL.Image")

from app.services.gif_service import remove_white_background  # noqa: E402


def test_remove_white_background_makes_white_pixels_transparent(tmp_path):
    src = tmp_path / "spinner.gif"
    frame = Image.new("RGBA", (8, 8), (255, 255, 255, 255))
    for x in range(3, 5):
        for y in range(3, 5):
            frame.putpixel((x, y), (20, 20, 20, 255))
    frame.save(src, save_all=True, append_images=[frame], duration=[80, 80], loop=0)

    out = remove_white_background(str(src), out_dir=str(tmp_path))
    result = Image.open(out).convert("RGBA")

    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((3, 3))[3] == 255
