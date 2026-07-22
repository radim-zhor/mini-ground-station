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
│   └── decoder.py     # noaa-apt subprocess (legacy APT; dispatcher lands in M2.2)
├── app/
│   ├── main.py        # FastAPI entrypoint
│   ├── routes/        # /passes, /contacts, /satellite/position, etc.
│   └── templates/     # Jinja2 + HTMX + Leaflet.js
└── shared/
    ├── tle.py         # TLE fetch (Celestrak) + skyfield wrappers
    └── models.py      # SQLAlchemy models (shared between agent SQLite + app PostgreSQL)
```

## Key decisions

- **Astrodynamics:** `skyfield` only — never use `sgp4` directly. It provides `find_events()`, `altaz()`, and all coordinate transforms out of the box.
- **TLE source:** SatNOGS API (`https://db.satnogs.org/api/tle/`), not Celestrak (their GP API returns 404 as of 2026-03). TLE cached in `.cache/noaa_tle.json`, TTL 12h. NORAD IDs hardcoded in `shared/tle.py` for NOAA 15/18/19.
- **Live map updates:** HTMX polling (`hx-trigger="every 5s"`), no WebSocket or SSE.
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
- **Retention is part of the capture path** (`agent/retention.py`): ~3 GB per
  10-minute pass at 1.2288 Msps. IQ is deleted after a *successful* decode and
  kept after a failure; `recordings/` is capped by `RECORDINGS_MAX_GB`, oldest
  pass first. Only directories containing a `meta.json` the agent wrote are ever
  deleted.
- **Bias tee on, gain manual.** The LNA is bias-tee powered (measured +14 dB);
  AGC overloads with an LNA ahead of the tuner.
- **Notifications:** ntfy.sh via `requests.post()`, no SMTP.
- **Agent → app auth:** shared secret in `Authorization` header (env var on both sides).
- **Mobile station:** the ground station moves. The agent auto-detects its position
  (IP geolocation via ipinfo.io, `OBSERVER_MODE=auto` default) and reports it to
  `POST /observer`; the app persists it in `station_status` (single row) and uses it
  for the map pin and pass predictions. `OBSERVER_LAT/LON` are the fallback;
  `OBSERVER_MODE=manual` pins them (e.g. when on VPN).
- **DB migrations:** Alembic (`alembic upgrade head` runs in the Render Start Command
  before uvicorn). Never rely on `create_all` for schema changes on production.

## Development setup

```bash
# Create venv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install RTL-SDR driver (Mac)
brew install librtlsdr
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
