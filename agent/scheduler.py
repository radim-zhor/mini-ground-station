"""
scheduler.py — satellite pass scheduler

Continuously watches for upcoming NOAA passes, triggers the recorder
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

import requests

from agent.client import post_contact, post_observer, retry_pending
from agent.decoder import decode_apt
from agent.location import detect_location
from agent.recorder import measure_snr_windows, record
from shared.tle import get_cached_passes, set_observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# APT downlink frequencies (Hz)
FREQUENCIES: dict[str, int] = {
    "NOAA 15": 137_620_000,
    "NOAA 18": 137_912_500,
    "NOAA 19": 137_100_000,
}

POLL_INTERVAL = 30   # seconds between pass-list refreshes
PRE_AOS_WAKE = 10    # seconds before AOS to stop sleeping and start recording
NOTIFY_LEAD_S = 600  # send an ntfy alert ~10 min before AOS


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


def _refresh_location(force_report: bool = False) -> None:
    """Detect the station position; on change, recompute passes and tell the app.

    The mobile station moves between sessions, so predictions must follow the
    device. detect_location() caches for 15 min — calling this every loop
    iteration is cheap.
    """
    lat, lon, source = detect_location()
    changed = set_observer(lat, lon)
    if changed:
        log.info("Station position: %.4f, %.4f (%s)", lat, lon, source)
    if changed or force_report:
        post_observer(lat, lon, source)


def run() -> None:
    log.info("Scheduler started  MOCK=%s", os.getenv("MOCK", "0"))
    _refresh_location(force_report=True)
    retry_pending()

    notified: set[str] = set()  # pass keys already announced via ntfy

    while True:
        _refresh_location()
        nxt = _next_upcoming_pass()

        if nxt is None:
            log.info("No passes in 24 h — sleeping 1 h")
            time.sleep(3600)
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
            time.sleep(POLL_INTERVAL)
            continue

        # Close to AOS — sleep the remaining seconds
        if wait_s > PRE_AOS_WAKE:
            time.sleep(wait_s - PRE_AOS_WAKE)

        freq = FREQUENCIES.get(nxt.satellite)
        if freq is None:
            log.warning("No frequency for %s — skipping", nxt.satellite)
            time.sleep(60)
            continue

        # ── Record ────────────────────────────────────────────────────────────
        # Record until LOS, not for a fixed length (A1). If the loop woke late
        # (slow prediction, pending retry) recording a fixed duration_s from
        # *now* would run past LOS into noise; anchor the end to LOS instead.
        record_s = int((nxt.los - datetime.now(timezone.utc)).total_seconds())
        if record_s <= 0:
            log.warning("%s LOS already passed — skipping", nxt.satellite)
            time.sleep(60)
            continue

        log.info("AOS  %s  %.4f MHz  recording %d s to LOS", nxt.satellite, freq / 1e6, record_s)
        try:
            wav_path = record(
                frequency_hz=freq,
                duration_s=record_s,
                satellite=nxt.satellite,
            )
        except Exception:
            log.exception("Recording failed")
            time.sleep(120)
            continue

        avg_snr, peak_snr = measure_snr_windows(wav_path)
        log.info("LOS  saved %s  SNR avg %.1f / peak %.1f dB", wav_path.name, avg_snr, peak_snr)

        # ── Decode ────────────────────────────────────────────────────────────
        png_path = None
        notes = None
        log.info("Decoding APT...")
        try:
            png_path = decode_apt(wav_path)
            log.info("Image saved: %s", png_path.name)
        except FileNotFoundError:
            notes = "decode skipped: noaa-apt binary not found"
            log.warning("%s", notes)
        except Exception as e:
            notes = f"decode failed: {e}"
            log.exception("Decode failed")

        post_contact(
            satellite=nxt.satellite,
            aos=nxt.aos,
            los=nxt.los,
            duration_s=nxt.duration_s,
            max_elevation=nxt.max_elevation,
            snr=peak_snr,
            avg_snr=avg_snr,
            notes=notes,
            png_path=png_path,
        )

        # Wait past LOS before looking for the next pass
        time.sleep(120)


if __name__ == "__main__":
    run()
