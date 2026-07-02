# ROADMAP — dokončení Mini Ground Station

Navazuje na PLAN.md (původní zadání). Stav k 2026-07-02: iterace 1–4a hotové
(predikce, mapa, nahrávání, APT dekódování, dashboard). Tento dokument řadí
zbývající práci do malých kroků — každý krok je samostatně dokončitelný
a na konci má fungující výsledek.

## Zjištění z auditu (2026-07-02)

### Chyby / rizika
- [x] **A1 — Zpožděný start nahrávání zkracuje pass špatným směrem.** ✅ F
  Scheduler nahrává `record_s = (los - now)` sekund místo fixní `duration_s`.
- [x] **A2 — `predict_passes()` bez cache v horké cestě.** ✅ F
  `/passes` i scheduler používají `get_cached_passes()` (5min TTL,
  `minutes_until` se obnovuje při každém volání). Bonus: opraven off-by-one,
  kdy scheduler v poslední minutě před AOS přelet zahodil (`int(59s/60)==0`) —
  teď filtruje `los > now`, takže chytne i právě probíhající přelet.
- [x] **A3 — Ground track 3× 91 skyfield vzorků při každém pollu.** ✅ F
  Vektorizováno do jednoho `ts.tt_jd(np.arange(...))` volání per satelit.
- [x] **A4 — Duplicitní kontakty při retry.** ✅ F
  `UniqueConstraint(satellite, aos)` v modelu + app-level kontrola: duplicita
  vrací 200 s existujícím id, `IntegrityError` se odchytí (race/tz round-trip).
- [x] **A5 — Auth porovnání sekretů přes `!=`.** ✅ F — `secrets.compare_digest`.
- [x] **A6 — Nevalidní datum v POST /contacts → 500.** ✅ F — parsuje se
  bezpečně, vrací 422.
- [x] **A7 — Gain natvrdo 49.6 v recorderu.** ✅ F — env `SDR_GAIN`
  (číslo v dB nebo `auto` pro AGC), zdokumentováno v `.env.example`.
- [x] **A8 — Žádné testy a žádné CI.** ✅ T (viz iterace T).

### Menší vylepšení (nice-to-have, průběžně)
- [ ] Root `decode_apt.py` — jednorázový skript; přesunout do `scripts/`
  nebo smazat (funkčnost je v `agent/decoder.py`).
- [ ] `noaa-apt/` vendorovaný Rust zdroják v repu — nepatří sem (je
  v .gitignore, jen lokální; případně přesunout mimo projekt).
- [ ] Sjednotit trojí duplicitní inicializaci Jinja2 v routes do jednoho
  modulu (`app/templating.py`).
- [ ] `README.md` chybí — pro portfolio klíčové (screenshoty, architektura,
  odkaz na live demo). Viz iterace P.

---

## Iterace T — Testy + CI (základ pro vše další) ✅ HOTOVO 2026-07-02
*Cíl: bezpečně refaktorovat a přidávat funkce. ~1 večer.*

1. [x] `pytest` + `httpx` + `ruff` do `requirements-dev.txt`, `tests/` adresář.
2. [x] Testy `shared/tle.py`: parsování TLE z fixture JSON, predikce passů
   (invarianty: aos<los, duration>0, řazení), footprint radius, cache.
3. [x] Testy API: `POST /contacts` (auth OK / špatný token / nevalidní
   datum jako xfail→A6), `GET /passes`, `GET /satellite/position`, dashboard.
4. [x] Test mock recorderu + `measure_snr` na syntetickém signálu.
5. [x] GitHub Actions workflow: lint (ruff) + pytest na push/PR.

**Výstup:** 18 passed / 1 xfailed, ruff clean, `.github/workflows/ci.yml`.

**Poznámky k realizaci:**
- Lokální venv je **Python 3.9.6**, Render běží **3.12** → ruff `target-version = "py39"`,
  kód držet 3.9-kompatibilní (žádné `datetime.UTC`, `X | Y` typy jen v anotacích jsou OK).
- Testy jsou hermetické: `conftest.py` nastaví izolovanou SQLite + `AGENT_SECRET`
  před importem app a podstrčí fixture TLE cache (žádná síť na SatNOGS).
- `test_post_contact_invalid_date` je `xfail` — dokumentuje A6, po opravě v F
  se změní na xpass (strict=False, tj. nerozbije CI).
- Predikce passů se netestuje na přesné hodnoty (skyfield + `ts.now()` je
  nedeterministické) — až iterace F přidá `t0` param, dá se přitvrdit.

## Iterace F — Opravy z auditu ✅ HOTOVO 2026-07-02
*Cíl: stabilní provoz agenta. ~1–2 večery. Vyžaduje T (testy na regrese).*

1. [x] A1 — nahrávat do LOS, ne fixní délku.
2. [x] A2 + A3 — cache predikcí (+ off-by-one fix) a vektorizace ground tracků.
3. [x] A4 — idempotentní POST /contacts (unique satellite+aos, 200 na duplicitu).
4. [x] A5, A6, A7 — drobné opravy + doplněný `.env.example`.

**Výstup:** agent přežije zpožděný start i probíhající přelet, retry bez
duplicit, web nešahá na skyfield při každém requestu. **20 passed**, ruff clean.

