"""
decoder.py — NOAA APT decoder

Shells out to the `noaa-apt` CLI binary to convert a recorded WAV
into a satellite weather image (PNG).

Install binary: https://github.com/martinber/noaa-apt/releases
"""
import shutil
import subprocess
from pathlib import Path


def decode_apt(wav_path: Path) -> Path:
    """
    Decode a NOAA APT WAV recording to a PNG image.

    Args:
        wav_path: Path to the 48 kHz mono WAV file.

    Returns:
        Path to the decoded PNG image (same directory as WAV).

    Raises:
        FileNotFoundError: noaa-apt binary not found in PATH.
        RuntimeError:      noaa-apt exited with an error.
    """
    binary = shutil.which("noaa-apt")
    if not binary:
        raise FileNotFoundError(
            "noaa-apt binary not found in PATH. "
            "Download from https://github.com/martinber/noaa-apt/releases"
        )

    png_path = wav_path.with_suffix(".png")
    result = subprocess.run(
        [binary, str(wav_path), "-o", str(png_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"noaa-apt failed (exit {result.returncode}): {result.stderr.strip()}")

    return png_path
