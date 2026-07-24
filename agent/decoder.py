"""
decoder.py — decides which decoder gets a recording, and runs it

Every decoder is an external program (CLAUDE.md: never implement DSP by hand),
so this module is a dispatcher: pick one by satellite, run it as a subprocess,
collect whatever it produced into ``<pass>/products/``.

    METEOR  → satdump meteor_m2-x_lrpt   → PNG images
    ORBCOMM → orbcomm-receiver/file_decoder.py → frames, PER, ephemeris

Two rules hold for every path through here:

* **Nothing raises.** A decode failure must not kill the agent — the pass is
  already on disk and there is another one in twenty minutes.
* **A failure never costs the recording.** ``success=False`` tells the
  retention policy to keep the IQ, which is exactly the recording worth
  re-running by hand.
"""
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent import aprs, orbcomm
from agent.recorder import read_meta

log = logging.getLogger(__name__)

SATDUMP_TIMEOUT_S = 1800  # LRPT decoding of a long pass is not quick

# SatDump on macOS ships as an .app; the CLI symlinked into PATH still needs to
# run with its Resources directory as the working directory, or it finds no
# pipelines and exits.
SATDUMP_APP_RESOURCES = Path("/Applications/SatDump.app/Contents/Resources")

# 72k is the mode both Meteor M2-3 and M2-4 have been transmitting; M2-x also
# has an 80k mode, hence the override.
METEOR_PIPELINE = os.getenv("SATDUMP_METEOR_PIPELINE", "meteor_m2-x_lrpt")

# Above this packet error rate an Orbcomm decode counts as failed, so retention
# keeps the baseband instead of deleting it. Measured range on this station is
# 0 % at TCA and ~18 % at the horizon, so the threshold is far above anything
# a healthy pass produces and only catches genuine garbage.
MAX_ACCEPTABLE_PER = float(os.getenv("MAX_ACCEPTABLE_PER", "50"))


@dataclass
class DecodeResult:
    """What came out of a decode attempt — the input to the contact report."""

    success: bool
    kind: str = "unknown"          # "image" | "telemetry"
    products: list = field(default_factory=list)   # [Path, ...]
    stats: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def image(self) -> Optional[Path]:
        """First image product, if any — what the dashboard shows."""
        for p in self.products:
            if p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                return p
        return None


def decode(pass_dir: Path) -> DecodeResult:
    """Decode a recorded pass directory. Never raises."""
    try:
        return _decode(pass_dir)
    except Exception as e:  # last line of defence — the agent keeps running
        log.exception("Decoding crashed")
        return DecodeResult(success=False, notes=f"decode crashed: {e}")


def _decode(pass_dir: Path) -> DecodeResult:
    meta = read_meta(pass_dir)
    satellite = meta.get("satellite", "")
    if not satellite:
        return DecodeResult(success=False, notes=f"no meta.json in {pass_dir.name}")

    name = satellite.upper()
    if name.startswith("METEOR"):
        return _decode_meteor(pass_dir, meta)
    if name.startswith("ORBCOMM"):
        return _decode_orbcomm(pass_dir, meta)
    if name.startswith("ISS"):
        return _decode_iss(pass_dir, meta)
    return DecodeResult(success=False, notes=f"no decoder for {satellite}")


# ── Meteor: SatDump LRPT ──────────────────────────────────────────────────────

