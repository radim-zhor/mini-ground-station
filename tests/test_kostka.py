"""Tests for agent/kostka.py — Doppler correction and AX.25 frame parsing.

The two things this module does that no other decoder path does: it mixes the
signal to DC itself (because ±10 kHz of Doppler at 436 MHz is half the width of
a 9k6 GFSK signal), and it reads KISS output rather than a decoder's printout.
"""
import json
import re
from types import SimpleNamespace

import numpy as np
import pytest

from agent import kostka


@pytest.fixture
def recording(tmp_path):
    """A KOSTKA pass directory holding a synthetic tone at the parking offset."""
    def make(duration_s=4.0, rate=250_000, tone_hz=kostka.KOSTKA_OFFSET_HZ,
             satellite="KOSTKA"):
        d = tmp_path / "KOSTKA_pass"
        d.mkdir(exist_ok=True)
        n = int(rate * duration_s)
        t = np.arange(n) / rate
        iq = 10_000 * np.exp(2j * np.pi * tone_hz * t)
        raw = np.empty(2 * n, dtype=np.int16)
        raw[0::2] = iq.real
        raw[1::2] = iq.imag
        iq_path = d / "kostka.cs16"
        raw.tofile(iq_path)

        meta = {
            "satellite": satellite,
            "center_freq_hz": kostka.KOSTKA_CENTER_HZ,
            "sample_rate": rate,
            "timestamp": 1_784_673_822.0,
            "duration_s": duration_s,
            "observer": {"lat": 49.2, "lon": 16.8},
        }
        (d / "meta.json").write_text(json.dumps(meta))
        return d, iq_path, meta

    return make


# ── Doppler correction ────────────────────────────────────────────────────────

def _peak_offset_hz(iq_path, rate):
    """Frequency of the strongest component in a .cs16 file."""
    raw = np.fromfile(iq_path, dtype=np.int16)
    iq = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
    spectrum = np.abs(np.fft.fft(iq))
    freqs = np.fft.fftfreq(len(iq), 1 / rate)
    return float(freqs[int(np.argmax(spectrum))])


def test_correction_moves_the_signal_from_the_parking_offset_to_dc(recording, monkeypatch):
    # The dongle is parked below the downlink, so the signal arrives at
    # +KOSTKA_OFFSET_HZ. gr-satellites expects it at DC.
    monkeypatch.setattr(kostka, "doppler_shift_hz", lambda *a, **kw: None)
    pass_dir, iq_path, meta = recording()
    out = pass_dir / "corrected.cs16"

    before = _peak_offset_hz(iq_path, meta["sample_rate"])
    summary = kostka.doppler_correct(iq_path, meta, out)
    after = _peak_offset_hz(out, meta["sample_rate"])

    assert abs(before - kostka.KOSTKA_OFFSET_HZ) < 100
    assert abs(after) < 100
    assert summary["offset_hz"] == float(kostka.KOSTKA_OFFSET_HZ)
    assert summary["from_tle"] is False
    assert out.stat().st_size == iq_path.stat().st_size


def test_correction_tracks_a_real_doppler_ramp(recording):
    # A signal that drifts the way a pass does must come out flat at DC, not
    # merely centred on average.
    rate, duration, ramp_hz_per_s = 250_000, 4.0, 2_000.0
    pass_dir, iq_path, meta = recording(duration_s=duration, rate=rate)

    n = int(rate * duration)
    t = np.arange(n) / rate
    phase = 2 * np.pi * (kostka.KOSTKA_OFFSET_HZ * t + ramp_hz_per_s * t**2 / 2)
    iq = 10_000 * np.exp(1j * phase)
    raw = np.empty(2 * n, dtype=np.int16)
    raw[0::2], raw[1::2] = iq.real, iq.imag
    raw.tofile(iq_path)

    # Stand in for skyfield with the exact ramp we synthesised.
    kostka_shift = lambda meta_, elapsed, downlink: ramp_hz_per_s * np.asarray(elapsed)  # noqa: E731
    original = kostka.doppler_shift_hz
    kostka.doppler_shift_hz = kostka_shift
    try:
        out = pass_dir / "corrected.cs16"
        summary = kostka.doppler_correct(iq_path, meta, out)
    finally:
        kostka.doppler_shift_hz = original

    assert summary["from_tle"] is True
    assert summary["shift_peak_hz"] == pytest.approx(ramp_hz_per_s * duration, rel=0.01)
    # Residual across the *whole* file — an uncorrected ramp would smear the
    # peak away from DC instead of leaving it there.
    assert abs(_peak_offset_hz(out, rate)) < 100


def test_doppler_shift_is_signed_and_plausible_for_a_leo_pass(recording):
    # Real skyfield, real TLE from the fixture: the magnitude must land in the
    # ±10 kHz a 436 MHz LEO downlink actually sweeps — not ±1 kHz, not ±100.
    _, _, meta = recording()
    shift = kostka.doppler_shift_hz(meta, np.arange(0.0, 600.0, 30.0),
                                    float(kostka.KOSTKA_DOWNLINK_HZ))
    assert shift is not None
    assert 1_000 < np.max(np.abs(shift)) < 15_000


