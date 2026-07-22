"""
scheduler.py — satellite pass scheduler

Continuously watches for upcoming Meteor-M passes, triggers the recorder
at AOS, and decodes the recording after LOS.

Usage (run as a module from the repo root — agent/ uses absolute imports):
    python -m agent.scheduler          # real RTL-SDR hardware
    MOCK=1 python -m agent.scheduler   # synthetic data, no hardware
"""
# Load .env before importing agent.client, which reads APP_URL / AGENT_SECRET
# at import time.
from dotenv import load_dotenv

load_dotenv()

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from agent import health, retention
from agent.client import post_contact, post_event, post_live, post_observer, retry_pending
from agent.decoder import decode
from agent.events import PassLog
from agent.location import detect_location
from agent.recorder import BYTES_PER_SAMPLE, RECORDINGS_DIR, record_iq
from shared.tle import get_cached_passes, set_observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Downlink frequencies (Hz), keyed by the satellite name as it appears in the
# SatNOGS TLE. The NOAA APT birds these replaced are all decommissioned (the
# last, NOAA-15, on 2025-08-19) — see shared/tle.py.
#
# All of these are now captured the same way: baseband IQ, no demodulation.
# What differs per satellite is only the sample rate (SAMPLE_RATES) and which
# decoder gets the file afterwards.
FREQUENCIES: dict[str, int] = {
    # Meteor-M — LRPT, 137.9 MHz (137.1 is the backup downlink)
    "METEOR M2-4": 137_900_000,
    "METEOR M2-3": 137_900_000,
    # Orbcomm — SDPSK 4800 bps, inside the FBP-137s passband
    "ORBCOMM FM 107": 137_250_000,
    "ORBCOMM FM 109": 137_250_000,
    "ORBCOMM FM 118": 137_287_500,
    "ORBCOMM FM 110": 137_287_500,
    "ORBCOMM FM 114": 137_287_500,
    "ORBCOMM FM 36": 137_440_000,
    "ORBCOMM FM 108": 137_460_000,
    "ORBCOMM FM 117": 137_460_000,
    "ORBCOMM FM 112": 137_662_500,
    "ORBCOMM FM 113": 137_662_500,
    "ORBCOMM FM 116": 137_662_500,
    # ISS — APRS digipeater; outside the filter passband (bypass it)
    "ISS (ZARYA)": 145_825_000,
}

# Capture sample rate (Hz) per satellite family. Wide enough for the signal
# plus Doppler, narrow enough not to write more bytes than necessary — at
# 1.2288 Msps a 10-minute pass is already ~3 GB.
SAMPLE_RATES: dict[str, int] = {
    # Meteor LRPT: ~120 kHz wide OQPSK, ±4 kHz Doppler at 137 MHz.
    "METEOR": 1_000_000,
    # Orbcomm: the rate the upstream decoder's timing recovery is built around
    # (1.2288 Msps = 256 × 4800 baud) — do not change without changing it too.
    "ORBCOMM": 1_228_800,
    # ISS 1200 baud AFSK packet — narrow, no reason to record a megahertz.
    "ISS": 250_000,
}
DEFAULT_SAMPLE_RATE = 1_000_000

# Orbcomm is recorded with the dongle parked at 137.5 MHz instead of on the
# satellite's own channel: every Orbcomm channel (137.25–137.6625) then sits
# inside the 1.2288 MHz band, none of them lands on the DC spike, and the file
# looks exactly like the upstream recordings the vendored decoder was built and
# proven against.
ORBCOMM_CENTER_HZ = 137_500_000

POLL_INTERVAL = 30   # seconds between pass-list refreshes
PRE_AOS_WAKE = 10    # seconds before AOS to stop sleeping and start recording
NOTIFY_LEAD_S = 600  # send an ntfy alert ~10 min before AOS
PROGRESS_LOG_S = 30  # how often the recording progress reaches the log
LIVE_INTERVAL_S = 2  # how often the /pass console hears from us while recording
HEARTBEAT_S = 10     # ... and while idle, so the console can tell we are alive


def sample_rate_for(satellite: str) -> int:
    """Capture rate for a satellite, matched on its name prefix."""
    for prefix, rate in SAMPLE_RATES.items():
        if satellite.upper().startswith(prefix):
            return rate
    return DEFAULT_SAMPLE_RATE


def center_freq_for(satellite: str) -> int:
    """Where to park the dongle — not always the satellite's own downlink."""
    if satellite.upper().startswith("ORBCOMM"):
        return ORBCOMM_CENTER_HZ
    return FREQUENCIES[satellite]


