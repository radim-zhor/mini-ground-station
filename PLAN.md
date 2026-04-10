# Mini Ground Station — Projektové zadání

## Cíl
Python webová aplikace pro příjem a monitoring CubeSat telemetrie.
Filozofie: funkční nástroj, každá iterace = fungující výsledek, ne jen kód.
Portfolio projekt pro pohovor u Groundcomu.

## Stack
- Backend: FastAPI (Python)
- Frontend: HTML + Leaflet.js + HTMX
- Databáze: SQLite (lokálně pro pending retry) + PostgreSQL (Render.com)
- Hosting: Render.com (Starter plán)
- CI/CD: GitHub Actions
- Hardware: RTL-SDR Blog V3
- Astrodynamika: `skyfield` (obaluje SGP4, poskytuje elevation/azimuth/find_events přímo)

## Architektura

```
Tvůj Mac (lokální agent) → GitHub → Render.com (web app)
```

### Lokální agent (Mac)
- Hlídá TLE a čeká na přelet
- Při přeletu spustí nahrávání přes RTL-SDR
- Dekóduje signál
- Pushuje výsledky do web app přes REST API (POST /contacts)
- Při selhání (výpadek sítě) uloží contact lokálně do SQLite jako "pending" a retry při příštím přeletu

### Web aplikace (Render.com)
- Mapa se satelity, predikce přeletů
- Contact log dashboard
- Dostupné odkudkoliv
- Render.com Starter plán (non-stop běh, žádný sleep)
- Přijímá data od agenta přes authenticated REST API

### Struktura repozitáře
```
ground-station/
├── agent/
│   ├── scheduler.py      # hlídá přelety
│   ├── recorder.py       # RTL-SDR nahrávání
│   └── decoder.py        # dekódování signálu
├── app/
│   ├── main.py           # FastAPI
│   ├── routes/
│   └── templates/
├── shared/
│   ├── tle.py            # TLE logika + skyfield
│   └── models.py         # databázové modely
├── .github/
│   └── workflows/        # CI/CD deploy
└── README.md
```

---

## Iterace

### Iterace 1 — "Kdy přiletí?"
**Cíl:** predikce přeletů nad mojí polohou

- Stažení TLE dat z Celestrak (celestrak.org)
- SGP4 výpočet přeletů na příštích 24h
- FastAPI endpoint `/passes`
- Jednoduchá HTML stránka se seznamem přeletů
- Zobrazení: čas, max elevace, azimut, délka přeletu

**Výstup:** otevřeš prohlížeč, vidíš "NOAA-19 přiletí za 47 minut, max elevace 62°"

**Klíčové knihovny:**
- `skyfield` — propagace TLE, výpočet přeletů (AOS/LOS/max elevation), souřadnicové transformace
- `requests` — stažení TLE dat
- `fastapi` + `uvicorn`

---

### Iterace 2 — "Kde teď je?"
**Cíl:** živá mapa se satelitem

- Leaflet.js mapa v prohlížeči
- Aktuální poloha satelitu aktualizovaná každých 5 sekund (HTMX polling `hx-trigger="every 5s"` → `GET /satellite/position`)
- Viditelný footprint satelitu (kruh dosahu)
- Tvoje poloha na mapě
- Vizualizace nadcházejícího přeletu

**Výstup:** koukáš na mapu a vidíš satelit jak se pohybuje v reálném čase

---

### Iterace 3 — "Nahraju signál"
**Cíl:** první skutečný kontakt s RTL-SDR

- Připojení RTL-SDR V3 přes `pyrtlsdr`
- Automatické spuštění nahrávání při začátku přeletu (scheduler)
- Nahrávání přímo jako WAV na 48 kHz (dostačující pro NOAA APT, ~55 MB/přelet místo 2.7 GB raw IQ)
- Základní SNR měření při záznamu
- Mock mode pro CI/CD testování (syntetická IQ data bez hardware)
- Hardware: RTL-SDR V3 k dispozici, primárně Mac (`brew install librtlsdr`), fallback Windows s klasickým USB
- Doppler korekce přesunuta do bonusových funkcí (viz níže)

**Výstup:** po přeletu máš soubor se zaznamenaným signálem + SNR log

**Klíčové knihovny:**
- `pyrtlsdr` — ovládání RTL-SDR
- `numpy` — zpracování IQ dat
- `scipy` — DSP operace

---

### Iterace 4 — "Co přijal?"
**Cíl:** dekódování a zobrazení dat

**Fáze 4a — NOAA APT:**
- Zpracování nahraného WAV souboru
- Dekódování APT přes `noaa-apt` CLI (subprocess) → satelitní snímek počasí jako PNG
- Zobrazení obrázku v dashboardu
- Vlastní APT dekodér se neimplementuje — `noaa-apt` je aktivně udržovaný, cross-platform (Mac + Windows)

**Fáze 4b — CubeSat telemetrie:**
- Cílový satelit: **FUNCUBE-1 (AO-73)**, 145.935 MHz — aktivní, otevřený formát, Python parser od komunity
- Dekódování AX.25 protokolu
- Parsování FUNcube telemetrie (teplota, napětí, stav systémů) — hotový spec na funcube.org.uk
- Zobrazení dat v tabulce/grafu
- Ukládání raw IQ s retention policy (48h, po dekódování smazat — doplnit při zahájení 4b)

**Výstup:** vidíš konkrétní data přijatá ze satelitu

---

### Iterace 5 — "Contact log"
**Cíl:** operations mindset — tohle je Groundcom část

- SQLite databáze každého kontaktu
  - čas začátku a konce kontaktu
  - délka kontaktu (sekundy)
  - max elevace
  - průměrný a max SNR
  - co se přijalo (soubory, telemetrie)
  - kvalita přijmu (OK / degradovaný / ztracený)
- Dashboard s historií kontaktů
- Graf SNR trendů v čase
- Export reportu (CSV)
- Push notifikace přes ntfy.sh "za 10 min přiletí XYZ" (jednoduchý HTTP POST, žádná SMTP závislost)

**Výstup:** profesionální operations log, který ukáže Groundcomu že chápeš monitoring ground station

---

## Groundcom bonusové funkce
Po dokončení základních iterací — pokud zbyde čas:

- **Real-time Doppler kompenzace** — automatická korekce frekvence při přeletu
- **Multi-satelit scheduling** — seznam satelitů s prioritami, plánování konfliktů
- **SNR degradace monitoring** — upozornění když klesá kvalita přijmu
- **Alerting** — notifikace před přeletem

---

## Časový plán
~5 hodin týdně → každá iterace 2–3 týdny → celý projekt ~3–4 měsíce

## Poznámky
- Začít s NOAA satelity (137 MHz, dekódování APT přes noaa-apt CLI)
- Pak FUNCUBE-1 / AO-73 (145.935 MHz, AX.25 + otevřený telemetrie formát)
- TLE data: celestrak.org nebo space-track.org
- SatNOGS databáze: db.satnogs.org (seznam satelitů, frekvencí, dekodérů)
- Mac: nutná USB-A → USB-C redukce pro RTL-SDR dongle
- Driver: `brew install librtlsdr`, ověření: `rtl_test`
