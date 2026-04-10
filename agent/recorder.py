"""
recorder.py — RTL-SDR satellite pass recorder

Real mode:  pyrtlsdr → FM demodulate → resample to 48 kHz → mono WAV
Mock mode:  synthetic APT-like noise WAV (no hardware required, set MOCK=1)
"""
import os
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly, welch

SAMPLE_RATE_SDR = 1_024_000   # RTL-SDR capture rate (Hz)
SAMPLE_RATE_OUT = 48_000      # Output WAV rate (Hz) — sufficient for APT
RESAMPLE_UP = 3
RESAMPLE_DOWN = 64            # 1_024_000 * 3/64 = 48_000

RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"


def record(frequency_hz: int, duration_s: int, satellite: str) -> Path:
    """
    Record a satellite pass and return path to the WAV file.

    Args:
        frequency_hz:  Centre frequency to tune to (e.g. 137_912_500)
        duration_s:    Recording length in seconds (typically pass duration)
        satellite:     Satellite name used in the output filename

    Returns:
        Path to the saved WAV file.
    """
    RECORDINGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = satellite.replace(" ", "_")
    out_path = RECORDINGS_DIR / f"{safe_name}_{timestamp}.wav"

    if os.getenv("MOCK"):
        return _mock_record(out_path, duration_s)

    return _rtlsdr_record(frequency_hz, duration_s, out_path)


def measure_snr(wav_path: Path) -> float:
    """
    Estimate SNR of an APT recording in dB.

    Compares power in the APT tone band (2000–2800 Hz) against
    the noise floor above it (4000–8000 Hz).
    Returns 0.0 on failure.
    """
    try:
        with wave.open(str(wav_path), "r") as wf:
            frames = wf.readframes(wf.getnframes())
            rate = wf.getframerate()
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        f, psd = welch(audio, fs=rate, nperseg=2048)
        sig_power = np.mean(psd[(f >= 2000) & (f <= 2800)])
        noise_power = np.mean(psd[(f >= 4000) & (f <= 8000)])
        if noise_power == 0:
            return 0.0
        return round(10 * np.log10(sig_power / noise_power), 1)
    except Exception:
        return 0.0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rtlsdr_record(frequency_hz: int, duration_s: int, out_path: Path) -> Path:
    from rtlsdr import RtlSdr  # imported lazily — not needed in mock/web-app

    sdr = RtlSdr()
    sdr.sample_rate = SAMPLE_RATE_SDR
    sdr.center_freq = frequency_hz
    sdr.gain = 49.6

    total_samples = SAMPLE_RATE_SDR * duration_s
    chunk_size = SAMPLE_RATE_SDR  # process 1 second at a time
    audio_chunks: list[np.ndarray] = []
    samples_read = 0

    try:
        while samples_read < total_samples:
            n = min(chunk_size, total_samples - samples_read)
            raw = np.array(sdr.read_samples(n), dtype=np.complex64)
            demod = _demodulate_fm(raw)
            resampled = resample_poly(demod, RESAMPLE_UP, RESAMPLE_DOWN)
            audio_chunks.append(resampled.astype(np.float32))
            samples_read += n
    finally:
        sdr.close()

    audio = np.concatenate(audio_chunks)
    _save_wav(out_path, audio)
    return out_path


def _demodulate_fm(iq: np.ndarray) -> np.ndarray:
    """FM demodulate complex IQ samples via angle of successive product."""
    diff = iq[1:] * np.conj(iq[:-1])
    return np.angle(diff).astype(np.float32)


def _mock_record(out_path: Path, duration_s: int) -> Path:
    """Generate synthetic APT-like signal (2400 + 2600 Hz tones in noise)."""
    rng = np.random.default_rng(42)
    n = SAMPLE_RATE_OUT * duration_s
    t = np.linspace(0, duration_s, n, endpoint=False)
    audio = (
        0.4 * np.sin(2 * np.pi * 2400 * t)
        + 0.4 * np.sin(2 * np.pi * 2600 * t)
        + rng.standard_normal(n) * 0.2
    ).astype(np.float32)
    _save_wav(out_path, audio)
    return out_path


def _save_wav(path: Path, audio: np.ndarray) -> None:
    """Normalise and save float32 audio array as 16-bit mono WAV."""
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE_OUT)
        wf.writeframes(pcm.tobytes())