def test_doppler_falls_back_to_the_fixed_offset_without_a_tle(recording):
    # No TLE is not a reason to throw the recording away: the fixed offset is
    # most of the correction, and a high pass sits near zero Doppler for a while.
    pass_dir, iq_path, meta = recording(satellite="NOT A TRACKED SATELLITE")
    summary = kostka.doppler_correct(iq_path, meta, pass_dir / "corrected.cs16")

    assert summary["from_tle"] is False
    assert summary["shift_peak_hz"] == 0.0


def test_short_recording_is_rejected_rather_than_decoded(recording):
    _, iq_path, meta = recording(duration_s=0.1)
    with pytest.raises(ValueError, match="too short"):
        kostka.doppler_correct(iq_path, meta, iq_path.parent / "out.cs16")


# ── Frame parsing ─────────────────────────────────────────────────────────────

def _kiss(payload: bytes) -> bytes:
    escaped = payload.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")
    return b"\xc0\x00" + escaped + b"\xc0"


def _ax25(source: str, dest: str, info: bytes) -> bytes:
    def addr(call: str, last: bool) -> bytes:
        padded = call.ljust(6)[:6]
        return bytes(ord(c) << 1 for c in padded) + bytes([0x60 | (1 if last else 0)])

    return addr(dest, False) + addr(source, True) + b"\x03\xf0" + info


def test_parses_kiss_frames_with_callsigns(tmp_path):
    path = tmp_path / "kostka.kiss"
    path.write_bytes(
        _kiss(_ax25("OK0KOS", "CQ", b"KOSTKA telemetry"))
        + _kiss(_ax25("OK0KOS", "CQ", b"second frame"))
    )
    stats = kostka.parse_frames(path)

    assert stats["packets"] == 2
    assert stats["callsigns"] == ["OK0KOS"]
    assert stats["frames"][0]["from"] == "OK0KOS"
    assert stats["frames"][0]["to"] == "CQ"
    assert "KOSTKA telemetry" in stats["frames"][0]["info"]


def test_kiss_escaping_is_undone(tmp_path):
    # 0xC0 inside a payload is escaped as 0xDB 0xDC; reading it literally would
    # split one frame into two and inflate the packet count.
    path = tmp_path / "kostka.kiss"
    path.write_bytes(_kiss(_ax25("OK0KOS", "CQ", b"\xc0\xdb ok")))
    stats = kostka.parse_frames(path)

    assert stats["packets"] == 1
    assert stats["frames"][0]["bytes"] == len(_ax25("OK0KOS", "CQ", b"\xc0\xdb ok"))


def test_missing_kiss_output_is_zero_frames_not_a_crash(tmp_path):
    stats = kostka.parse_frames(tmp_path / "nothing.kiss")
    assert stats == {"packets": 0, "frames": [], "callsigns": []}


def test_garbage_is_not_counted_as_a_frame(tmp_path):
    path = tmp_path / "kostka.kiss"
    path.write_bytes(_kiss(b"\x00\x01\x02\x03"))
    assert kostka.parse_frames(path)["packets"] == 0


# ── Failure modes ─────────────────────────────────────────────────────────────

def test_missing_gr_satellites_is_reported_not_raised(recording, monkeypatch):
    monkeypatch.setattr(kostka, "is_available", lambda: False)
    pass_dir, _, meta = recording()
    stats = kostka.decode(pass_dir, meta, pass_dir / "products")

    assert "gr_satellites not found" in stats["error"]


def test_command_line_matches_what_gr_satellites_expects(recording, monkeypatch):
    # --rawint16 IS the int16 input-file option, not a modifier of --rawfile
    # (which is float32/complex64). Passing both puts a filename where
    # gr_satellites expects none and the decode fails on argument parsing —
    # long after the pass is over.
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(kostka.subprocess, "run", fake_run)
    pass_dir, _, meta = recording()
    kostka._run_gr_satellites(pass_dir / "iq.cs16", 250_000,
                              pass_dir / "out.kiss", meta)

    cmd = seen["cmd"]
    assert cmd[0] == "gr_satellites"
    assert cmd[1] == str(kostka.SATYAML)
    assert "--rawfile" not in cmd
    assert cmd[cmd.index("--rawint16") + 1].endswith("iq.cs16")
    assert "--iq" in cmd
    assert cmd[cmd.index("--samp_rate") + 1] == "250000.0"
    # There is no --f_offset for FSK with IQ input (AFSK-only); passing it
    # fails on unrecognized arguments.
    assert "--f_offset" not in cmd
    # We mixed the signal onto DC on purpose — the default DC blocker would
    # notch out its middle.
    assert "--disable_dc_block" in cmd
    # Documented format is plain seconds — no microseconds, no UTC offset.
    start = cmd[cmd.index("--start_time") + 1]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", start)


def test_the_corrected_copy_is_cleaned_up(recording, monkeypatch):
    # It is the same size as the recording — leaving it behind would double the
    # disk cost of every pass, silently.
    monkeypatch.setattr(kostka, "is_available", lambda: True)
    monkeypatch.setattr(kostka, "_run_gr_satellites",
                        lambda *a, **kw: "no frames")
    pass_dir, _, meta = recording()
    kostka.decode(pass_dir, meta, pass_dir / "products")

    assert not (pass_dir / "kostka_doppler.cs16").exists()
