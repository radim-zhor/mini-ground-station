"""
aprs.py — decode the ISS APRS downlink from our baseband IQ.

The ISS carries an APRS digipeater on 145.825 MHz: 1200 baud Bell-202 AFSK
inside narrow-band FM, AX.25 UI frames whose callsigns, positions and comments
are sent in the clear (unlike the proprietary Orbcomm payload). We record it as
baseband IQ like everything else; this module turns that IQ into packets:

    .cs16 IQ (145.825 MHz centre, 250 ksps) → channelise + FM-demodulate to
    audio → WAV → direwolf's `atest` (AFSK1200 / AX.25) → parse the packets

`atest` ships with direwolf (brew install direwolf). Everything here is offline
and never raises — a failed decode must not cost the recording or the agent.
"""
import logging
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

log = logging.getLogger(__name__)

AUDIO_RATE = 50_000          # 250 ksps / 5, a clean integer decimation
ATEST_TIMEOUT_S = 300
# atest prints one line per decoded frame, then a summary. A frame line is the
# AX.25 address path SRC>DEST[,VIA...]:payload, prefixed by a "[N] " channel
# tag and wrapped in ANSI colour codes — both are stripped before matching.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_PACKET_RE = re.compile(
    r"(?:\[\d+\]\s*)?([A-Z0-9]{1,6}(?:-\d{1,2})?)>([A-Z0-9]{1,6}(?:-\d{1,2})?)[,:]")
_COUNT_RE = re.compile(r"(\d+)\s+packets?\s+decoded", re.IGNORECASE)


def is_available() -> bool:
    return shutil.which("atest") is not None


def decode(pass_dir: Path, meta: dict, products_dir: Path) -> dict:
    """Decode the best-effort APRS packets from an ISS recording.

    Returns a stats dict; ``{"error": "..."}`` when decoding could not run.
    Never raises.
    """
    if not is_available():
        return {"error": "atest not found (brew install direwolf)"}
    iq_files = sorted(pass_dir.glob("*.cs16"))
    if not iq_files:
        return {"error": "no .cs16 in pass directory"}

    products_dir.mkdir(parents=True, exist_ok=True)
    wav_path = products_dir / "iss_audio.wav"
    try:
        _iq_to_audio_wav(iq_files[0], int(meta["sample_rate"]), wav_path)
    except Exception as e:
        log.exception("FM-demodulating the ISS recording failed")
        return {"error": f"demod failed: {e}"}

    try:
        proc = subprocess.run(
            ["atest", "-B", "1200", str(wav_path)],
            capture_output=True, text=True, timeout=ATEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"atest timed out after {ATEST_TIMEOUT_S} s"}
    except Exception as e:
        return {"error": f"atest failed to run: {e}"}

    (products_dir / "atest.log").write_text((proc.stdout or "") + (proc.stderr or ""))
    return _parse(proc.stdout or "", products_dir)


def _iq_to_audio_wav(iq_path: Path, sample_rate: int, wav_path: Path) -> None:
    """Channelise to AUDIO_RATE and FM-demodulate the IQ to 16-bit mono audio.

    A single-finger NBFM discriminator (phase of x[n]·conj(x[n-1])) is all AFSK
    needs; the Doppler offset within the band just becomes a DC term in the
    audio, which the 1200/2200 Hz tones ride above.
    """
    raw = np.fromfile(iq_path, dtype=np.int16)
    if raw.size < 4:
        raise ValueError("recording too short")
    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)

    decim = max(1, round(sample_rate / AUDIO_RATE))
    if decim > 1:
        iq = resample_poly(iq, 1, decim)   # anti-aliased downsample to ~AUDIO_RATE
    rate = sample_rate // decim

    disc = np.angle(iq[1:] * np.conj(iq[:-1]))  # instantaneous frequency
    peak = np.max(np.abs(disc)) or 1.0
    audio = (disc / peak * 20000.0).astype(np.int16)
    wavfile.write(str(wav_path), int(rate), audio)


def _parse(stdout: str, products_dir: Path) -> dict:
    """Pull decoded frames and callsigns out of atest's output."""
    packets, sources = [], []
    for line in stdout.splitlines():
        line = _ANSI_RE.sub("", line).strip()
        m = _PACKET_RE.search(line)
        if m:
            packets.append(line)
            sources.append(m.group(1))

    count = len(packets)
    m = _COUNT_RE.search(stdout)
    if m:  # trust atest's own tally if it is higher (frames without a clean path)
        count = max(count, int(m.group(1)))

    if packets:
        (products_dir / "packets.txt").write_text("\n".join(packets) + "\n")

    stats: dict = {
        "packets": count,
        "frames": packets,
        "callsigns": sorted(set(sources)),
    }
    if count == 0:
        stats["error"] = "no APRS frames decoded"
    return stats


def write_summary(products_dir: Path, stats: dict) -> None:
    """Mirror orbcomm.write_summary: a telemetry.json the dashboard can read."""
    import json
    out = {
        "packets": stats.get("packets", 0),
        "callsigns": stats.get("callsigns", []),
        "frames": stats.get("frames", [])[:50],
    }
    (products_dir / "telemetry.json").write_text(json.dumps(out, indent=2))