**Poznámky k realizaci:**
- `get_cached_passes()` teď obnovuje `minutes_until` při každém volání, takže
  cache neovlivní odpočet na `/passes` ani na mapě.
- Idempotence má dvě vrstvy: app-level lookup (rychlá cesta) + DB constraint
  s odchycením `IntegrityError` (race / tz round-trip). Constraint platí pro
  **nově vytvořené** tabulky; existující DB na Renderu drží jen app-level guard
  (migrace tabulky není automatická — kdyby vadily staré duplicity, dořešit
  ručně nebo přes Alembic).
- Off-by-one ve scheduleru byl latentní i před cache — stál za samostatnou
  opravu (filtr `los > now` místo `minutes_until > 0`).

## Iterace 5 — Contact log & operations (z PLAN.md) ✅ HOTOVO 2026-07-02
*Cíl: Groundcom část — operations mindset. ~2–3 večery.*

1. [x] Model `Contact` rozšířen: `avg_snr`, `quality` (ok/degraded/lost),
   `notes`. Stávající `snr` = peak SNR.
2. [x] SNR měřeno per 10 s okno (`measure_snr_windows` → avg + peak), agent
   posílá obojí; `quality` se odvozuje server-side z peak SNR + přítomnosti
   snímku (prahy `QUALITY_OK_DB=10`, `QUALITY_DEGRADED_DB=3`).
3. [x] Dashboard: filtr podle satelitu (dropdown), stránkování (10/stránku),
   badge kvality (barevné) + avg SNR + notes.
4. [x] SNR trend graf — inline SVG sparkline (posledních 30 kontaktů,
   respektuje filtr).
5. [x] `GET /contacts/export.csv` — export všech sloupců.
6. [x] ntfy.sh notifikace z agenta ~10 min před AOS (`NTFY_TOPIC`,
   `notify_upcoming`, jednou per přelet).

**Výstup:** ověřeno vizuálně (screenshot dashboardu s grafem + badge),
30 passed, ruff clean.

**Poznámky k realizaci:**
- Retry fronta (`pending.db`) rozšířena o `avg_snr` + `notes`, aby se při
  výpadku sítě neztratily.
- Stejný **migrační dluh** jako A4: nové sloupce `avg_snr`/`quality`/`notes`
  se na existující Render PostgreSQL nepřidají automaticky (`create_all`
  neupravuje existující tabulky) — dořešit společně s A4 (Alembic /
  `ALTER TABLE ADD COLUMN`, sloupce jsou nullable, takže bezpečné).
- Test-DB izolace: `conftest` teď dropne+recreatne tabulky před každým testem
  (kontakty se jinak hromadily a rozbíjely count/CSV asserty).

## Iterace 4b — FUNCUBE-1 / AO-73 telemetrie (z PLAN.md)
*Cíl: skutečná CubeSat telemetrie. ~3+ večery, nejtěžší část.*

1. [ ] Přidat FUNCUBE-1 (NORAD 39444) do `shared/tle.py` + frekvenci
   145.935 MHz do scheduleru; per-satelit typ dekodéru (APT vs. BPSK).
2. [ ] Recorder: režim pro 1200 baud BPSK (jiná šířka pásma, SSB — nutná
   úprava demodulace; ověřit nejdřív nahrávkou přes SDR++).
3. [ ] Dekodér: FUNcube AX.25/FEC rámce → telemetrie (spec funcube.org.uk;
   zvážit hotový `funcube-telemetry` parser z komunity dle PLAN.md).
4. [ ] Model `Telemetry` (contact_id FK, klíč/hodnota nebo pevné sloupce:
   napětí, teploty, stav) + POST endpoint + tabulka/graf v dashboardu.
5. [ ] Retention policy nahrávek: WAV starší 48 h po úspěšném dekódování
   smazat (cron v scheduleru).

**Výstup:** tabulka reálné telemetrie (napětí, teploty) z AO-73.

## Iterace P — Prezentace / portfolio finish
*Cíl: projekt se dá ukázat u pohovoru. ~1 večer.*

1. [ ] `README.md`: co to je, architektura (diagram z CLAUDE.md),
   screenshoty mapy + dashboardu + dekódovaný snímek, live demo link,
   „lessons learned" (Doppler, antény, SNR).
2. [ ] Landing `/` místo redirectu: mini přehled (další pass, poslední
   kontakt, poslední snímek).
3. [ ] Vyčistit repo: `decode_apt.py` → `scripts/`, aktualizovat CLAUDE.md.

## Bonusy (jen pokud zbyde čas — dle PLAN.md)
- Doppler kompenzace při nahrávání (skyfield dává range rate zadarmo).
- Multi-satelit konflikt scheduling (dva passy naráz → priorita dle max el).
- SNR degradace alerting (ntfy když klesne pod práh vs. průměr).

## Doporučené pořadí
**T → F → 5 → 4b → P** (bonusy kdykoli po F).
Testy první — všechno ostatní se pak dělá bezpečně. Iterace 5 před 4b:
je levnější, viditelnější pro Groundcom a nezávisí na novém hardwaru/DSP.
