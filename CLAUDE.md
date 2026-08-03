# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Mini Ground Station — portfolio project for Groundcom. Receives and monitors CubeSat/NOAA satellite telemetry via RTL-SDR. Each iteration must produce a working result, not just code.

## Architecture

Two separate runtimes sharing one repo:

**Local agent (Mac)** — runs continuously, owns all SDR/DSP logic:
- Watches TLE data, waits for passes using `skyfield`
- Records passes as baseband IQ (`.cs16`) via `pyrtlsdr`, one directory per pass
- Decodes from the stored IQ after the pass (SatDump for Meteor, `file_decoder.py`
  for Orbcomm) — recording must keep real time, decoding must not
- POSTs results to web app REST API; on network failure writes to local SQLite as "pending" and retries next pass

**Web app (Render.com Starter)** — FastAPI + HTMX + Leaflet.js:
- Receives contact data from agent via authenticated `POST /contacts`
- Serves live satellite position via `GET /satellite/position` (polled every 5s by frontend)
- Stores all data in PostgreSQL (Render-hosted)
- Never sleeps (paid Starter plan)

```
ground-station/
├── agent/
│   ├── scheduler.py   # skyfield pass prediction, triggers recorder
│   ├── recorder.py    # pyrtlsdr → interleaved int16 IQ (.cs16); mock mode for CI
│   ├── retention.py   # drops IQ after a successful decode, caps recordings/
│   ├── events.py      # the pass timeline (AOS, signal, decode, result)
│   ├── health.py      # SDR/bias tee/gain/disk state for the console
│   ├── decoder.py     # dispatcher: satdump (Meteor) / file_decoder.py (Orbcomm)
│   ├── orbcomm.py     # bridge from our .cs16 to the vendored Orbcomm decoder
│   ├── aprs.py        # ISS APRS: FM demod → direwolf atest (AFSK1200/AX.25)
│   ├── kostka.py      # KOSTKA: Doppler correction → gr-satellites (9k6 G3RUH)
│   └── satyaml/       # gr-satellites SatYAML for satellites not in its DB
├── app/
│   ├── main.py        # FastAPI entrypoint
│   ├── routes/        # /passes, /pass, /contacts, /satellite/position, etc.
│   └── templates/     # Jinja2 + HTMX + Leaflet.js
└── shared/
    ├── tle.py         # TLE fetch (Celestrak) + skyfield wrappers
    └── models.py      # SQLAlchemy models (shared between agent SQLite + app PostgreSQL)
```

## Key decisions

- **Astrodynamics:** `skyfield` only — never use `sgp4` directly. It provides `find_events()`, `altaz()`, and all coordinate transforms out of the box.
- **TLE source:** SatNOGS API (`https://db.satnogs.org/api/tle/`), not Celestrak (their GP API returns 404 as of 2026-03). TLE cached in `.cache/noaa_tle.json`, TTL 12h. NORAD IDs hardcoded in `shared/tle.py` for NOAA 15/18/19.
- **Live map updates:** polling every 5 s, no WebSocket or SSE.
- **The agent pushes, the browser polls.** The agent is behind NAT, so the app
  can never ask it anything: it POSTs its position to `/observer` and its live
  state to `/station/live`, and pages poll the app. The live state is in memory
  (`app/routes/station.py`) — it describes what the station is doing *now*, and
  after a restart "offline" is the honest answer until the next heartbeat.
- **The station never says "lock".** The recorder does not demodulate, so it
  cannot know: the timeline reports `signal_acquired` / `signal_lost` derived
  from the measured SNR, which is what the station actually knows.
- **Decoding:** always shell out to an existing decoder (SatDump, `noaa-apt`,
  the vendored Orbcomm `file_decoder.py`), never implement DSP by hand.
- **CubeSat target:** FUNCUBE-1 / AO-73 at 145.935 MHz. Telemetry spec at funcube.org.uk.
- **Recording format: baseband IQ, never demodulated audio.** FM demodulation
  destroys the phase that Meteor (OQPSK) and Orbcomm (SDPSK) carry data in.
  `recorder.py` streams interleaved int16 IQ to `recordings/<pass>/<pass>.cs16`
  with a `meta.json` sidecar, readable by SatDump (`--baseband_format s16`) or
  `np.fromfile(..., np.int16)` with no conversion.
