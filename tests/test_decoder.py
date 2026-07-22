"""Tests for the decoding dispatcher (M2.2)."""
import json
import subprocess

import pytest

from agent import decoder, orbcomm


@pytest.fixture
def pass_dir(tmp_path):
    """A recorded pass directory as the recorder leaves it."""
    def make(satellite="METEOR M2-4", **meta_overrides):
        d = tmp_path / "recordings" / satellite.replace(" ", "_")
        d.mkdir(parents=True)
        (d / "iq.cs16").write_bytes(b"\0" * 4096)
        meta = {
            "satellite": satellite,
            "center_freq_hz": 137_900_000,
            "sample_rate": 1_000_000,
            "format": "cs16",
            "timestamp": 1_784_673_822.0,
            "duration_s": 60.0,
            "snr_series": [[10.0, 3.0], [20.0, 12.0], [30.0, 5.0]],
            "observer": {"lat": 49.2, "lon": 16.8},
        }
        meta.update(meta_overrides)
        (d / "meta.json").write_text(json.dumps(meta))
        return d

    return make


# ── Dispatch ──────────────────────────────────────────────────────────────────

def test_dispatches_meteor_to_satdump(pass_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(decoder, "satdump_binary", lambda: "/fake/satdump")

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["cwd"] = kw.get("cwd")
        (pass_dir_path / "products" / "MSU-MR.png").write_bytes(b"\x89PNG")
        return subprocess.CompletedProcess(cmd, 0, "decoded", "")

    pass_dir_path = pass_dir("METEOR M2-4")
    monkeypatch.setattr(decoder.subprocess, "run", fake_run)

    result = decoder.decode(pass_dir_path)

    assert result.success
    assert result.kind == "image"
    assert result.image.name == "MSU-MR.png"
    assert seen["cmd"][1] == decoder.METEOR_PIPELINE
    assert "--baseband_format" in seen["cmd"] and "s16" in seen["cmd"]
    assert "--samplerate" in seen["cmd"] and "1000000" in seen["cmd"]


def test_dispatches_orbcomm_to_the_vendored_decoder(pass_dir, monkeypatch):
    called = {}

    def fake_decode(pd, meta, products_dir):
        called["satellite"] = meta["satellite"]
        products_dir.mkdir(parents=True, exist_ok=True)
        return {"packets": 98, "per": 1.0, "ephemeris": [{"lat": 46.7, "lon": 16.4}]}

    monkeypatch.setattr(decoder.orbcomm, "decode", fake_decode)
    result = decoder.decode(pass_dir("ORBCOMM FM 118"))

    assert result.success
    assert result.kind == "telemetry"
    assert called["satellite"] == "ORBCOMM FM 118"
    assert "98 packets" in result.notes and "PER 1.0%" in result.notes
    assert result.image is None  # telemetry has no picture to show


def test_unknown_satellite_is_reported_not_raised(pass_dir):
    result = decoder.decode(pass_dir("ISS (ZARYA)"))
    assert result.success is False
    assert "no decoder for ISS (ZARYA)" in result.notes


def test_missing_meta_is_reported(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = decoder.decode(empty)
    assert result.success is False
    assert "no meta.json" in result.notes


# ── Failures must never cost the agent or the recording ───────────────────────

def test_missing_satdump_is_a_note_not_a_crash(pass_dir, monkeypatch):
    monkeypatch.setattr(decoder, "satdump_binary", lambda: None)
    result = decoder.decode(pass_dir("METEOR M2-4"))

    assert result.success is False
    assert "satdump not found" in result.notes


def test_satdump_failure_keeps_the_iq(pass_dir, monkeypatch):
    d = pass_dir("METEOR M2-4")
    monkeypatch.setattr(decoder, "satdump_binary", lambda: "/fake/satdump")
    monkeypatch.setattr(
        decoder.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "pipeline not found"),
    )

    result = decoder.decode(d)

    assert result.success is False  # → retention keeps the baseband
    assert "pipeline not found" in result.notes
    assert (d / "iq.cs16").exists()


def test_satdump_timeout_is_reported(pass_dir, monkeypatch):
    monkeypatch.setattr(decoder, "satdump_binary", lambda: "/fake/satdump")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, decoder.SATDUMP_TIMEOUT_S)

    monkeypatch.setattr(decoder.subprocess, "run", timeout)
    result = decoder.decode(pass_dir("METEOR M2-4"))

    assert result.success is False
    assert "timed out" in result.notes


