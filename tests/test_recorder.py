"""Tests for the agent recorder — mock recording and SNR measurement."""
import wave

import pytest

from agent import recorder


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setattr(recorder, "RECORDINGS_DIR", tmp_path)
    return tmp_path


def test_mock_record_produces_valid_wav(mock_env):
    path = recorder.record(frequency_hz=137_912_500, duration_s=3, satellite="NOAA 18")
    assert path.exists()
    assert path.suffix == ".wav"

    with wave.open(str(path), "r") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == recorder.SAMPLE_RATE_OUT
        assert wf.getnframes() == recorder.SAMPLE_RATE_OUT * 3


def test_mock_record_filename_has_satellite(mock_env):
    path = recorder.record(frequency_hz=137_620_000, duration_s=1, satellite="NOAA 15")
    assert path.name.startswith("NOAA_15_")


def test_measure_snr_positive_on_tone_signal(mock_env):
    # Mock signal is 2400/2600 Hz tones in the SNR band → positive dB vs noise floor.
    path = recorder.record(frequency_hz=137_620_000, duration_s=4, satellite="NOAA 15")
    snr = recorder.measure_snr(path)
    assert snr > 0


def test_measure_snr_missing_file_returns_zero(tmp_path):
    assert recorder.measure_snr(tmp_path / "does_not_exist.wav") == 0.0


def test_measure_snr_windows_returns_avg_and_peak(mock_env):
    # 25 s recording → two full 10 s windows; both avg and peak positive.
    path = recorder.record(frequency_hz=137_620_000, duration_s=25, satellite="NOAA 15")
    avg, peak = recorder.measure_snr_windows(path)
    assert avg > 0
    assert peak >= avg


def test_measure_snr_windows_short_file_falls_back(mock_env):
    # Shorter than one window → falls back to a whole-file measurement.
    path = recorder.record(frequency_hz=137_620_000, duration_s=3, satellite="NOAA 15")
    avg, peak = recorder.measure_snr_windows(path)
    assert avg == peak
    assert avg > 0


def test_measure_snr_windows_missing_file(tmp_path):
    assert recorder.measure_snr_windows(tmp_path / "nope.wav") == (0.0, 0.0)
