"""
orbcomm.py — bridge from our IQ recordings to the vendored Orbcomm decoder

`orbcomm-receiver/file_decoder.py` (fbieberly/ORBCOMM-receiver, plus the macOS
patch in `patches/`) already does the hard part: Doppler compensation, timing
and carrier recovery, packet framing and the Fletcher checksum. It is a
research script, not a library — module-level code, matplotlib figures, and it
reads one 2-second `.mat` file produced by the upstream recorder.

Rather than reimplement any of that (see CLAUDE.md: always shell out to an
existing decoder), this module meets it where it is:

    our .cs16 → pick the best 2 s window → write it as a .mat → run the script
    → parse its stdout

The window matters. A pass is minutes long, the decoder wants seconds, and the
difference between the horizon and TCA was 18 % vs 0 % PER on 22 July. The SNR
profile the recorder measured while capturing picks the moment for us.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
RECEIVER_DIR = REPO_ROOT / "orbcomm-receiver"
WINDOW_S = 2.0        # what the upstream decoder was written for
TIMEOUT_S = 300


def is_available() -> bool:
    """Is the vendored decoder checked out? It is gitignored, so it may not be."""
    return (RECEIVER_DIR / "file_decoder.py").is_file()


def decode(pass_dir: Path, meta: dict, products_dir: Path) -> dict:
    """
    Decode the best window of an Orbcomm recording.

    Returns a stats dict; ``{"error": "..."}`` when decoding could not run or
    produced nothing. Never raises — a decode failure must not cost us the
    recording (or the agent).
    """
    if not is_available():
        return {"error": f"orbcomm decoder not found at {RECEIVER_DIR}"}

    iq_files = sorted(pass_dir.glob("*.cs16"))
    if not iq_files:
        return {"error": "no .cs16 in pass directory"}

    sat_key = sat_db_name(meta.get("satellite", ""))
    if sat_key is None:
        return {"error": f"{meta.get('satellite')} is not in the decoder's satellite database"}

    tle = _tle_for(meta.get("satellite", ""))
    if tle is None:
        return {"error": f"no TLE available for {meta.get('satellite')}"}

    products_dir.mkdir(parents=True, exist_ok=True)
    offset_s = best_window_offset(meta.get("snr_series") or [], meta.get("duration_s", 0))

    try:
        mat_path = _write_mat(iq_files[0], meta, sat_key, tle, offset_s)
    except Exception as e:
        log.exception("Preparing the Orbcomm decoder input failed")
        return {"error": f"could not prepare decoder input: {e}"}

    try:
        stdout = _run_decoder(mat_path)
    except subprocess.TimeoutExpired:
        return {"error": f"decoder timed out after {TIMEOUT_S} s"}
    except Exception as e:
        log.exception("Orbcomm decoder failed")
        return {"error": f"decoder failed: {e}"}
    finally:
        mat_path.unlink(missing_ok=True)

    (products_dir / "orbcomm_decode.log").write_text(stdout)
    packets_txt = RECEIVER_DIR / "packets.txt"
    if packets_txt.is_file():
        shutil.copy(packets_txt, products_dir / "packets.txt")

    stats = parse_output(stdout)
    if packets_txt.is_file():
        # One hex packet per line — an exact count, unlike parsing the printout.
        stats["packets"] = sum(1 for line in packets_txt.read_text().splitlines() if line.strip())
    stats["window_offset_s"] = round(offset_s, 1)
    stats["satellite"] = sat_key
    if stats.get("packets", 0) == 0:
        stats.setdefault("error", "decoder produced no packets")
    return stats


# ── Window selection ──────────────────────────────────────────────────────────

def best_window_offset(snr_series, duration_s: float) -> float:
    """
    Offset (seconds from the start of the file) of the best window to decode.

    Uses the SNR profile measured while recording; falls back to the middle of
    the pass, which is close to TCA anyway.
    """
    usable = max(float(duration_s) - WINDOW_S, 0.0)
    if not snr_series:
        return usable / 2

    elapsed, snr = max(snr_series, key=lambda pair: pair[1])
    # snr_series timestamps mark the *end* of each chunk.
    start = float(elapsed) - WINDOW_S
    return min(max(start, 0.0), usable)


# ── Satellite naming ──────────────────────────────────────────────────────────

def sat_db_name(satellite: str) -> Optional[str]:
    """
    Map a SatNOGS name ("ORBCOMM FM 118") to the decoder's key ("orbcomm fm118").

    Returns None when that satellite is not one the decoder knows — its
    database also carries the channel frequencies, so an unknown bird cannot be
    decoded even if we recorded it.
    """
    try:
        sys.path.insert(0, str(RECEIVER_DIR))
        from sat_db import active_orbcomm_satellites
    except Exception:
        return None
    finally:
        if sys.path and sys.path[0] == str(RECEIVER_DIR):
            sys.path.pop(0)

    squashed = satellite.replace(" ", "").lower()
    for key in active_orbcomm_satellites:
        if key.replace(" ", "").lower() == squashed:
            return key
    return None


def _tle_for(satellite: str):
    from shared.tle import tle_lines

    return tle_lines(satellite)


# ── Decoder input / invocation ────────────────────────────────────────────────

def _write_mat(iq_path: Path, meta: dict, sat_key: str, tle, offset_s: float) -> Path:
    """Write one window of our IQ as the .mat the upstream decoder reads."""
    from scipy.io import savemat

    rate = int(meta["sample_rate"])
    start_sample = int(offset_s * rate)
    count = int(WINDOW_S * rate)

    raw = np.fromfile(iq_path, dtype=np.int16, count=2 * count, offset=start_sample * 4)
    if len(raw) < 2 * 1024:
        raise ValueError(f"only {len(raw) // 2} samples at offset {offset_s:.1f} s")
    samples = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) / 32768.0

    observer = meta.get("observer") or {}
    fd, path = tempfile.mkstemp(suffix=".mat", dir=str(RECEIVER_DIR / "data"))
    os.close(fd)
    savemat(path, {
        "samples": samples.astype(np.complex64).reshape(1, -1),
        "timestamp": float(meta["timestamp"]) + offset_s,  # this window, not the file
        "fs": float(rate),
        "fc": float(meta["center_freq_hz"]),
        "lat": float(observer.get("lat") or 0.0),
        "lon": float(observer.get("lon") or 0.0),
        "alt": float(observer.get("alt") or 0.0),
        "sats": [sat_key],
        "tles": [list(tle)],
    })
    return Path(path)


def _run_decoder(mat_path: Path) -> str:
    """
    Run the vendored script on one .mat file and return its stdout.

    MPLBACKEND=Agg is what makes this usable unattended: the script ends in
    plt.show(), which otherwise blocks forever waiting for a window nobody is
    looking at.
    """
    env = dict(os.environ, MPLBACKEND="Agg")
    result = subprocess.run(
        [sys.executable, "file_decoder.py", str(mat_path)],
        cwd=str(RECEIVER_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"file_decoder.py exited {result.returncode}: {result.stderr.strip()[-300:]}"
        )
    return result.stdout


# ── Output parsing ────────────────────────────────────────────────────────────

_PER_RE = re.compile(
    r"(\d+) packets with errors, (\d+) packets corrected, PER:\s*([\d.]+)%"
)
_OFFSET_RE = re.compile(r"Remaining frequency offset after doppler compensation: ([-\d.]+) Hz")
_SAT_TIME_RE = re.compile(r"Current satellite time: (.+) Z")
_SAT_POS_RE = re.compile(
    r"Sat Lat/Lon:\s*([-\d.]+),\s*([-\d.]+), Altitude:\s*([\d.]+) km, Velocity:\s*([\d.]+)"
)
_POS_DIFF_RE = re.compile(r"Difference in reported and ephemeris position:\s*([\d.]+) km")


def parse_output(stdout: str) -> dict:
    """Turn the decoder's printout into the numbers a contact report needs."""
    stats: dict = {"packet_types": {}}

    m = _PER_RE.search(stdout)
    if m:
        stats["packets_with_errors"] = int(m.group(1))
        stats["packets_corrected"] = int(m.group(2))
        stats["per"] = float(m.group(3))

    m = _OFFSET_RE.search(stdout)
    if m:
        stats["freq_offset_hz"] = float(m.group(1))

    ephemeris = []
    times = _SAT_TIME_RE.findall(stdout)
    positions = _SAT_POS_RE.findall(stdout)
    diffs = _POS_DIFF_RE.findall(stdout)
    for i, pos in enumerate(positions):
        entry = {
            "lat": float(pos[0]),
            "lon": float(pos[1]),
            "alt_km": float(pos[2]),
            "velocity_ms": float(pos[3]),
        }
        if i < len(times):
            entry["satellite_time"] = times[i].strip()
        if i < len(diffs):
            entry["ephemeris_diff_km"] = float(diffs[i])
        ephemeris.append(entry)
    if ephemeris:
        stats["ephemeris"] = ephemeris

    # Packet type counts — only from the packet listing. Everything above it is
    # the script's own preamble ("SDR Sample rate: ...") and would otherwise be
    # counted as packets.
    _, _, listing = stdout.partition("List of packets:")
    packets = 0
    for line in listing.splitlines():
        if line.startswith("\t"):  # ephemeris detail lines belong to the packet above
            continue
        m = re.match(r"(?:###\s*)?([A-Z][A-Za-z]*(?: [a-z]+)?): ", line)
        if not m:
            continue
        kind = "Unrecognized" if m.group(1).startswith("Unrecognized") else m.group(1)
        stats["packet_types"][kind] = stats["packet_types"].get(kind, 0) + 1
        packets += 1
    stats["packets"] = packets

    return stats


def write_summary(products_dir: Path, stats: dict) -> None:
    """Persist the parsed stats next to the raw decoder log."""
    with (products_dir / "telemetry.json").open("w") as f:
        json.dump(stats, f, indent=2)
