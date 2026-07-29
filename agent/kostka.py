"""
kostka.py — bridge from our IQ recordings to gr-satellites, for KOSTKA

KOSTKA (VUT Brno / YSpace, 1U CubeSat) transmits 9600 baud GFSK with G3RUH
scrambling and AX.25 framing on 436.870 MHz. Telemetry, SSDV image chunks, the
digipeater and the CW beacon all share that one downlink and differ only by the
AX.25 frame, not by frequency — so there is exactly one thing to record and one
decoder to run.

    our .cs16 → Doppler-correct against the TLE → gr_satellites → KISS frames

Two things here are not shared with the other decoders:

**Doppler correction is ours, not the decoder's.** At 436.87 MHz a LEO pass
sweeps roughly ±10 kHz, which is half the occupied bandwidth of a 9k6 GFSK
signal — the same excursion at 137 MHz is ±3 kHz and small enough that Meteor
and Orbcomm get away without it here (the Orbcomm decoder does its own).
Leaving it in would drag the signal across the demodulator's capture range
mid-pass. We have the TLE, the station position and the sample timestamps in
meta.json, so we mix it out ourselves before the decoder ever sees the file.

**The dongle is not parked on the downlink.** It sits KOSTKA_OFFSET_HZ below it,
for the same reason Orbcomm is recorded at 137.5: the R820T's DC spike sits
exactly at the centre frequency and would land in the middle of the signal.
The correction above shifts the wanted signal from that offset down to 0 Hz.

Nothing here raises — a failed decode must cost the products, never the
recording or the agent.
"""
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Downlink (AMSAT ANS-186 / IARU coordination, 5. 7. 2026). Overridable because
# the satellite is days old: a fresh bird's published frequency is a claim until
# it has been heard, and SatNOGS transmitter entries for new launches do move.
KOSTKA_DOWNLINK_HZ = int(os.getenv("KOSTKA_FREQ_HZ", "436870000"))

# How far below the downlink the dongle is parked, to keep the DC spike out of
# the signal. 50 kHz clears both the ~20 kHz wide 9k6 GFSK signal and its
# ±10 kHz of Doppler, and still leaves margin inside the 250 ksps band.
KOSTKA_OFFSET_HZ = 50_000
KOSTKA_CENTER_HZ = KOSTKA_DOWNLINK_HZ - KOSTKA_OFFSET_HZ

# FSK deviation passed to gr_satellites. Empty = leave it at its own default
# (5 kHz). See _run_gr_satellites for why this is a knob.
KOSTKA_DEVIATION_HZ = os.getenv("KOSTKA_DEVIATION_HZ", "")

SATYAML = Path(__file__).parent / "satyaml" / "kostka.yml"
TIMEOUT_S = 900

C_KM_S = 299_792.458

# Doppler is resampled on this grid and the phase interpolated between points.
# The range rate of a LEO pass changes smoothly over tens of seconds; 0.5 s
# leaves the interpolated phase error far below a symbol at 9600 baud.
DOPPLER_GRID_S = 0.5
# Samples per Doppler-correction block. At 250 ksps this is ~17 s of signal and
# ~100 MB of working set — the recording itself is far too big to hold at once.
BLOCK_SAMPLES = 1 << 22


def is_available() -> bool:
    """Is gr-satellites installed? It is a GNU Radio dependency, not a pip one,
    so a working checkout can perfectly well not have it."""
    return shutil.which("gr_satellites") is not None