def notify_upcoming(p) -> None:
    """Push a 'pass incoming' alert to ntfy.sh, if NTFY_TOPIC is configured."""
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return
    mins = max(int((p.aos - datetime.now(timezone.utc)).total_seconds() / 60), 0)
    msg = f"{p.satellite} in {mins} min — max el {p.max_elevation:.0f}°, {p.duration_s // 60} min pass"
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=msg.encode("utf-8"),
            headers={"Title": "Satellite pass incoming", "Tags": "satellite"},
            timeout=10,
        )
        log.info("ntfy: %s", msg)
    except Exception as e:
        log.warning("ntfy failed: %s", e)


def _next_upcoming_pass():
    """Return the soonest pass that hasn't ended yet, or None if none in 24 h.

    Uses ``los > now`` rather than ``minutes_until > 0`` (A2): the latter drops
    the pass in its final minute before AOS (``int(59s / 60) == 0``) and also
    ignores a pass already in progress, which we can still partially record.
    Predictions come from the shared 5-minute cache instead of a fresh skyfield
    run on every 30 s poll.
    """
    now = datetime.now(timezone.utc)
    upcoming = sorted(
        (p for p in get_cached_passes() if p.los > now),
        key=lambda p: p.aos,
    )
    return upcoming[0] if upcoming else None


def _refresh_location(force_report: bool = False) -> tuple:
    """Detect the station position; on change, recompute passes and tell the app.

    The mobile station moves between sessions, so predictions must follow the
    device. detect_location() caches for 15 min — calling this every loop
    iteration is cheap. Returns (lat, lon) so the recording can record where it
    was made.
    """
    lat, lon, source = detect_location()
    changed = set_observer(lat, lon)
    if changed:
        log.info("Station position: %.4f, %.4f (%s)", lat, lon, source)
    if changed or force_report:
        post_observer(lat, lon, source)
    return lat, lon


def _progress_reporter(p, passlog=None):
    """
    Recorder callback: log progress occasionally, tell the /pass console often.

    Two different rhythms on purpose — the log is for reading afterwards, the
    console is for watching now. Both are throttled against the recorder's
    ~2 Hz callback so neither the log nor the network gets flooded.
    """
    state = {"next_log": 0.0, "next_live": 0.0}

    def report(elapsed: float, total: float, snr: float) -> None:
        if elapsed >= state["next_log"]:
            state["next_log"] = elapsed + PROGRESS_LOG_S
            pct = 100 * elapsed / total if total else 0
            log.info("  %s  %3.0f%%  %.0f/%.0f s  SNR %.1f dB",
                     p.satellite, pct, elapsed, total, snr)

        if passlog is not None:
            # Turns the SNR stream into "we can hear it" / "we lost it" entries.
            passlog.observe_signal(elapsed, snr)

        if elapsed >= state["next_live"]:
            state["next_live"] = elapsed + LIVE_INTERVAL_S
            post_live(
                state="recording", satellite=p.satellite, aos=p.aos, los=p.los,
                elapsed_s=round(elapsed, 1), total_s=round(total, 1), snr=snr,
                health=health.snapshot(recording=True),
            )

    return report


def _sleep_with_heartbeat(seconds: float, state: str, p=None, note: Optional[str] = None) -> None:
    """
    Sleep, but keep telling the app we are alive.

    The console calls the station offline after 30 s of silence, and the
    scheduler otherwise sleeps far longer than that between passes — an idle
    agent must not look like a dead one.
    """
    remaining = max(seconds, 0.0)
    while True:
        post_live(
            state=state,
            satellite=p.satellite if p else None,
            aos=p.aos if p else None,
            los=p.los if p else None,
            note=note,
            health=health.snapshot(),
        )
        if remaining <= 0:
            return
        slice_s = min(HEARTBEAT_S, remaining)
        time.sleep(slice_s)
        remaining -= slice_s


