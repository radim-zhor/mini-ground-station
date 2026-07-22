"""
health.py — what the station's hardware is doing, from the agent's side

Two days of debugging in July went on things that were invisible until someone
looked at the right terminal: an unplugged dongle, SatDump still holding the
device, and an LNA sitting there unpowered because the bias tee was off. All
three are knowable *before* a pass. This module collects them; the app turns
them into warnings on /pass.

The SDR probe opens and immediately closes the device, so it must never run
while a recording is in progress — it would take the dongle away from the
thing that needs it.
"""
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

PROBE_INTERVAL_S = 60  # opening the dongle is cheap, but not free

_cache: dict = {"sdr": None, "checked_at": 0.0}


def probe_sdr(force: bool = False) -> str:
    """
    Is the dongle there? Returns "mock", "ok", "busy", "missing" or "error: ...".

    "busy" is its own answer on purpose: a dongle held by SatDump looks exactly
    like a working station until the pass starts and the recording fails.
    """
    if os.getenv("MOCK"):
        return "mock"

    now = time.time()
    if not force and _cache["sdr"] and (now - _cache["checked_at"]) < PROBE_INTERVAL_S:
        return _cache["sdr"]

    state = _open_and_close()
    _cache.update(sdr=state, checked_at=now)
    return state


def _open_and_close() -> str:
    try:
        from rtlsdr import RtlSdr
    except Exception as e:
        return f"error: pyrtlsdr unavailable ({e})"

    sdr = None
    try:
        sdr = RtlSdr()
        return "ok"
    except Exception as e:
        message = str(e).lower()
        if "resource" in message or "busy" in message or "in use" in message:
            return "busy"
        if "no device" in message or "not found" in message or "index" in message:
            return "missing"
        return f"error: {e}"
    finally:
        if sdr is not None:
            try:
                sdr.close()
            except Exception:
                pass


def snapshot(recording: bool = False, frequency_hz: Optional[int] = None,
             sample_rate: Optional[int] = None) -> dict:
    """
    Everything the health panel needs, as a plain dict.

    ``recording`` suppresses the SDR probe — during a pass the answer is
    obviously "in use by us", and asking would fight the recorder for the
    device.
    """
    from agent import retention
    from agent.recorder import RECORDINGS_DIR

    gain = os.getenv("SDR_GAIN", "20.7")
    cap = retention.max_bytes()
    used = retention._dir_size(RECORDINGS_DIR)

    return {
        "sdr": "recording" if recording else probe_sdr(),
        "bias_tee": os.getenv("SDR_BIAS_TEE", "1") != "0",
        "lna": os.getenv("LNA_PRESENT", "1") != "0",
        "gain": gain,
        "agc": gain.lower() == "auto",
        "mock": bool(os.getenv("MOCK")),
        "frequency_hz": frequency_hz,
        "sample_rate": sample_rate,
        "disk_used_gb": round(used / 1e9, 2),
        "disk_cap_gb": round(cap / 1e9, 2),
    }