- **Recording and decoding are separate.** Recording must keep up with the pass
  in real time; decoding must not. Verified 22. 7.: a realtime Orbcomm decoder
  failed while the same pass decoded from stored IQ at 0.0 % PER.
- **Sample rate is per satellite** (`SAMPLE_RATES` in `scheduler.py`): Meteor
  1 Msps, Orbcomm 1.2288 Msps (= 256 × 4800 baud, assumed by the upstream
  decoder), ISS 250 ksps.
- **Orbcomm is recorded at 137.5 MHz centre**, not on the satellite's own
  channel: all Orbcomm channels then fit in the band, none sits on the DC
  spike, and the file matches what the vendored decoder was proven against.
- **Decoder dispatch is by satellite name** (`agent/decoder.py`), and it never
  raises: a failed decode returns `success=False`, which keeps the IQ for a
  manual re-run. The Orbcomm bridge (`agent/orbcomm.py`) picks the best 2 s
  window from the recorded SNR profile, writes it as the `.mat` the upstream
  script expects and runs it with `MPLBACKEND=Agg` (the script ends in
  `plt.show()` and would otherwise block forever).
- **Retention is part of the capture path** (`agent/retention.py`): ~3 GB per
  10-minute pass at 1.2288 Msps. IQ is deleted after a *successful* decode and
  kept after a failure; `recordings/` is capped by `RECORDINGS_MAX_GB`, oldest
  pass first. Only directories containing a `meta.json` the agent wrote are ever
  deleted.
- **Bias tee on, gain manual.** The LNA is bias-tee powered (measured +14 dB);
  AGC overloads with an LNA ahead of the tuner.
- **Never test the antenna on the FM broadcast band.** An FBP-137s bandpass
  filter sits ahead of the LNA, so 88-108 MHz reads ~10-15 dB above noise on a
  perfectly healthy chain. Measured 22. 7. and misread as a dead antenna for an
  hour. Test in band, bias tee on, `SDR_GAIN=20.7`.
