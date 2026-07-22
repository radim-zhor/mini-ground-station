"""
Regression test on a real Orbcomm pass (FM 118, 22 July 2026).

This is the test that proves the whole capture-and-decode chain, not a mock of
it: a genuine recording is written out in *our* `.cs16` + `meta.json` layout and
handed to the dispatcher, which slices a window, bridges it to the vendored
decoder and parses what comes back. It decoded at 0.0 % PER by hand that night;
anything worse here means we broke something.

The recordings live in `orbcomm-receiver/data/`, which is gitignored — CI skips
this test, a local checkout runs it.
"""
import json

import numpy as np
import pytest

from agent.decoder import decode

pytest.importorskip("scipy.io")

# Mid-pass, near TCA: decodes at 0.0 % PER and carries an ephemeris frame.
REFERENCE_MAT = "1784673817p299.mat"


def _reference_recording():
    from agent.orbcomm import RECEIVER_DIR

    return RECEIVER_DIR / "data" / REFERENCE_MAT


requires_reference = pytest.mark.skipif(
    not _reference_recording().is_file(),
    reason=f"reference recording {REFERENCE_MAT} not checked out (gitignored)",
)


def _as_pass_dir(mat_path, dest):
    """Rewrite an upstream .mat recording as a pass directory of ours."""
    from scipy.io import loadmat

    data = loadmat(str(mat_path))
    samples = data["samples"][0]

    iq = np.empty(2 * len(samples), dtype=np.int16)
    iq[0::2] = np.clip(samples.real * 32767, -32768, 32767)
    iq[1::2] = np.clip(samples.imag * 32767, -32768, 32767)

    dest.mkdir(parents=True, exist_ok=True)
    iq.tofile(dest / f"{dest.name}.cs16")

    rate = int(data["fs"][0][0])
    (dest / "meta.json").write_text(json.dumps({
        "satellite": "ORBCOMM FM 118",
        "center_freq_hz": float(data["fc"][0][0]),
        "sample_rate": rate,
        "format": "cs16",
        "timestamp": float(data["timestamp"][0][0]),
        "duration_s": len(samples) / rate,
        "snr_series": [],
        "observer": {
            "lat": float(data["lat"][0][0]),
            "lon": float(data["lon"][0][0]),
        },
    }))
    return dest


@requires_reference
def test_real_orbcomm_pass_decodes_without_errors(tmp_path):
    pass_dir = _as_pass_dir(_reference_recording(), tmp_path / "ORBCOMM_FM_118_20260722")

    result = decode(pass_dir)

    assert result.success, result.notes
    assert result.kind == "telemetry"
    assert result.stats["per"] == 0.0, "the reference pass decoded at 0.0 % PER by hand"
    assert result.stats["packets"] > 50


@requires_reference
def test_real_orbcomm_pass_yields_usable_ephemeris(tmp_path):
    pass_dir = _as_pass_dir(_reference_recording(), tmp_path / "ORBCOMM_FM_118_20260722")

    result = decode(pass_dir)
    ephemeris = result.stats.get("ephemeris")

    assert ephemeris, "an ephemeris frame is what makes this telemetry worth keeping"
    first = ephemeris[0]
    assert 600 < first["alt_km"] < 900          # Orbcomm flies at ~700 km
    assert 6500 < first["velocity_ms"] < 8000   # and at ~7.2 km/s
    # The satellite's own reported position against the TLE prediction: this is
    # the number that proved on 21 July we were really hearing the satellite.
    assert first["ephemeris_diff_km"] < 100


@requires_reference
def test_real_orbcomm_pass_writes_products(tmp_path):
    pass_dir = _as_pass_dir(_reference_recording(), tmp_path / "ORBCOMM_FM_118_20260722")

    result = decode(pass_dir)
    names = {p.name for p in result.products}

    assert {"packets.txt", "telemetry.json", "orbcomm_decode.log"} <= names
    saved = json.loads((pass_dir / "products" / "telemetry.json").read_text())
    assert saved["per"] == result.stats["per"]
