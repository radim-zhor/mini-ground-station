"""
scheduler.py — satellite pass scheduler

Continuously watches for upcoming NOAA passes, triggers the recorder
at AOS, and decodes the recording after LOS.

Usage:
    python agent/scheduler.py          # real RTL-SDR hardware
    MOCK=1 python agent/scheduler.py   # synthetic data, no hardware
"""
import logging
import os
import time
from datetime import datetime, timezone

from agent.client import post_contact, retry_pending
from agent.decoder import decode_apt
from agent.recorder import measure_snr, record
from shared.tle import predict_passes

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


def _next_upcoming_pass():
    """Return the soonest future pass, or None if nothing in 24 h."""
    passes = predict_passes(hours=24)
    upcoming = [p for p in passes if p.minutes_until > 0]
    return upcoming[0] if upcoming else None


def run() -> None:
    log.info("Scheduler started  MOCK=%s", os.getenv("MOCK", "0"))
    retry_pending()

    while True:
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
        log.info("AOS  %s  %.4f MHz  duration %d s", nxt.satellite, freq / 1e6, nxt.duration_s)
        try:
            wav_path = record(
                frequency_hz=freq,
                duration_s=nxt.duration_s,
                satellite=nxt.satellite,
            )
        except Exception:
            log.exception("Recording failed")
            time.sleep(120)
            continue

        snr = measure_snr(wav_path)
        log.info("LOS  saved %s  SNR %.1f dB", wav_path.name, snr)

        # ── Decode ────────────────────────────────────────────────────────────
        png_path = None
        log.info("Decoding APT...")
        try:
            png_path = decode_apt(wav_path)
            log.info("Image saved: %s", png_path.name)
        except FileNotFoundError as e:
            log.warning("%s — skipping decode", e)
        except Exception:
            log.exception("Decode failed")

        post_contact(
            satellite=nxt.satellite,
            aos=nxt.aos,
            los=nxt.los,
            duration_s=nxt.duration_s,
            max_elevation=nxt.max_elevation,
            snr=snr,
            png_path=png_path,
        )

        # Wait past LOS before looking for the next pass
        time.sleep(120)


if __name__ == "__main__":
    run()