def decode(pass_dir: Path, meta: dict, products_dir: Path) -> dict:
    """Decode a KOSTKA recording into AX.25 frames.

    Returns a stats dict; ``{"error": "..."}`` when decoding could not run or
    produced nothing. Never raises.
    """
    if not is_available():
        return {"error": "gr_satellites not found (source build — see CLAUDE.md)"}
    if not SATYAML.is_file():
        return {"error": f"SatYAML missing at {SATYAML}"}

    iq_files = sorted(pass_dir.glob("*.cs16"))
    if not iq_files:
        return {"error": "no .cs16 in pass directory"}

    products_dir.mkdir(parents=True, exist_ok=True)
    corrected = pass_dir / "kostka_doppler.cs16"
    try:
        doppler = doppler_correct(iq_files[0], meta, corrected)
    except Exception as e:
        log.exception("Doppler correction of the KOSTKA recording failed")
        corrected.unlink(missing_ok=True)
        return {"error": f"doppler correction failed: {e}"}

    kiss_path = products_dir / "kostka.kiss"
    try:
        stdout = _run_gr_satellites(corrected, int(meta["sample_rate"]), kiss_path, meta)
    except subprocess.TimeoutExpired:
        return {"error": f"gr_satellites timed out after {TIMEOUT_S} s"}
    except Exception as e:
        log.exception("gr_satellites failed")
        return {"error": f"gr_satellites failed: {e}"}
    finally:
        # The corrected copy is the same size as the recording — it exists only
        # for the length of the decode.
        corrected.unlink(missing_ok=True)

    (products_dir / "kostka_decode.log").write_text(stdout)

    stats = parse_frames(kiss_path)
    stats["doppler"] = doppler
    if stats.get("packets", 0) == 0:
        stats["error"] = "gr_satellites produced no frames"
    return stats


# ── Doppler correction ────────────────────────────────────────────────────────

def doppler_correct(iq_path: Path, meta: dict, out_path: Path) -> dict:
    """Mix the wanted signal down to 0 Hz, removing Doppler as it goes.

    The received signal sits at ``(downlink - centre) + f_doppler(t)`` in the
    baseband, so multiplying by ``exp(-j·2π·∫f(t)dt)`` puts it at DC and holds
    it there for the whole pass. Written back as .cs16 so the decoder reads it
    exactly like a recording.

    Returns a small summary of what was removed, for the contact report.
    """
    rate = int(meta["sample_rate"])
    centre = float(meta["center_freq_hz"])
    started = float(meta["timestamp"])
    downlink = float(KOSTKA_DOWNLINK_HZ)

    total_samples = iq_path.stat().st_size // 4
    if total_samples < rate:  # less than a second of signal is not a pass
        raise ValueError(f"recording too short ({total_samples} samples)")
    duration = total_samples / rate

    # Ends exactly at the last sample, never past it: the reported start/end/peak
    # shift then describes the pass that was actually recorded.
    grid = np.linspace(0.0, duration, max(int(duration / DOPPLER_GRID_S) + 1, 2))
    shift = doppler_shift_hz(meta, grid, downlink)
    if shift is None:
        # No TLE: still worth decoding, the fixed offset alone is most of the
        # job and a high pass spends a fair while near zero Doppler.
        log.warning("No TLE for KOSTKA — correcting the fixed offset only")
        shift = np.zeros_like(grid)
        used_tle = False
    else:
        used_tle = True

    baseband_hz = (downlink - centre) + shift

    # The *frequency* is interpolated onto every sample and integrated there,
    # rather than interpolating an already-integrated phase between grid points.
    # Interpolating the phase linearly makes the corrected frequency piecewise
    # constant, which leaves a sawtooth residual of ±(dopplerslope · grid / 2) —
    # measured at 462 Hz against a 2 kHz/s test ramp. Integrating at the sample
    # rate removes it, and the running phase carries across blocks so the
    # correction stays continuous over the whole pass.
    phase_offset = 0.0
    step = 2 * np.pi / rate

    with out_path.open("wb") as out:
        for start in range(0, total_samples, BLOCK_SAMPLES):
            n = min(BLOCK_SAMPLES, total_samples - start)
            raw = np.fromfile(iq_path, dtype=np.int16, count=2 * n, offset=4 * start)
            n = len(raw) // 2
            if n == 0:
                break
            iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
            t = (start + np.arange(n)) / rate
            phase = phase_offset + step * np.cumsum(np.interp(t, grid, baseband_hz))
            phase_offset = float(phase[-1])
            iq *= np.exp(-1j * phase)
            block = np.empty(2 * n, dtype=np.int16)
            block[0::2] = np.clip(iq.real, -32768, 32767)
            block[1::2] = np.clip(iq.imag, -32768, 32767)
            out.write(block.tobytes())

    return {
        "from_tle": used_tle,
        "offset_hz": round(downlink - centre, 1),
        "shift_start_hz": round(float(shift[0]), 1),
        "shift_end_hz": round(float(shift[-1]), 1),
        "shift_peak_hz": round(float(np.max(np.abs(shift))), 1),
        "started_at_unix": started,
    }


