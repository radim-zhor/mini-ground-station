# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Mini Ground Station — portfolio project for Groundcom. Receives and monitors CubeSat/NOAA satellite telemetry via RTL-SDR. Each iteration must produce a working result, not just code.

## Architecture

Two separate runtimes sharing one repo:

**Local agent (Mac)** — runs continuously, owns all SDR/DSP logic:
- Watches TLE data, waits for passes using `skyfield`
- Records passes as 48 kHz WAV via `pyrtlsdr`
- Decodes: NOAA APT via `noaa-apt` CLI (subprocess), FUNCUBE-1 via AX.25 parser
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
│   ├── recorder.py    # pyrtlsdr → 48 kHz WAV; mock mode for CI
│   └── decoder.py     # noaa-apt subprocess (APT) / AX.25 parser (FUNCUBE-1)
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
- **APT decoding:** shell out to `noaa-apt` binary, do not implement DSP manually.
- **CubeSat target:** FUNCUBE-1 / AO-73 at 145.935 MHz. Telemetry spec at funcube.org.uk.
- **IQ storage:** record directly as 48 kHz WAV (~55 MB/pass). Raw IQ retention policy (48h) to be added in iteration 4b.
- **Notifications:** ntfy.sh via `requests.post()`, no SMTP.
- **Agent → app auth:** shared secret in `Authorization` header (env var on both sides).

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
MOCK=1 python agent/scheduler.py
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
| `OBSERVER_LAT` | agent | Observer latitude |
| `OBSERVER_LON` | agent | Observer longitude |
| `MOCK` | agent | Set to `1` to use synthetic IQ data instead of RTL-SDR |
| `NTFY_TOPIC` | agent | ntfy.sh topic for pass notifications |
