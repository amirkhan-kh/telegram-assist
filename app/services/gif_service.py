"""Small GIF utilities used by the owner bot."""

from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageSequence

from app.logging_conf import get_logger

logger = get_logger(__name__)


def ensure_loading_spinner(out_dir: str = "data/media") -> str:
    """Create/reuse a small transparent loading spinner GIF."""
    os.makedirs(out_dir, exist_ok=True)
    path = Path(out_dir) / "joni_spinner.gif"
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    size = 96
    frames: list[Image.Image] = []
    durations: list[int] = []
    center = size // 2
    radius = 30
    dot_r = 5
    for frame_idx in range(18):
        frame = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        for i in range(12):
            # A rotating comet: one bright dot, trailing dots fade out.
            phase = (i - frame_idx) % 12
            alpha = max(30, 235 - phase * 17)
            angle = 2 * math.pi * i / 12
            x = int(center + radius * math.cos(angle))
            y = int(center + radius * math.sin(angle))
            color = (34, 132, 245, alpha)
            _draw_dot(frame, x, y, dot_r, color)
        frames.append(frame)
        durations.append(55)
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        transparency=0,
    )
    logger.info("gif.spinner.created", path=str(path))
    return str(path)


def remove_white_background(
    src_path: str,
    *,
    out_dir: str,
    threshold: int = 245,
    max_frames: int = 120,
) -> str:
    """Remove near-white background from a GIF/MP4 animation.

    Telegram "GIFs" often arrive as MP4 animations. For GIF input Pillow reads
    frames directly; for MP4 input ffmpeg extracts PNG frames first. The result is
    always a transparent GIF sent back as a document so alpha is preserved.
    """
    os.makedirs(out_dir, exist_ok=True)
    src = Path(src_path)
    out = Path(out_dir) / f"{src.stem}_transparent.gif"
    try:
        frames, durations = _load_gif_frames(src_path, threshold, max_frames)
    except Exception:
        frames, durations = _load_video_frames(src_path, out_dir, threshold, max_frames)
    if not frames:
        raise ValueError("animation has no frames")
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        transparency=0,
    )
    logger.info("gif.background_removed", src=src_path, out=str(out), frames=len(frames))
    return str(out)


def _draw_dot(frame: Image.Image, cx: int, cy: int, radius: int, color: tuple[int, ...]) -> None:
    """Draw a small anti-aliased-ish filled circle into ``frame``."""
    px = frame.load()
    for x in range(cx - radius, cx + radius + 1):
        for y in range(cy - radius, cy + radius + 1):
            if x < 0 or y < 0 or x >= frame.width or y >= frame.height:
                continue
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                px[x, y] = color


def _load_gif_frames(
    src_path: str, threshold: int, max_frames: int
) -> tuple[list[Image.Image], list[int]]:
    image = Image.open(src_path)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for i, frame in enumerate(ImageSequence.Iterator(image)):
        if i >= max_frames:
            break
        frames.append(_transparent_white(frame.convert("RGBA"), threshold))
        durations.append(int(frame.info.get("duration", image.info.get("duration", 80))))
    return frames, durations


def _load_video_frames(
    src_path: str, out_dir: str, threshold: int, max_frames: int
) -> tuple[list[Image.Image], list[int]]:
    frame_dir = Path(out_dir) / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frame_dir / "frame_%04d.png")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src_path,
        "-t",
        "10",
        "-vf",
        "fps=12,scale='min(480,iw)':-2",
        pattern,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    paths = sorted(frame_dir.glob("frame_*.png"))[:max_frames]
    frames = [
        _transparent_white(Image.open(path).convert("RGBA"), threshold)
        for path in paths
    ]
    return frames, [83 for _ in frames]


def _transparent_white(frame: Image.Image, threshold: int) -> Image.Image:
    """Turn near-white pixels transparent while keeping the spinner opaque."""
    pixels = []
    for r, g, b, a in frame.getdata():
        if r >= threshold and g >= threshold and b >= threshold:
            pixels.append((255, 255, 255, 0))
        else:
            pixels.append((r, g, b, a))
    frame.putdata(pixels)
    return frame
