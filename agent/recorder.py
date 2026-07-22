"""
recorder.py — RTL-SDR baseband IQ recorder

Real mode:  pyrtlsdr → interleaved 16-bit IQ (`.cs16`), streamed to disk
Mock mode:  synthetic IQ (no hardware required, set MOCK=1)

Why IQ and not demodulated audio: FM demodulation destroys the phase
information that Meteor (OQPSK) and Orbcomm (SDPSK) carry the data in. The
recorder therefore stores raw baseband and leaves every decision about
demodulation to the decoder, which does not have to keep up with the pass.

Each pass gets its own directory:

    recordings/METEOR_M2-4_20260722_183000/
    ├── METEOR_M2-4_20260722_183000.cs16   # interleaved int16 I,Q
    ├── meta.json                          # what the decoders need to know
    └── products/                          # written later by the decoder

The `.cs16` file is a flat sample stream with no header, so it can be handed
to SatDump (`--baseband_format s16`) or read with `np.fromfile(..., np.int16)`
without conversion.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from scipy.signal import welch

RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"

DEFAULT_SAMPLE_RATE = 1_000_000  # Hz, used when the caller has no preference
CHUNK_SECONDS = 0.5              # read granularity → callback fires ~2×/s
BYTES_PER_SAMPLE = 4             # int16 I + int16 Q
SNR_FFT_SAMPLES = 65_536         # per-chunk slice used for the SNR estimate

log = logging.getLogger(__name__)


@dataclass
class Recording:
    """Everything the decoder and the contact report need about one capture."""

    path: Path                    # the .cs16 file
    pass_dir: Path                # directory holding IQ, meta.json and products/
    satellite: str
    center_freq_hz: int
    sample_rate: int
    started_at: datetime
    duration_s: float             # actually captured, may be < requested
    avg_snr: float = 0.0
    peak_snr: float = 0.0
    snr_series: list = field(default_factory=list)  # [(elapsed_s, snr_db), ...]

    @property
    def products_dir(self) -> Path:
        return self.pass_dir / "products"


# ── Public API ────────────────────────────────────────────────────────────────

def record_iq(
    frequency_hz: int,
    duration_s: int,
    satellite: str,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    progress_cb: Optional[Callable[[float, float, float], None]] = None,
    observer: Optional[tuple] = None,
) -> Recording:
    """
    Record a pass as baseband IQ and return the resulting :class:`Recording`.

    Args:
        frequency_hz:  Centre frequency to tune to (e.g. 137_900_000)
        duration_s:    Recording length in seconds (typically AOS → LOS)
        satellite:     Satellite name, used for the pass directory name
        sample_rate:   SDR sample rate in Hz (per satellite — see scheduler)
        progress_cb:   Called after every chunk with (elapsed_s, total_s,
                       snr_db). Must not raise; exceptions are logged and
                       swallowed so a broken reporter never kills a pass.
        observer:      (lat, lon) of the station at recording time. The Orbcomm
                       decoder needs it to check the ephemeris against a known
                       position; the station moves, so it belongs in the file.

    Returns:
        Recording with the SNR profile measured while capturing — the file is
        far too large to measure again afterwards.
    """
    started_at = datetime.now(timezone.utc)
    safe_name = satellite.replace(" ", "_").replace("/", "-")
    stem = f"{safe_name}_{started_at.strftime('%Y%m%d_%H%M%S')}"

    pass_dir = RECORDINGS_DIR / stem
    pass_dir.mkdir(parents=True, exist_ok=True)
    iq_path = pass_dir / f"{stem}.cs16"

    log.info(
        "Recording %s: %.4f MHz, %.4f Msps, %d s ≈ %.1f GB",
        satellite,
        frequency_hz / 1e6,
        sample_rate / 1e6,
        duration_s,
        sample_rate * duration_s * BYTES_PER_SAMPLE / 1e9,
    )

    rec = Recording(
        path=iq_path,
        pass_dir=pass_dir,
        satellite=satellite,
        center_freq_hz=frequency_hz,
        sample_rate=sample_rate,
        started_at=started_at,
        duration_s=0.0,
    )

    reader = _mock_chunks if os.getenv("MOCK") else _sdr_chunks
    total_samples = int(sample_rate * duration_s)
    written = 0
    snrs: list = []

    with iq_path.open("wb") as f:
        for chunk in reader(frequency_hz, sample_rate, total_samples):
            f.write(chunk.tobytes())
            written += len(chunk) // 2
            elapsed = written / sample_rate
            snr = _snr_of_iq(chunk, sample_rate)
            snrs.append(snr)
            rec.snr_series.append((round(elapsed, 1), snr))
            _report(progress_cb, elapsed, float(duration_s), snr)

    rec.duration_s = round(written / sample_rate, 1)
    if snrs:
        rec.avg_snr = round(float(np.mean(snrs)), 1)
        rec.peak_snr = round(float(np.max(snrs)), 1)

    _write_meta(rec, observer)
    log.info(
        "Saved %s (%.1f s, %.2f GB, SNR avg %.1f / peak %.1f dB)",
        iq_path.name,
        rec.duration_s,
        iq_path.stat().st_size / 1e9,
        rec.avg_snr,
        rec.peak_snr,
    )
    return rec


def read_meta(pass_dir: Path) -> dict:
    """Read the meta.json sidecar of a pass directory ({} if unreadable)."""
    try:
        with (pass_dir / "meta.json").open() as f:
            return json.load(f)
    except Exception:
        return {}


# ── SDR capture ───────────────────────────────────────────────────────────────

def _sdr_chunks(frequency_hz: int, sample_rate: int, total_samples: int):
    """Yield int16 interleaved IQ chunks straight from the dongle."""
    from rtlsdr import RtlSdr  # imported lazily — not needed in mock/web-app

    sdr = RtlSdr()
    try:
        _configure_sdr(sdr, frequency_hz, sample_rate)
        chunk_samples = _chunk_samples(sample_rate)
        read = 0
        while read < total_samples:
            n = min(chunk_samples, total_samples - read)
            n = max(n - n % 512, 512)  # librtlsdr wants a multiple of 512
            # read_bytes gives the raw uint8 pairs; converting those directly
            # to int16 skips pyrtlsdr's float64 round-trip, which matters at
            # 1.2288 Msps.
            raw = np.frombuffer(sdr.read_bytes(2 * n), dtype=np.uint8)
            yield (raw.astype(np.int16) - 127) << 7
            read += n
    finally:
        sdr.close()


def _configure_sdr(sdr, frequency_hz: int, sample_rate: int) -> None:
    """Tune the dongle and log the settings that silently ruin a pass."""
    sdr.sample_rate = sample_rate
    sdr.center_freq = frequency_hz

    # Manual gain, never AGC: with an LNA ahead of the tuner, AGC rides the
    # gain up on a high pass and overloads. 20.7 dB is the value verified
    # against Meteor and Orbcomm carriers on this station.
    gain_env = os.getenv("SDR_GAIN", "20.7")
    if gain_env.lower() == "auto":
        sdr.gain = "auto"
        log.warning("SDR gain: AGC — manual gain is strongly preferred with an LNA")
    else:
        sdr.gain = float(gain_env)
        log.info("SDR gain: %.1f dB (manual)", float(gain_env))

    # The LNA is bias-tee powered; measured +14 dB with this on. Without it
    # the LNA is an unpowered attenuator — the single most expensive mistake
    # of the 17.–22. 7. sessions.
    want_bias = os.getenv("SDR_BIAS_TEE", "1") != "0"
    try:
        sdr.set_bias_tee(want_bias)
        log.info("Bias tee: %s", "ON" if want_bias else "off")
    except Exception as e:
        log.warning("Bias tee could not be set (%s) — LNA may be unpowered", e)


def _chunk_samples(sample_rate: int) -> int:
    return max(int(sample_rate * CHUNK_SECONDS), 1024)


# ── Mock capture ──────────────────────────────────────────────────────────────

def _mock_chunks(frequency_hz: int, sample_rate: int, total_samples: int):
    """Synthetic IQ: an offset carrier in noise, same layout as the real path."""
    rng = np.random.default_rng(42)
    chunk_samples = _chunk_samples(sample_rate)
    read = 0
    while read < total_samples:
        n = min(chunk_samples, total_samples - read)
        t = np.arange(read, read + n) / sample_rate
        tone = 8000 * np.exp(2j * np.pi * 50_000 * t)
        noise = rng.standard_normal(n) + 1j * rng.standard_normal(n)
        iq = tone + 800 * noise
        out = np.empty(2 * n, dtype=np.int16)
        out[0::2] = np.clip(iq.real, -32768, 32767).astype(np.int16)
        out[1::2] = np.clip(iq.imag, -32768, 32767).astype(np.int16)
        yield out
        read += n


# ── Helpers ───────────────────────────────────────────────────────────────────

def _snr_of_iq(chunk: np.ndarray, sample_rate: int) -> float:
    """
    SNR in dB of an interleaved int16 IQ chunk: strongest spectral component
    against the noise floor (median PSD).

    Deliberately signal-agnostic — it has to work for a narrow Orbcomm carrier
    and a ~120 kHz wide Meteor downlink alike, and it only has to be
    *comparable across the pass* to show the rise towards TCA.
    """
    try:
        n = min(len(chunk) // 2, SNR_FFT_SAMPLES)
        if n < 1024:
            return 0.0
        iq = chunk[: 2 * n : 2].astype(np.float32) + 1j * chunk[1 : 2 * n : 2].astype(np.float32)
        _, psd = welch(iq, fs=sample_rate, nperseg=1024, return_onesided=False)
        floor = float(np.median(psd))
        if floor <= 0:
            return 0.0
        return round(float(10 * np.log10(float(np.max(psd)) / floor)), 1)
    except Exception:
        return 0.0


def _report(progress_cb, elapsed: float, total: float, snr: float) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(elapsed, total, snr)
    except Exception:
        log.exception("progress callback failed — recording continues")


def _write_meta(rec: Recording, observer: Optional[tuple] = None) -> None:
    """
    Sidecar with everything a decoder needs; also marks the directory as ours,
    which is what the retention policy keys off (see agent/retention.py).
    """
    lat, lon = observer if observer else (_env_float("OBSERVER_LAT"), _env_float("OBSERVER_LON"))
    meta = {
        "satellite": rec.satellite,
        "center_freq_hz": rec.center_freq_hz,
        "sample_rate": rec.sample_rate,
        "format": "cs16",
        "started_at": rec.started_at.isoformat(),
        "timestamp": rec.started_at.timestamp(),
        "duration_s": rec.duration_s,
        "avg_snr": rec.avg_snr,
        "peak_snr": rec.peak_snr,
        "snr_series": rec.snr_series,
        "gain": os.getenv("SDR_GAIN", "20.7"),
        "bias_tee": os.getenv("SDR_BIAS_TEE", "1") != "0",
        "observer": {"lat": lat, "lon": lon},
        "written_at": time.time(),
    }
    with (rec.pass_dir / "meta.json").open("w") as f:
        json.dump(meta, f, indent=2)


def _env_float(name: str) -> Optional[float]:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None