- **ISS APRS (145.825 MHz) is opt-in via `ISS_ENABLED=1`.** It is out of the
  FBP-137s passband, so it is only recordable with the filter physically
  bypassed; left always-on, a ~87° ISS pass would outrank every 137 MHz sat in
  the value selection and record noise. Decoded by `agent/aprs.py` (FM demod →
  direwolf's `atest`, AFSK1200/AX.25). ISS names from SatNOGS are unstable
  ("ISS" vs "ISS (ZARYA)"), so everything keys off the `is_iss()` prefix.
- **KOSTKA (436.870 MHz) is opt-in via `KOSTKA_ENABLED=1`** — a 1U CubeSat of
  VUT Brno / team YSpace, 9k6 GFSK G3RUH AX.25, decoded by `agent/kostka.py`
  via gr-satellites. Same physical station config as ISS (filter bypassed, 70cm
  dipole ~16 cm, gain 15.7) but a separate gate, because an ~87° ISS pass would
  outrank KOSTKA (max ~40° from Brno) in the value selection.
- **KOSTKA is tracked by SatNOGS `sat_id`, never by name or NORAD ID.** Checked
  29. 7. 2026 the TLE feed carries it unnamed as `0 OBJECT BU` with
  `norad_cat_id` 98395 while the TLE lines already say 69935 — both will change
  again as cataloguing settles, the `sat_id` will not. `shared/tle.py` therefore
  matches on the sat_id and *assigns* the name "KOSTKA"
  (`SAT_NAME_OVERRIDES`), because frequency, sample rate and decoder dispatch
  are all keyed by satellite name.
- **KOSTKA is recorded 50 kHz below its downlink**, like Orbcomm is parked off
  channel: the R820T's DC spike sits exactly at the tuned frequency and the
  signal is only ~20 kHz wide, so tuning on it would put the spike in the middle.
- **Doppler correction for UHF is ours, not the decoder's** (`agent/kostka.py`).
  At 436 MHz a LEO pass sweeps ±10 kHz — half the occupied bandwidth of 9k6
  GFSK — where the same excursion at 137 MHz is ±3 kHz and ignorable. The
  correction interpolates the *frequency* per sample and integrates there:
  interpolating an already-integrated phase between grid points makes the
  corrected frequency piecewise constant and leaves a sawtooth residual
  (measured 462 Hz against a 2 kHz/s ramp).
- **`pyrtlsdr` needs `DYLD_LIBRARY_PATH=/opt/homebrew/lib`.** ctypes does not
  search Homebrew's prefix on Apple Silicon, and `recorder.py` imports `rtlsdr`
  *lazily* — so a misconfigured agent starts cleanly and only fails at AOS,
  when the pass is already being lost. Any unattended launcher must set it.
- **Selection is by AOS, not by elevation** (`_next_upcoming_pass`). A weak pass
  that starts first holds the dongle and shadows a better one underneath it;
  simulated 22. 7., a 78.3° Meteor pass drops to 69.8 % coverage behind a 32.2°
  Orbcomm. Fixing this means scoring passes, not just sorting them.
- **Never restart the agent mid-pass.** `find_events()` needs AOS, culmination
  and LOS inside a window starting *now*, so a pass already in progress
  disappears from the recomputed cache and is lost for good.
- **Unattended operation is documented in `docs/bezobsluzny-provoz.md`** — the
  launch line, why each env var exists, the 40 s cold start that looks like a
  hang, and why launchd currently fails (macOS TCC on `~/Documents`).
- **Notifications:** ntfy.sh via `requests.post()`, no SMTP.
- **Agent → app auth:** shared secret in `Authorization` header (env var on both sides).
- **Mobile station:** the ground station moves. The agent auto-detects its position
  (IP geolocation via ipinfo.io, `OBSERVER_MODE=auto` default) and reports it to
  `POST /observer`; the app persists it in `station_status` (single row) and uses it
  for the map pin and pass predictions. `OBSERVER_LAT/LON` are the fallback;
  `OBSERVER_MODE=manual` pins them (e.g. when on VPN).
- **DB migrations:** Alembic (`alembic upgrade head` runs in the Render Start Command
  before uvicorn). Never rely on `create_all` for schema changes on production.
- **Not every pass is a picture.** `Contact.contact_type` is `image` (Meteor
  LRPT) or `telemetry` (Orbcomm frames); the decoder's output lives in the
  `telemetry` JSON column, because its shape differs per decoder and only the
  dashboard reads it. Telemetry quality is derived from PER, not SNR — a clean
  decode at modest SNR is a good pass.
- **PER gates `success`, and `success` gates retention** (`MAX_ACCEPTABLE_PER`
  in `decoder.py`, default 50 %). Until 22. 7. any run that emitted a packet
  counted as a success, so a PER 99 % decode of a good recording (avg SNR
  17.8 dB) had its 2.94 GB of baseband deleted and could never be re-run. A
  decode that cannot be trusted must keep its IQ.

## Development setup

```bash
# Create venv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install RTL-SDR driver (Mac)
brew install librtlsdr

# Install direwolf for ISS APRS decoding (provides `atest`)
brew install direwolf

# Install gr-satellites for KOSTKA (9k6 GFSK G3RUH AX.25). There is NO package:
# not on PyPI, not in Homebrew, not in conda — it is a GNU Radio out-of-tree
# module and the only supported install is a source build. Verified 29. 7. 2026.
brew install gnuradio pybind11        # pybind11 is required and not pulled in
# Python deps must land where GNU Radio's interpreter sees them. Brew's python
# is externally managed, and gnuradio's own venv (libexec/venv) shares
# site-packages with it, so this is the one place that works:
/opt/homebrew/opt/python@3.14/bin/python3.14 -m pip install \
    --break-system-packages construct requests websocket-client pyzmq scipy
git clone --depth 1 https://github.com/daniestevez/gr-satellites.git
cd gr-satellites && mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=/opt/homebrew -DCMAKE_PREFIX_PATH=/opt/homebrew
make -j"$(sysctl -n hw.ncpu)" && make install   # no sudo: /opt/homebrew is ours
# The first gr_satellites run prints a wall of `gr::vmcircbuf ... shmat/shmget`
# errors. That is GNU Radio probing shared-memory backends once and caching the
# working one in ~/.config/gnuradio/prefs — the second run is silent.
rtl_test          # verify dongle is detected

# Install noaa-apt decoder
# Download binary from github.com/martinber/noaa-apt/releases

# Run web app locally
uvicorn app.main:app --reload

# Run agent locally (mock mode — no hardware required)
# Must run as a module from the repo root (agent/ uses absolute imports).
MOCK=1 python -m agent.scheduler
```

## Docs

User-facing tutorials live in `docs/`:

| File | Obsah |
|---|---|
| `docs/sdrpp-recording.md` | Jak nahrát přelet v SDR++ a dekódovat APT snímek přes `noaa-apt` |
| `docs/bezobsluzny-provoz.md` | Jak nechat agenta nahrávat přes noc bez dozoru, a co se při tom tiše rozbije |
| `docs/satelity-cheatsheet.md` | Cheat-sheet per typ družice: frekvence, HW, dekodér, háčky (Meteor/Orbcomm/ISS) |
| `docs/migrace-neon.md` | Přesun databáze z expirující Render free Postgres na Neon: kroky, zálohy, pasti |

## Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | app | PostgreSQL connection string |
| `AGENT_SECRET` | agent + app | Shared secret for POST /contacts auth |
| `OBSERVER_MODE` | agent | `auto` (default) = IP geolocation; `manual` = pin to OBSERVER_LAT/LON |
| `OBSERVER_LAT` | agent + app | Observer latitude (fallback when auto-detection fails) |
| `OBSERVER_LON` | agent + app | Observer longitude (fallback when auto-detection fails) |
| `MOCK` | agent | Set to `1` to use synthetic IQ data instead of RTL-SDR |
| `NTFY_TOPIC` | agent | ntfy.sh topic for pass notifications |
| `SDR_GAIN` | agent | Tuner gain in dB, or `auto` for AGC (default 20.7 — the value verified with this station's LNA) |
| `SDR_BIAS_TEE` | agent | `0` disables bias-tee power to the LNA (default on) |
| `RECORDINGS_MAX_GB` | agent | Size cap for `recordings/`; oldest passes are dropped first (default 20) |
| `LNA_PRESENT` | agent | `0` when no LNA is in the path, so the bias-tee warning stays quiet (default on) |
| `SATDUMP_BIN` | agent | Path to the satdump CLI (default: `satdump` in PATH, then the macOS .app) |
| `SATDUMP_METEOR_PIPELINE` | agent | LRPT pipeline (default `meteor_m2-x_lrpt` = 72k; `meteor_m2-x_lrpt_80k` for the 80k mode) |
| `ISS_ENABLED` | agent | `1` records the ISS — only with the FBP-137s bypassed |
| `ISS_FREQ_HZ` | agent | ISS APRS downlink (default 145825000; 437825000 for the 70cm radio) |
| `ISS_SAMPLE_RATE` | agent | ISS capture rate (default 250000) |
| `KOSTKA_ENABLED` | agent | `1` records KOSTKA — only with the FBP-137s bypassed |
| `KOSTKA_FREQ_HZ` | agent | KOSTKA downlink (default 436870000); moves both the recording and the decode |
| `KOSTKA_SAMPLE_RATE` | agent | KOSTKA capture rate (default 250000) |
| `KOSTKA_SAT_ID` | agent + app | SatNOGS sat_id KOSTKA is tracked by (default `PCVZ-1444-3446-1456-5852`) |
| `KOSTKA_DEVIATION_HZ` | agent | FSK deviation passed to gr_satellites; empty = its default 5 kHz. First knob to turn if a healthy-looking pass decodes to nothing |