def doppler_shift_hz(meta: dict, elapsed_s: np.ndarray, downlink_hz: float):
    """Doppler shift (Hz) of the downlink at each elapsed time in the pass.

    Positive while the satellite is approaching. Returns None when the TLE or
    the station position needed to compute it is not available.
    """
    from skyfield.api import EarthSatellite, wgs84

    from shared.tle import tle_lines, ts

    lines = tle_lines(meta.get("satellite", ""))
    observer = meta.get("observer") or {}
    if lines is None or observer.get("lat") is None or observer.get("lon") is None:
        return None

    sat = EarthSatellite(lines[1], lines[2], lines[0], ts)
    station = wgs84.latlon(float(observer["lat"]), float(observer["lon"]))

    t = ts.from_datetime(_utc(meta["timestamp"]))
    times = ts.tt_jd(t.tt + elapsed_s / 86400.0)

    topocentric = (sat - station).at(times)
    pos = topocentric.position.km
    vel = topocentric.velocity.km_per_s
    distance = np.linalg.norm(pos, axis=0)
    range_rate = np.sum(pos * vel, axis=0) / distance  # km/s, + = receding

    return -downlink_hz * range_rate / C_KM_S


def _utc(timestamp: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)


# ── gr-satellites invocation ──────────────────────────────────────────────────

