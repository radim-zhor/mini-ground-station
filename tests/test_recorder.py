"""Tests for the agent recorder — baseband IQ capture (M2.1)."""
import json

import numpy as np
import pytest

from agent import recorder

SAMPLE_RATE = 200_000  # small enough to keep the test files tiny


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setattr(recorder, "RECORDINGS_DIR", tmp_path)
    return tmp_path


def _record(duration_s=2, satellite="METEOR M2-4", **kw):
    return recorder.record_iq(
        frequency_hz=137_900_000,
        duration_s=duration_s,
        satellite=satellite,
        sample_rate=SAMPLE_RATE,
        **kw,
    )


def test_record_iq_writes_cs16_of_expected_size(mock_env):
    rec = _record(duration_s=2)

    assert rec.path.suffix == ".cs16"
    assert rec.path.parent == rec.pass_dir
    # Interleaved int16 I,Q → 4 bytes per complex sample, no header.
    assert rec.path.stat().st_size == SAMPLE_RATE * 2 * recorder.BYTES_PER_SAMPLE
    assert rec.duration_s == pytest.approx(2.0, abs=0.1)


def test_recording_is_readable_as_flat_int16_stream(mock_env):
    # Acceptance criterion: hand the file to SatDump / file_decoder unchanged.
    rec = _record(duration_s=1)
    raw = np.fromfile(rec.path, dtype=np.int16)
    assert len(raw) == 2 * SAMPLE_RATE
    assert np.abs(raw).max() > 0  # not a file full of zeros


def test_pass_directory_holds_meta_and_satellite_name(mock_env):
    rec = _record(satellite="ORBCOMM FM 118")
    assert rec.pass_dir.name.startswith("ORBCOMM_FM_118_")

    meta = json.loads((rec.pass_dir / "meta.json").read_text())
    assert meta["satellite"] == "ORBCOMM FM 118"
    assert meta["sample_rate"] == SAMPLE_RATE
    assert meta["center_freq_hz"] == 137_900_000
    assert meta["format"] == "cs16"
    assert meta["bias_tee"] is True  # on by default — the LNA needs it


def test_meta_records_observer_position(mock_env):
    rec = _record(observer=(49.2, 16.6))
    meta = recorder.read_meta(rec.pass_dir)
    assert meta["observer"] == {"lat": 49.2, "lon": 16.6}


def test_progress_callback_fires_at_least_once_a_second(mock_env):
    calls = []
    rec = _record(duration_s=4, progress_cb=lambda e, t, s: calls.append((e, t, s)))

    assert len(calls) >= 4  # 4 s of recording, callback at least 1×/s
    elapsed = [c[0] for c in calls]
    assert elapsed == sorted(elapsed)
    assert elapsed[-1] == pytest.approx(rec.duration_s, abs=0.1)
    assert all(c[1] == 4.0 for c in calls)  # total is the requested duration


def test_progress_callback_failure_does_not_kill_the_recording(mock_env):
    def broken(elapsed, total, snr):
        raise RuntimeError("reporter is down")

    rec = _record(duration_s=1, progress_cb=broken)
    assert rec.path.exists()


def test_snr_is_measured_while_recording(mock_env):
    # The mock signal is a carrier in noise → clearly positive SNR, and the
    # profile is kept for the live sparkline in M3.
    rec = _record(duration_s=3)
    assert rec.peak_snr > 0
    assert rec.peak_snr >= rec.avg_snr
    # One (elapsed, snr) pair per chunk, ending at the end of the recording.
    assert len(rec.snr_series) == 3 / recorder.CHUNK_SECONDS
    assert rec.snr_series[-1][0] == pytest.approx(rec.duration_s, abs=0.1)


def test_snr_of_noise_only_is_low(mock_env):
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(2 * 65_536) * 500).astype(np.int16)
    tone_t = np.arange(65_536) / SAMPLE_RATE
    tone = np.empty(2 * 65_536, dtype=np.int16)
    tone[0::2] = (8000 * np.cos(2 * np.pi * 50_000 * tone_t)).astype(np.int16)
    tone[1::2] = (8000 * np.sin(2 * np.pi * 50_000 * tone_t)).astype(np.int16)

    assert recorder._snr_of_iq(noise, SAMPLE_RATE) < recorder._snr_of_iq(
        noise + tone, SAMPLE_RATE
    )


def test_read_meta_of_missing_directory_is_empty(tmp_path):
    assert recorder.read_meta(tmp_path / "nope") == {}