def run() -> None:
    log.info("Scheduler started  MOCK=%s", os.getenv("MOCK", "0"))
    _refresh_location(force_report=True)
    retry_pending()

    notified: set[str] = set()  # pass keys already announced via ntfy

    while True:
        lat, lon = _refresh_location()
        nxt = _next_upcoming_pass()

        if nxt is None:
            log.info("No passes in 24 h — sleeping 1 h")
            _sleep_with_heartbeat(3600, "idle", note="no passes in 24 h")
            continue

        wait_s = int((nxt.aos - datetime.now(timezone.utc)).total_seconds())
        log.info(
            "Next: %-10s  AOS %s  el %.1f°  in %d min",
            nxt.satellite,
            nxt.aos.strftime("%H:%M UTC"),
            nxt.max_elevation,
            max(wait_s // 60, 0),
        )

        # Alert ~10 min ahead, once per pass.
        key = f"{nxt.satellite}@{nxt.aos.isoformat()}"
        if 0 < wait_s <= NOTIFY_LEAD_S and key not in notified:
            notify_upcoming(nxt)
            notified.add(key)

        if wait_s > POLL_INTERVAL + PRE_AOS_WAKE:
            # Too early — sleep a bit and re-check
            _sleep_with_heartbeat(POLL_INTERVAL, "waiting", nxt)
            continue

        # Close to AOS — sleep the remaining seconds
        if wait_s > PRE_AOS_WAKE:
            _sleep_with_heartbeat(wait_s - PRE_AOS_WAKE, "waiting", nxt)

        if nxt.satellite not in FREQUENCIES:
            log.warning("No frequency for %s — skipping", nxt.satellite)
            _sleep_with_heartbeat(60, "idle", note=f"no frequency for {nxt.satellite}")
            continue
        freq = center_freq_for(nxt.satellite)

        # ── Record ────────────────────────────────────────────────────────────
        # Record until LOS, not for a fixed length (A1). If the loop woke late
        # (slow prediction, pending retry) recording a fixed duration_s from
        # *now* would run past LOS into noise; anchor the end to LOS instead.
        record_s = int((nxt.los - datetime.now(timezone.utc)).total_seconds())
        if record_s <= 0:
            log.warning("%s LOS already passed — skipping", nxt.satellite)
            _sleep_with_heartbeat(60, "idle", note=f"{nxt.satellite} LOS already passed")
            continue

        rate = sample_rate_for(nxt.satellite)
        expected_bytes = rate * record_s * BYTES_PER_SAMPLE

        # From here on, everything that happens to this pass is written down.
        passlog = PassLog(nxt.satellite, nxt.aos, nxt.los, reporter=post_event)
        passlog.add(
            "pass_start",
            f"{freq / 1e6:.4f} MHz, {rate / 1e6:.4f} Msps, max el {nxt.max_elevation:.0f}°",
        )

        # Make room *before* the pass, not after: a full disk mid-recording
        # loses the pass, and the pass does not come back.
        retention.enforce_cap(RECORDINGS_DIR)
        retention.has_room_for(RECORDINGS_DIR, expected_bytes)

        log.info(
            "AOS  %s  %.4f MHz  %.4f Msps  recording %d s to LOS",
            nxt.satellite, freq / 1e6, rate / 1e6, record_s,
        )
        passlog.add("recording_started", f"{record_s} s to LOS, ~{expected_bytes / 1e9:.1f} GB")
        try:
            rec = record_iq(
                frequency_hz=freq,
                duration_s=record_s,
                satellite=nxt.satellite,
                sample_rate=rate,
                progress_cb=_progress_reporter(nxt, passlog),
                observer=(lat, lon),
            )
        except Exception as e:
            log.exception("Recording failed")
            passlog.add("error", f"recording failed: {e}")
            post_live(state="error", satellite=nxt.satellite, aos=nxt.aos, los=nxt.los,
                      note=f"recording failed: {e}")
            _sleep_with_heartbeat(120, "idle", note="recovering from a failed recording")
            continue

        log.info(
            "LOS  saved %s  SNR avg %.1f / peak %.1f dB",
            rec.path.name, rec.avg_snr, rec.peak_snr,
        )
        passlog.add(
            "recording_finished",
            f"{rec.duration_s:.0f} s, SNR avg {rec.avg_snr:.1f} / peak {rec.peak_snr:.1f} dB",
        )

        # ── Decode ────────────────────────────────────────────────────────────
        # decode() never raises: a failed decode costs us the products, not the
        # pass. On failure the IQ stays on disk for a manual re-run.
        passlog.add("decode_started", rec.path.name)
        post_live(state="decoding", satellite=nxt.satellite, aos=nxt.aos, los=nxt.los,
                  note=f"decoding {rec.path.name}", health=health.snapshot())
        result = decode(rec.pass_dir)
        log.info("Decode: %s", result.notes)
        passlog.add("decode_finished" if result.success else "decode_failed", result.notes)

        retention.after_decode(rec.pass_dir, result.success, reason=result.notes)
        retention.enforce_cap(RECORDINGS_DIR, keep=rec.pass_dir)
        passlog.add(
            "retention",
            "IQ smazáno po úspěšném dekódování" if result.success else "IQ ponecháno k ladění",
        )

        post_contact(
            satellite=nxt.satellite,
            aos=nxt.aos,
            los=nxt.los,
            duration_s=nxt.duration_s,
            max_elevation=nxt.max_elevation,
            snr=rec.peak_snr,
            avg_snr=rec.avg_snr,
            notes=result.notes,
            png_path=result.image,
            # A failed Orbcomm decode is still a telemetry pass — the dashboard
            # should say the telemetry is missing, not that a picture is.
            contact_type=result.kind if result.kind in ("image", "telemetry") else "image",
            telemetry=result.stats if result.kind == "telemetry" and result.stats else None,
            events=passlog.events,
        )

        post_live(
            state="done" if result.success else "error",
            satellite=nxt.satellite, aos=nxt.aos, los=nxt.los, note=result.notes,
            health=health.snapshot(),
        )

        # Wait past LOS before looking for the next pass
        _sleep_with_heartbeat(120, "idle", note=result.notes)


if __name__ == "__main__":
    run()
