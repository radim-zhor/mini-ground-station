"""Tests for the recordings retention policy (M2.4)."""
import json
import os

import pytest

from agent import retention


@pytest.fixture
def recordings(tmp_path):
    """A recordings/ dir of its own — tmp_path itself also holds test fixtures."""
    d = tmp_path / "recordings"
    d.mkdir()
    return d


def make_pass(root, name, iq_bytes=1000, mtime=None, products=False):
    """Create a pass directory the way the recorder does."""
    d = root / name
    d.mkdir(parents=True)
    (d / f"{name}.cs16").write_bytes(b"\0" * iq_bytes)
    (d / "meta.json").write_text(json.dumps({"satellite": name, "format": "cs16"}))
    if products:
        (d / "products").mkdir()
        (d / "products" / "image.png").write_bytes(b"\0" * 100)
    if mtime is not None:
        os.utime(d / "meta.json", (mtime, mtime))
    return d


# ── Rule 1: IQ goes on success, stays on failure ──────────────────────────────

def test_successful_decode_drops_iq_but_keeps_products(recordings):
    d = make_pass(recordings, "METEOR_M2-4_1", products=True)

    retention.after_decode(d, success=True)

    assert list(d.glob("*.cs16")) == []
    assert (d / "products" / "image.png").exists()
    assert (d / "meta.json").exists()


def test_failed_decode_keeps_iq_and_logs_why(recordings, caplog):
    d = make_pass(recordings, "METEOR_M2-4_1")

    with caplog.at_level("WARNING"):
        retention.after_decode(d, success=False, reason="satdump not found")

    assert (d / "METEOR_M2-4_1.cs16").exists()
    assert "satdump not found" in caplog.text


# ── Rule 2: hard cap on the directory ─────────────────────────────────────────

def test_cap_drops_oldest_passes_first(recordings):
    old = make_pass(recordings, "old", iq_bytes=1000, mtime=1000)
    mid = make_pass(recordings, "mid", iq_bytes=1000, mtime=2000)
    new = make_pass(recordings, "new", iq_bytes=1000, mtime=3000)

    freed = retention.enforce_cap(recordings, limit_bytes=2500)

    assert not old.exists()
    assert mid.exists() and new.exists()
    assert freed >= 1000


def test_cap_under_limit_deletes_nothing(recordings):
    d = make_pass(recordings, "keep-me", iq_bytes=1000)
    assert retention.enforce_cap(recordings, limit_bytes=10_000) == 0
    assert d.exists()


def test_cap_never_touches_the_current_recording(recordings):
    current = make_pass(recordings, "current", iq_bytes=5000, mtime=1000)  # oldest
    other = make_pass(recordings, "other", iq_bytes=1000, mtime=3000)

    retention.enforce_cap(recordings, limit_bytes=1000, keep=current)

    assert current.exists()
    assert not other.exists()


def test_cap_never_touches_files_the_agent_did_not_write(recordings):
    # Hand-made recordings and reference images live in recordings/ too.
    manual = recordings / "noaa18_reference.png"
    manual.write_bytes(b"\0" * 5000)
    stray_dir = recordings / "sdrpp_session"
    stray_dir.mkdir()
    (stray_dir / "audio.wav").write_bytes(b"\0" * 5000)
    ours = make_pass(recordings, "METEOR_M2-4_1", iq_bytes=1000)

    retention.enforce_cap(recordings, limit_bytes=100)

    assert manual.exists()
    assert (stray_dir / "audio.wav").exists()
    assert not ours.exists()


def test_cap_warns_when_it_cannot_free_enough(recordings, caplog):
    (recordings / "untouchable.wav").write_bytes(b"\0" * 5000)

    with caplog.at_level("WARNING"):
        retention.enforce_cap(recordings, limit_bytes=100)

    assert "over the" in caplog.text


def test_cap_on_missing_directory_is_a_no_op(tmp_path):
    assert retention.enforce_cap(tmp_path / "does-not-exist") == 0


# ── Sizing helpers ────────────────────────────────────────────────────────────

def test_max_bytes_reads_env(monkeypatch):
    monkeypatch.setenv("RECORDINGS_MAX_GB", "5")
    assert retention.max_bytes() == 5_000_000_000

    monkeypatch.setenv("RECORDINGS_MAX_GB", "nonsense")
    assert retention.max_bytes() == int(retention.DEFAULT_MAX_GB * 1e9)


def test_has_room_for_rejects_a_pass_bigger_than_the_cap(recordings, monkeypatch, caplog):
    monkeypatch.setenv("RECORDINGS_MAX_GB", "1")

    with caplog.at_level("WARNING"):
        # ~3 GB, i.e. one 10-minute Orbcomm pass at 1.2288 Msps.
        assert retention.has_room_for(recordings, 3_000_000_000) is False

    assert "RECORDINGS_MAX_GB" in caplog.text
    assert retention.has_room_for(recordings, 100) is True


@pytest.mark.parametrize("expected", [0, 999])
def test_has_room_for_small_recordings(recordings, expected):
    assert retention.has_room_for(recordings, expected) is True
