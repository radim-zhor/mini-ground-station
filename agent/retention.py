"""
retention.py — keeps `recordings/` from eating the disk

Baseband IQ is roughly a thousand times heavier than the 48 kHz WAVs it
replaced: 10 min × 1.2288 Msps × 4 B ≈ 3 GB per pass, and there are ~42 passes
a day. Retention is therefore part of the capture path, not a nice-to-have.

Two rules:

1. **After a successful decode the IQ goes.** The products are the result; the
   baseband was only the means. After a *failed* decode it stays — that is
   exactly the recording worth re-running by hand.
2. **A hard cap on the directory.** Whatever survives rule 1, the oldest pass
   directories are dropped until the total fits under ``RECORDINGS_MAX_GB``.

Only directories this agent wrote (they contain a ``meta.json``) are ever
deleted. Hand-made recordings and reference images sitting in ``recordings/``
are left alone — losing those to an automatic cleanup would be unforgivable.
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MAX_GB = 20.0


def max_bytes() -> int:
    """Size cap for `recordings/`, from RECORDINGS_MAX_GB (default 20 GB)."""
    try:
        gb = float(os.getenv("RECORDINGS_MAX_GB", DEFAULT_MAX_GB))
    except ValueError:
        gb = DEFAULT_MAX_GB
    return int(gb * 1e9)


def discard_iq(pass_dir: Path, reason: str = "decoded") -> bool:
    """
    Delete the baseband IQ of a pass, keeping the directory and its products.

    Call this only after a *successful* decode. Returns True if anything was
    removed.
    """
    removed = False
    for iq in sorted(pass_dir.glob("*.cs16")):
        size = iq.stat().st_size
        iq.unlink()
        removed = True
        log.info("Retention: removed %s (%.2f GB, %s)", iq.name, size / 1e9, reason)
    return removed


def keep_iq(pass_dir: Path, reason: str) -> None:
    """Log why a recording is being kept, so the disk never fills silently."""
    size = sum(p.stat().st_size for p in pass_dir.glob("*.cs16"))
    log.warning(
        "Retention: keeping %s (%.2f GB) for debugging — %s",
        pass_dir.name,
        size / 1e9,
        reason,
    )


def after_decode(pass_dir: Path, success: bool, reason: str = "") -> None:
    """Apply rule 1: drop the IQ on success, keep it (loudly) on failure."""
    if success:
        discard_iq(pass_dir)
    else:
        keep_iq(pass_dir, reason or "decode failed")


def enforce_cap(
    recordings_dir: Path,
    limit_bytes: Optional[int] = None,
    keep: Optional[Path] = None,
) -> int:
    """
    Apply rule 2: delete whole pass directories, oldest first, until the total
    fits under the cap.

    Args:
        recordings_dir: the directory to police
        limit_bytes:    cap; defaults to RECORDINGS_MAX_GB
        keep:           a pass directory to never touch — the one being
                        recorded right now

    Returns:
        Number of bytes freed.
    """
    limit = max_bytes() if limit_bytes is None else limit_bytes
    if not recordings_dir.exists():
        return 0

    total = _dir_size(recordings_dir)
    if total <= limit:
        return 0

    keep = keep.resolve() if keep else None
    # Oldest first — mtime of meta.json is when the pass finished recording.
    candidates = sorted(_pass_dirs(recordings_dir), key=_pass_mtime)

    freed = 0
    for pass_dir in candidates:
        if total - freed <= limit:
            break
        if keep and pass_dir.resolve() == keep:
            continue
        size = _dir_size(pass_dir)
        shutil.rmtree(pass_dir, ignore_errors=True)
        freed += size
        log.info("Retention: dropped oldest pass %s (%.2f GB)", pass_dir.name, size / 1e9)

    remaining = total - freed
    if remaining > limit:
        # Everything deletable is gone and we are still over — that means the
        # overflow is in files we refuse to touch, or in the current pass.
        log.warning(
            "Retention: %.1f GB still over the %.1f GB cap — nothing left that "
            "this agent may delete",
            remaining / 1e9,
            limit / 1e9,
        )
    return freed


def has_room_for(recordings_dir: Path, expected_bytes: int) -> bool:
    """Would a recording of this size fit under the cap? Logs if it would not."""
    limit = max_bytes()
    if expected_bytes > limit:
        log.warning(
            "Retention: the upcoming pass alone (%.1f GB) exceeds the %.1f GB cap — "
            "raise RECORDINGS_MAX_GB or lower the sample rate",
            expected_bytes / 1e9,
            limit / 1e9,
        )
        return False
    return _dir_size(recordings_dir) + expected_bytes <= limit


# ── Internals ─────────────────────────────────────────────────────────────────

def _pass_dirs(recordings_dir: Path):
    """Pass directories written by this agent — anything else is off limits."""
    for entry in recordings_dir.iterdir():
        if entry.is_dir() and (entry / "meta.json").is_file():
            yield entry


def _pass_mtime(pass_dir: Path) -> float:
    try:
        return (pass_dir / "meta.json").stat().st_mtime
    except OSError:
        return pass_dir.stat().st_mtime


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total