def _run_gr_satellites(iq_path: Path, sample_rate: int, kiss_path: Path, meta: dict) -> str:
    """Run gr_satellites over the Doppler-corrected IQ and return its output.

    The satellite is given as a SatYAML path rather than a name or NORAD ID:
    KOSTKA is too new to be in the gr-satellites database, and its catalogue
    number is not final anyway (see shared/tle.py).

    The whole command line is written into the decode log, because it is the
    first thing to check when a decode comes back empty.
    """
    cmd = [
        "gr_satellites", str(SATYAML),
        # --rawint16 *is* the input file option for int16 samples, not a
        # modifier of --rawfile (which is float32/complex64). Passing both puts
        # a filename where gr_satellites expects none.
        "--rawint16", str(iq_path),
        "--iq",
        "--samp_rate", str(float(sample_rate)),
        # doppler_correct() put the signal *on* DC, so the DC blocker
        # gr_satellites applies to IQ by default would notch out its middle.
        # It exists to kill the SDR's DC spike — which the 50 kHz parking
        # offset already moved to -50 kHz, far from the signal. (There is no
        # --f_offset for FSK with IQ input; that option is AFSK-only, and
        # passing it fails on unrecognized arguments.)
        "--disable_dc_block",
        # The recording is not level-controlled: gain is fixed before AOS and
        # the signal rises ~20 dB towards TCA.
        "--use_agc",
        "--kiss_out", str(kiss_path),
        "--hexdump",
        # Seconds, no microseconds and no UTC offset — the documented format is
        # plain YYYY-MM-DDTHH:MM:SS.
        "--start_time", _utc(meta["timestamp"]).strftime("%Y-%m-%dT%H:%M:%S"),
    ]
    # gr_satellites defaults to 5 kHz deviation. KOSTKA's actual deviation is
    # not published (AMSAT ANS-186 gives only the baudrate), and 9k6 G3RUH is
    # commonly run nearer 3 kHz — so this is the first knob to turn if a
    # recording that looks healthy decodes to nothing.
    if KOSTKA_DEVIATION_HZ:
        cmd += ["--deviation", str(KOSTKA_DEVIATION_HZ)]
    log.info("Decoding KOSTKA: %s", " ".join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    output = f"$ {' '.join(cmd)}\n\n{result.stdout or ''}{result.stderr or ''}"
    if result.returncode != 0 and not kiss_path.exists():
        raise RuntimeError(
            f"gr_satellites exited {result.returncode}: {(result.stderr or '').strip()[-300:]}"
        )
    return output


# ── Frame parsing ─────────────────────────────────────────────────────────────

KISS_FEND = 0xC0
KISS_FESC = 0xDB
KISS_TFEND = 0xDC
KISS_TFESC = 0xDD


def parse_frames(kiss_path: Path) -> dict:
    """Turn gr-satellites' KISS output into counted, addressed AX.25 frames.

    Every frame in that file already passed its CRC inside gr-satellites — a
    G3RUH AX.25 frame is either valid or was never emitted — so unlike Orbcomm
    there is no packet error rate to weigh, only a count.
    """
    if not kiss_path.is_file():
        return {"packets": 0, "frames": [], "callsigns": []}

    frames, callsigns = [], []
    for payload in _kiss_frames(kiss_path.read_bytes()):
        addressed = _ax25_addresses(payload)
        if addressed is None:
            continue
        source, dest, info = addressed
        callsigns.append(source)
        frames.append({
            "from": source,
            "to": dest,
            "bytes": len(payload),
            "info": info,
        })

    return {
        "packets": len(frames),
        "frames": frames,
        "callsigns": sorted(set(callsigns)),
    }


def _kiss_frames(data: bytes):
    """Yield the payload of each KISS frame, unescaped, dropping the port byte."""
    for chunk in data.split(bytes([KISS_FEND])):
        if len(chunk) < 2:
            continue
        out = bytearray()
        escaped = False
        for byte in chunk[1:]:  # chunk[0] is the KISS command/port byte
            if escaped:
                out.append(KISS_FEND if byte == KISS_TFEND else
                           KISS_FESC if byte == KISS_TFESC else byte)
                escaped = False
            elif byte == KISS_FESC:
                escaped = True
            else:
                out.append(byte)
        if out:
            yield bytes(out)


def _ax25_addresses(frame: bytes):
    """(source, destination, printable info) of an AX.25 UI frame, or None.

    AX.25 addresses are 7 bytes: six callsign characters shifted left one bit,
    then an SSID byte. Destination comes first, then source.
    """
    if len(frame) < 15:
        return None
    dest = _callsign(frame[0:7])
    source = _callsign(frame[7:14])
    if not dest or not source:
        return None
    # Skip any digipeater addresses (bit 0 of the SSID byte marks the last one),
    # then the control and PID bytes.
    idx = 14
    while idx >= 7 and not frame[idx - 1] & 0x01 and idx + 7 <= len(frame):
        idx += 7
    info = frame[idx + 2:] if len(frame) > idx + 2 else b""
    printable = info.decode("ascii", errors="replace").strip()
    return source, dest, printable[:200]


def _callsign(field: bytes) -> str:
    """Decode one 7-byte AX.25 address field to CALL-SSID."""
    call = "".join(chr(b >> 1) for b in field[:6]).strip()
    if not call.replace("-", "").isalnum():
        return ""
    ssid = (field[6] >> 1) & 0x0F
    return f"{call}-{ssid}" if ssid else call


def write_summary(products_dir: Path, stats: dict) -> None:
    """Mirror orbcomm/aprs: a telemetry.json the dashboard can read."""
    products_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "packets": stats.get("packets", 0),
        "callsigns": stats.get("callsigns", []),
        "frames": stats.get("frames", [])[:50],
        "doppler": stats.get("doppler", {}),
    }
    (products_dir / "telemetry.json").write_text(json.dumps(out, indent=2))