def test_a_decoder_that_explodes_does_not_propagate(pass_dir, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("segfault in the decoder")

    monkeypatch.setattr(decoder.orbcomm, "decode", boom)
    result = decoder.decode(pass_dir("ORBCOMM FM 118"))

    assert result.success is False
    assert "segfault" in result.notes


def test_orbcomm_error_is_surfaced(pass_dir, monkeypatch):
    monkeypatch.setattr(
        decoder.orbcomm, "decode", lambda pd, meta, out: {"error": "no TLE available"}
    )
    result = decoder.decode(pass_dir("ORBCOMM FM 118"))

    assert result.success is False
    assert "no TLE available" in result.notes


# ── Orbcomm bridge internals ──────────────────────────────────────────────────

def test_window_offset_follows_the_snr_peak():
    series = [[10.0, 3.0], [20.0, 12.0], [30.0, 5.0]]
    # Peak SNR is measured at the end of the chunk ending at 20 s.
    assert orbcomm.best_window_offset(series, 30.0) == 18.0


def test_window_offset_without_a_profile_uses_mid_pass():
    assert orbcomm.best_window_offset([], 100.0) == 49.0


def test_window_offset_stays_inside_the_recording():
    assert orbcomm.best_window_offset([[1.0, 30.0]], 1.5) == 0.0
    assert orbcomm.best_window_offset([[500.0, 30.0]], 10.0) == 8.0


@pytest.mark.skipif(
    not orbcomm.is_available(),
    reason="vendored orbcomm decoder not checked out (gitignored, so absent in CI)",
)
def test_satnogs_names_map_to_the_decoder_database():
    # The mapping is looked up in the decoder's own satellite database, which
    # also carries the channel frequencies — a bird it does not know cannot be
    # decoded even if we recorded it.
    assert orbcomm.sat_db_name("ORBCOMM FM 118") == "orbcomm fm118"
    assert orbcomm.sat_db_name("METEOR M2-4") is None


def test_satellite_names_resolve_to_nothing_without_the_decoder(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(orbcomm, "RECEIVER_DIR", tmp_path)
    # sat_db is imported off sys.path, so an earlier test may have cached it.
    monkeypatch.delitem(sys.modules, "sat_db", raising=False)

    assert orbcomm.sat_db_name("ORBCOMM FM 118") is None


def test_parse_output_reads_per_and_ephemeris():
    stdout = (
        "Filename: data/x.mat\n"
        "SDR Sample rate: 1228800.0 Hz\n"
        "Remaining frequency offset after doppler compensation: 99.75 Hz\n"
        "\nList of packets: (### indicates checksum failed)\n"
        "Fill: Data: FFFFFF \n"
        "Message: Data: 0102 \n"
        "### Sync: Data: 6500 \n"
        "Ephemeris: Data: AABB \n"
        "\tCurrent satellite time: 2026-07-21 22:43:42 Z\n"
        "\tSat Lat/Lon:    46.7506,  16.3988, Altitude:  702.4 km, Velocity: 7158.5 m/s\n"
        "\tDifference in reported and ephemeris position:   22.7 km\n"
        "Unrecognized packet: 1122\n"
        "1 packets with errors, 0 packets corrected, PER:   1.0%\n"
    )
    stats = orbcomm.parse_output(stdout)

    assert stats["per"] == 1.0
    assert stats["packets_with_errors"] == 1
    assert stats["freq_offset_hz"] == 99.75
    assert stats["packets"] == 5
    assert stats["packet_types"] == {
        "Fill": 1, "Message": 1, "Sync": 1, "Ephemeris": 1, "Unrecognized": 1
    }
    assert stats["ephemeris"] == [{
        "lat": 46.7506, "lon": 16.3988, "alt_km": 702.4, "velocity_ms": 7158.5,
        "satellite_time": "2026-07-21 22:43:42", "ephemeris_diff_km": 22.7,
    }]
    # The preamble above the listing must not be counted as packets.
    assert "SDR Sample rate" not in stats["packet_types"]


def test_missing_vendored_decoder_is_reported(pass_dir, monkeypatch):
    monkeypatch.setattr(orbcomm, "is_available", lambda: False)
    d = pass_dir("ORBCOMM FM 118")
    stats = orbcomm.decode(d, json.loads((d / "meta.json").read_text()), d / "products")
    assert "not found" in stats["error"]
