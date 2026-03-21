"""Media processing utilities for thumbnail generation and video watermarking."""

import io
import subprocess
import tempfile
import os
from logging_config import get_logger

logger = get_logger("media")


def generate_thumbnail(image_bytes: bytes, content_type: str) -> bytes:
    """Generate a JPEG thumbnail (400x400 max) from an image."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((400, 400), Image.LANCZOS)

    # Convert to RGB if needed (e.g., PNG with alpha)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    buf.seek(0)
    return buf.read()


def generate_video_thumbnail(video_bytes: bytes) -> bytes:
    """Extract a frame at 1s from a video and return as JPEG thumbnail."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        tmp_in.write(video_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + "_thumb.jpg"

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", tmp_in_path,
                "-ss", "1",
                "-vframes", "1",
                "-vf", "scale=400:400:force_original_aspect_ratio=decrease",
                "-q:v", "5",
                tmp_out_path,
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        with open(tmp_out_path, "rb") as f:
            return f.read()
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg thumbnail extraction failed: {e.stderr.decode()}")
        raise
    finally:
        for p in (tmp_in_path, tmp_out_path):
            if os.path.exists(p):
                os.unlink(p)


def apply_video_watermark(video_bytes: bytes, watermark_text: str) -> bytes:
    """Apply a semi-transparent text watermark to a video using ffmpeg drawtext."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        tmp_in.write(video_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + "_wm.mp4"

    # Escape special characters for ffmpeg drawtext
    safe_text = watermark_text.replace("'", "'\\''").replace(":", "\\:")

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", tmp_in_path,
                "-vf", (
                    f"drawtext=text='{safe_text}'"
                    ":fontsize=24"
                    ":fontcolor=white@0.3"
                    ":shadowcolor=black@0.3:shadowx=2:shadowy=2"
                    ":x=(w-text_w)/2:y=(h-text_h)/2"
                ),
                "-codec:a", "copy",
                "-preset", "fast",
                tmp_out_path,
            ],
            capture_output=True,
            timeout=300,
            check=True,
        )
        with open(tmp_out_path, "rb") as f:
            return f.read()
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg watermark failed: {e.stderr.decode()}")
        raise
    finally:
        for p in (tmp_in_path, tmp_out_path):
            if os.path.exists(p):
                os.unlink(p)
