"""
events.py — the timeline of one pass

Every debugging session between 17 and 22 July came down to reading the
agent's log on the Mac. This turns the same information into something the
console can show live and the dashboard can show months later: an ordered list
of what happened, with times.

Events are pushed to the app as they happen (so /pass can draw the timeline
during the pass) and sent again in full with the contact (so the pass can be
reconstructed afterwards).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Chunks whose SNR is above this count as "we can hear it". Deliberately not
# called a lock — the recorder does not demodulate, so it cannot know. It is a
# signal-present threshold, and saying so is more honest than borrowing a word
# from the decoder.
SIGNAL_PRESENT_DB = 8.0
SIGNAL_LOST_AFTER_S = 20.0  # how long it must stay quiet before we call it lost


class PassLog:
    """Collects the events of a single pass, reporting each one as it happens."""

    def __init__(self, satellite: str, aos: datetime, los: datetime, reporter=None):
        self.satellite = satellite
        self.aos = aos
        self.los = los
        self.events: list = []
        # Injected so tests (and a future offline mode) do not need the network.
        self._reporter = reporter
        self._has_signal = False
        self._last_signal_at: Optional[float] = None

    def add(self, kind: str, detail: str = "") -> dict:
        """Record an event now, and tell the app about it."""
        event = {
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "detail": detail,
        }
        self.events.append(event)
        log.info("event: %-18s %s", kind, detail)
        if self._reporter:
            try:
                self._reporter(
                    kind=kind, detail=detail, satellite=self.satellite, aos=self.aos
                )
            except Exception:
                # A timeline entry is never worth interrupting a pass for.
                log.debug("event reporting failed", exc_info=True)
        return event

    def observe_signal(self, elapsed_s: float, snr: float) -> None:
        """
        Turn the SNR stream into acquired/lost events.

        Called from the recorder callback, so it must be cheap and must not
        chatter: one event when the signal appears, one when it has been gone
        long enough to mean something.
        """
        if snr >= SIGNAL_PRESENT_DB:
            self._last_signal_at = elapsed_s
            if not self._has_signal:
                self._has_signal = True
                self.add("signal_acquired", f"{snr:.1f} dB at {elapsed_s:.0f} s")
            return

        if self._has_signal and self._last_signal_at is not None:
            quiet_for = elapsed_s - self._last_signal_at
            if quiet_for >= SIGNAL_LOST_AFTER_S:
                self._has_signal = False
                self.add(
                    "signal_lost",
                    f"below {SIGNAL_PRESENT_DB:.0f} dB for {quiet_for:.0f} s",
                )