def _decode_meteor(pass_dir: Path, meta: dict) -> DecodeResult:
    binary = satdump_binary()
    if not binary:
        return DecodeResult(success=False, notes="decode skipped: satdump not found in PATH")

    iq = sorted(pass_dir.glob("*.cs16"))
    if not iq:
        return DecodeResult(success=False, notes="no .cs16 in pass directory")

    products_dir = pass_dir / "products"
    products_dir.mkdir(exist_ok=True)
    cmd = [
        binary, METEOR_PIPELINE, "baseband", str(iq[0]), str(products_dir),
        "--samplerate", str(int(meta["sample_rate"])),
        "--baseband_format", "s16",
    ]
    log.info("Decoding %s with satdump %s", iq[0].name, METEOR_PIPELINE)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(SATDUMP_APP_RESOURCES) if SATDUMP_APP_RESOURCES.is_dir() else None,
            capture_output=True,
            text=True,
            timeout=SATDUMP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return DecodeResult(success=False, notes=f"satdump timed out after {SATDUMP_TIMEOUT_S} s")

    (products_dir / "satdump.log").write_text((result.stdout or "") + (result.stderr or ""))
    images = sorted(p for p in products_dir.rglob("*.png"))

    if result.returncode != 0 and not images:
        tail = (result.stderr or result.stdout or "").strip()[-200:]
        return DecodeResult(success=False, notes=f"satdump exited {result.returncode}: {tail}")
    if not images:
        # A pass too low or too noisy decodes to nothing. Not an error, but the
        # IQ stays: without an image there is nothing else to keep.
        return DecodeResult(success=False, notes="satdump produced no image")

    return DecodeResult(
        success=True,
        kind="image",
        products=images,
        stats={"images": len(images)},
        notes=f"satdump {METEOR_PIPELINE}: {len(images)} image(s)",
    )


def satdump_binary() -> Optional[str]:
    """Path to the satdump CLI, or None."""
    env = os.getenv("SATDUMP_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("satdump")
    if found:
        return found
    bundled = Path("/Applications/SatDump.app/Contents/MacOS/satdump")
    return str(bundled) if bundled.exists() else None


# ── Orbcomm: vendored file_decoder ────────────────────────────────────────────

def _decode_orbcomm(pass_dir: Path, meta: dict) -> DecodeResult:
    products_dir = pass_dir / "products"
    stats = orbcomm.decode(pass_dir, meta, products_dir)

    if "error" in stats:
        return DecodeResult(success=False, kind="telemetry", stats=stats,
                            notes=f"orbcomm decode failed: {stats['error']}")

    orbcomm.write_summary(products_dir, stats)
    products = sorted(p for p in products_dir.iterdir() if p.is_file())

    per = stats.get("per")
    ephem = len(stats.get("ephemeris", []))
    notes = f"orbcomm: {stats.get('packets', 0)} packets"
    if per is not None:
        notes += f", PER {per:.1f}%"
    if ephem:
        notes += f", {ephem} ephemeris frame(s)"

    # PER decides, not the packet count. A run can emit packets and still be
    # garbage: 22. 7. a pass with avg SNR 17.8 dB decoded at PER 99 % and was
    # reported as a success, so retention deleted its 2.94 GB of IQ and the
    # failure became undiagnosable. Normal is 0 % at TCA, ~18 % at the horizon
    # (see agent/orbcomm.py), so anything above MAX_ACCEPTABLE_PER is a failed
    # decode that must keep its baseband for a manual re-run.
    if per is not None and per > MAX_ACCEPTABLE_PER:
        return DecodeResult(success=False, kind="telemetry", products=products,
                            stats=stats,
                            notes=f"{notes} — PER over {MAX_ACCEPTABLE_PER:.0f} %, keeping IQ")

    return DecodeResult(success=True, kind="telemetry", products=products,
                        stats=stats, notes=notes)


def _decode_iss(pass_dir: Path, meta: dict) -> DecodeResult:
    """ISS APRS: any decoded frame is a real success, unlike Orbcomm there is no
    PER — a valid AX.25 frame either passed its CRC or was never emitted."""
    products_dir = pass_dir / "products"
    stats = aprs.decode(pass_dir, meta, products_dir)

    if "error" in stats:
        return DecodeResult(success=False, kind="telemetry", stats=stats,
                            notes=f"iss aprs: {stats['error']}")

    aprs.write_summary(products_dir, stats)
    products = sorted(p for p in products_dir.iterdir() if p.is_file())
    calls = stats.get("callsigns", [])
    notes = f"iss aprs: {stats.get('packets', 0)} frame(s)"
    if calls:
        notes += f" from {', '.join(calls[:5])}"
    return DecodeResult(success=True, kind="telemetry", products=products,
                        stats=stats, notes=notes)
