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
  s odchycením `IntegrityError` (race / tz round-trip). Constraint na existující
  Render DB doplní **migrace M** (Alembic) — viz níže.
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
- Nové sloupce `avg_snr`/`quality`/`notes` + constraint doplní na produkci
  **migrace M** (Alembic) — viz níže.
- Test-DB izolace: `conftest` teď dropne+recreatne tabulky před každým testem
  (kontakty se jinak hromadily a rozbíjely count/CSV asserty).

## Iterace M — Migrace (Alembic) ✅ HOTOVO 2026-07-02
*Cíl: dostat schema změny z F/5 bezpečně na živou Render DB. ~1 večer.*

1. [x] Alembic scaffolding, `env.py` napojený na `shared.models.Base` a
   `DATABASE_URL` (ošetřen Render `postgres://` → `postgresql://`).
2. [x] `0001_baseline` — schema jaké je teď na Renderu; přeskočí CREATE,
   když `contacts` už existuje (bezpečné bez ručního `stamp`).
3. [x] `0002_add_ops_columns` — přidá sloupce (guardované), odmaže duplicitní
   přelety, přidá `uq_contact_pass`. Idempotentní.
4. [x] CI smoke-test: `alembic upgrade head && alembic downgrade base`.
5. [x] **Na Renderu**: migrace běží při deployi — vyřešeno **prependnutím do
   Start Command** (`alembic upgrade head && uvicorn ...`), ne přes Pre-Deploy
   Command.

**Ověřeno lokálně** proti SQLite kopii starého schématu: 3 řádky (s duplicitou)
→ upgrade → sloupce doplněny, duplicita odmazána (3→2), constraint přidán;
idempotence, čerstvá DB i downgrade OK. Detaily v `alembic/README`.

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

## Iterace L — Mobilní stanice (auto-poloha) ✅ HOTOVO 2026-07-14
*Cíl: observer sleduje skutečnou polohu zařízení — stanice není pevná.*

1. [x] `agent/location.py` — IP geolokace (ipinfo.io, HTTPS, bez klíče),
   cache 15 min, `OBSERVER_MODE=auto|manual`, fallback na env.
2. [x] Agent hlásí polohu appce (`POST /observer`, auth), při změně polohy
   invaliduje pass cache (`tle.set_observer`) a přepočítá predikce.
3. [x] App: tabulka `station_status` (migrace 0003), poloha přežije restart,
   mapa + predikce ji používají; marker se posouvá při každém pollu.
4. [x] Mapa: sidebar sekce „Stanice" (souřadnice, zdroj, čas hlášení).
5. [x] Auth helper vytažen do `app/auth.py` (sdílí contacts + observer).

**Ověřeno:** 41 testů, E2E (výchozí → hlášení → přesun markeru → restart →
poloha z DB), reálná IP detekce na zařízení. Přesnost IP geolokace je ~km,
což pro predikce bohatě stačí (footprint NOAA ~3000 km). Pozor na VPN —
pak `OBSERVER_MODE=manual`.

## Iterace P — Prezentace / portfolio finish ⏸️ ODLOŽENO NA NEURČITO (2026-07-22)
*Cíl: projekt se dá ukázat u pohovoru. ~1 večer.*

**Odloženo na neurčito.** Nemá termín ani pořadí, dělá se až se rozhodne, že je
čas. Body zůstávají sepsané, aby se k nim dalo vrátit.

1. [ ] `README.md`: co to je, architektura (diagram z CLAUDE.md),
   screenshoty mapy + dashboardu + dekódovaný snímek, live demo link,
   „lessons learned" (Doppler, antény, SNR).
2. [ ] Landing `/` místo redirectu: mini přehled (další pass, poslední
   kontakt, poslední snímek).
3. [ ] Vyčistit repo: `decode_apt.py` → `scripts/`, aktualizovat CLAUDE.md.

## Iterace M2 — IQ pipeline (Meteor + Orbcomm)
*Cíl: agent umí sám nahrát a dekódovat to, co dnes děláme ručně. ~4 večery.*

**Proč:** `recorder.py` dělá FM demodulaci → 48 kHz WAV. To zničí fázovou
informaci, na které stojí Meteor (OQPSK) i Orbcomm (SDPSK). `decoder.py` volá
`noaa-apt`, dekodér mrtvého režimu. Obojí je slepá ulička — potřebujeme
**baseband IQ**.

**Klíčové rozhodnutí: oddělit nahrávání od dekódování.** Nahrávání musí stihnout
přelet v reálném čase, dekódování ne. Ověřeno 22. 7.: realtime Orbcomm dekodér
selhal, ale z uloženého IQ jsme dekódovali s PER 0,0 %.

### M2.1 — IQ recorder ✅ HOTOVO 2026-07-22
- [x] `recorder.py` ukládá surové IQ (`cs16`), ne demodulované audio
- [x] **Bias tee zapnout** — změřeno +14 dB; bez toho je LNA útlum
- [x] Manuální gain z env (`SDR_GAIN`), ne AGC
- [x] Vzorkovací frekvence per satelit: Meteor 1 MHz, Orbcomm 1,2288 MHz
- [x] Callback po každém chunku (pro M3 — progress a SNR)

> **Akceptační kritéria M2.1**
> 1. Nahrávka 10min přeletu vznikne jako `.cs16` a jde ji beze změny předat
>    SatDumpu (`--baseband_format s16`) i `file_decoder.py`.
> 2. V mock režimu (`MOCK=1`) vznikne syntetické IQ bez hardwaru; testy projdou.
> 3. Log obsahuje potvrzení, že bias tee je zapnutý.
> 4. Callback je volán ≥1×/s a dostane elapsed, total a naměřený SNR.

**Výstup:** `record_iq()` vrací `Recording` (cesta, pass adresář, sample rate,
SNR profil). Jeden adresář na přelet: `recordings/<pass>/<pass>.cs16` +
`meta.json` + `products/`. 62 testů, ruff clean.

**Poznámky k realizaci:**
- Zápis je **streamovaný** — 3 GB se do RAM nevejde. Čte se `read_bytes()`
  a uint8 páry se převádějí rovnou na int16 (`(b-127) << 7`), bez pyrtlsdr
  float64 mezikroku; při 1,2288 Msps to není kosmetika.
- **SNR se měří při nahrávání**, ne dodatečně — soubor je na druhý průchod moc
  velký. Metrika je záměrně signal-agnostická (špička PSD vs. medián), aby
  fungovala na úzký Orbcomm nosič i široký Meteor. Celý profil
  (`snr_series`) se ukládá do `meta.json` → hotový vstup pro sparkline v M3.3.
- `meta.json` nese i **polohu stanice v době nahrávání** (stanice je mobilní,
  Orbcomm dekodér polohu potřebuje) a je zároveň značkou „tohle napsal agent",
  podle které se řídí retence.
- Výchozí `SDR_GAIN` změněn 49.6 → **20.7** (hodnota ověřená s LNA této
  stanice); AGC teď loguje varování.
- Ověřeno v mocku: 4 s @ 1,2288 Msps = 19,7 MB → ~3 GB na 10min přelet, přesně
  jak odhaduje M2.4. **Na reálném hardwaru zatím neověřeno — dongle nebyl
  připojen** (kritérium 1 a 3 čeká na příští přelet).

### M2.2 — Dekódovací dispatcher ✅ HOTOVO 2026-07-22
- [x] `decoder.py` = rozcestník podle satelitu (subprocess, jako dnes `noaa-apt`)
- [x] Meteor → `satdump meteor_m2-x_lrpt` (pozor: spouštět z `Resources` složky)
- [x] Orbcomm → `file_decoder.py`
- [x] Neúspěch dekódování nesmí shodit agenta ani smazat IQ

> **Akceptační kritéria M2.2**
> 1. Meteor nahrávka → PNG produkty v `recordings/<pass>/products/`.
> 2. Orbcomm nahrávka → strukturovaný výstup (rámce, PER, sat_id, efemeridy).
> 3. Chybějící/rozbitý dekodér = zalogovaný `notes`, contact se přesto odešle.
> 4. Test na **reálné nahrávce z 22. 7.** (FM118) dá PER 0,0 % — regresní jistota.

**Výstup:** `decode(pass_dir)` vrací `DecodeResult` (success, kind, products,
stats, notes). Kritérium 4 splněno: `tests/test_orbcomm_real.py` na nahrávce
`1784673817p299` dává **PER 0,0 %, 98 rámců, efemeridy 22,7 km od TLE predikce**.
80 testů, ruff clean.

**Poznámky k realizaci:**
- Orbcomm most (`agent/orbcomm.py`) je nejzajímavější kus: `file_decoder.py` je
  výzkumný skript s kódem na úrovni modulu, matplotlib okny a vstupem ve formátu
  `.mat` z upstream recorderu. Místo přepisování DSP se most trefí do jeho
  rozhraní — vybere okno, zapíše `.mat`, spustí skript, rozparsuje stdout.
- **Výběr okna dělá SNR profil z M2.1.** Dekodér chce 2 s, přelet trvá minuty
  a rozdíl mezi horizontem a TCA byl 22. 7. rozdíl mezi PER 18 % a 0 %. Bez
  profilu se bere střed přeletu.
- `MPLBACKEND=Agg` je to, co dělá skript použitelným bez obsluhy — končí
  `plt.show()`, což by jinak čekalo na okno donekonečna.
- Počet rámců se bere z `packets.txt` (jeden hex rámec na řádek), ne z parsování
  výpisu; parsování hlásilo i řádky preambule jako rámce.
- **Změna: Orbcomm se ladí na 137,5 MHz**, ne na kanál satelitu. Všechny kanály
  se pak vejdou do pásma, žádný nesedí na DC špičce a soubor vypadá přesně jako
  upstream nahrávky, proti kterým je dekodér odladěný.
- TLE pro pyephem se bere z naší SatNOGS cache (`shared.tle.tle_lines`), ne
  z vendorovaného `tles/` adresáře, který upstream skript neumí spolehlivě
  aktualizovat (viz `patches/README.md`).
- **Meteor cesta je ověřená jen po úroveň příkazové řádky** — satdump
  `meteor_m2-x_lrpt` proběhl celý na syntetickém šumu a korektně nevyrobil
  snímek. Reálné Meteor IQ ze 17. 7. se nedochovalo (zůstaly jen produkty),
  takže skutečný snímek z naší nahrávky čeká na příští přelet.

### M2.3 — Datový model pro telemetrii ✅ HOTOVO 2026-07-22
- [x] `Contact.contact_type` (`image` / `telemetry`)
- [x] Tabulka/JSON pro dekódovaná data (rámce, PER, sat_id, efemeridy)
- [x] Dashboard: u telemetrie zobrazit počty rámců a PER místo náhledu snímku
- [x] Alembic migrace

> **Akceptační kritéria M2.3**
> 1. Orbcomm contact se na dashboardu zobrazí smysluplně (ne prázdná karta).
> 2. CSV export obsahuje i telemetrické kontakty.
> 3. `alembic upgrade head` projde na kopii produkční DB.

**Výstup:** telemetrická karta s rámci, PER, typy paketů a efemeridami
(souřadnice, výška, rychlost, odchylka od TLE). Ověřeno vizuálně. 95 testů,
ruff clean.

**Poznámky k realizaci:**
- **Kvalita se u telemetrie počítá z PER, ne ze SNR.** Čistý dekód při skromném
  SNR je dobrý přelet, ne degradovaný — rámce buď sedí, nebo ne. Práh
  `QUALITY_PER_OK=5 %`, nula rámců = `lost`.
- Dekódovaná data jsou jeden JSON sloupec, ne vlastní tabulka: tvar se liší
  dekodér od dekodéru a čte to jen dashboard. `sqlalchemy.JSON` funguje na
  SQLite i Postgresu.
- Neúspěšně dekódovaný Orbcomm přelet je pořád `contact_type=telemetry` —
  dashboard pak hlásí „telemetrie nedekódována" místo „snímek nedostupný".
- Fronta `pending.db` v agentovi umí doplnit chybějící sloupce do existující
  tabulky (`ALTER TABLE`), takže upgrade agenta neztratí čekající kontakty.
- **Migrace 0004 ověřena na kopii staré produkční DB** (schéma před 0002, jeden
  reálný řádek): upgrade z prázdné historie až na head, opakovaný upgrade jako
  no-op, downgrade o krok zpět bez ztráty řádku, downgrade až na base.

### M2.4 — Retenční politika (NUTNÁ, ne volitelná) ✅ HOTOVO 2026-07-22
- [x] IQ smazat po **úspěšném** dekódování; při selhání ponechat (k ladění)
- [x] Strop na celkovou velikost `recordings/` + úklid nejstarších

> **Proč nutná:** ~10 min × 1,2288 MHz × 4 B ≈ **3 GB na přelet**. Při 42
> přeletech denně je disk pryč za den. Oproti dosavadním 55 MB WAV je to
> tisícinásobek.
>
> **Akceptační kritéria M2.4**
> 1. Po úspěšném dekódování zůstanou produkty, IQ zmizí.
> 2. Po selhání IQ zůstane a je v logu důvod.
> 3. `recordings/` nikdy nepřeroste nastavený strop.

**Výstup:** `agent/retention.py` (`after_decode`, `enforce_cap`, `has_room_for`),
strop `RECORDINGS_MAX_GB` (výchozí 20). Scheduler uklízí **před** přeletem
i po dekódování.

**Poznámky k realizaci:**
- Uklízí se **před** nahráváním, ne až po něm: plný disk uprostřed přeletu
  přelet ztratí a ten se nevrátí.
- Maže se **jen to, co napsal agent** — pass adresáře s `meta.json`. Ruční
  nahrávky ze SDR++ a referenční snímky v `recordings/` (dnes 261 MB) zůstávají,
  i kdyby to znamenalo zůstat nad stropem; v tom případě je v logu varování.
- Právě nahrávaný přelet je z úklidu vyjmutý (`keep=`), i když je nejstarší.
- Pravidlo „smaž po úspěšném dekódování" je zapojené, ale naostro se projeví až
  s dispatcherem v M2.2 — do té doby scheduler hlásí selhání dekódování a IQ
  drží.

---

## Iterace M3 — Stránka „Přelet" (live řízení a vizualizace)
*Cíl: jedna obrazovka, která řídí a ukazuje celý životní cyklus přeletu. ~3–4 večery.*

Nová sekce `/pass` vedle Přelety / Mapa / Dashboard. Zatímco `/passes` je
**seznam** a `/dashboard` je **historie**, `/pass` je **operační konzole pro
právě probíhající (nebo nejbližší) přelet**.

### Architektura
Agent je za NATem, takže push z appky nejde. Použije se **stejný vzor jako
`post_observer`**: agent tlačí stav, prohlížeč polluje.

```
agent (Mac)                          app (Render)         prohlížeč
recorder, po každém chunku:
  ├ SNR z FFT
  ├ % hotovo            ──POST /station/live──→ drží  ──poll 5 s──→ /pass
  └ satelit, AOS/LOS        (á 2 s)          poslední stav
```

### M3.1 — Live status endpoint ✅ HOTOVO 2026-07-22
- [x] `POST /station/live` (auth stejná jako `/contacts`) — stav agenta
- [x] `GET /station/live` — poslední stav + `last_seen`
- [x] Heartbeat i mimo přelet (aby šlo poznat „agent žije, jen nenahrává")

> **Akceptační kritéria M3.1**
> 1. Bez tokenu → 401; nevalidní data → 422 (jako `/observer`).
> 2. Když agent 30 s mlčí, `GET` vrací `state: "offline"`.
> 3. Stav přežije restart appky (jako `station_status`), nebo je čistě
>    in-memory a po restartu korektně hlásí `offline` — vybrat a otestovat.

**Rozhodnutí k bodu 3: čistě in-memory.** Stav popisuje, co stanice dělá *teď*;
po restartu appky je poctivá odpověď „nevím" a další heartbeat je do 10 s.
Poloha stanice je něco jiného a v DB zůstává. Otestováno (`reset_live()`
simuluje restart).

**Poznámky k realizaci:**
- Agent tepe každých 10 s (`_sleep_with_heartbeat` spí po ≤10s kouscích),
  console hlásí offline po 30 s ticha — tedy tři zmeškané tepy.
- Při nahrávání jde stav do appky každé 2 s z recorder callbacku z M2.1,
  zatímco do logu se píše po 30 s. Dva různé rytmy schválně: log se čte potom,
  konzole se sleduje teď.
- `post_live()` je fire-and-forget s 5s timeoutem a spolkne všechno. Běží uvnitř
  nahrávací smyčky a ztracený status update je neviditelný, ztracený kus přeletu ne.
- Offline stav si drží poslední hlášená pole jako kontext, ale `state` přepíše
  na `offline` — nikdo se stanicí půl minuty nemluvil, takže tvrdit „nahrává"
  by byla lež.

### M3.2 — Vizualizace oblohy (polární graf) ✅ HOTOVO 2026-07-22
- [x] Az/el oblouk přeletu ze skyfieldu (krok 30 s), inline SVG
- [x] Vyznačit AOS, TCA, LOS a světové strany
- [x] Během přeletu živá poloha satelitu na oblouku

> **Akceptační kritéria M3.2**
> 1. Oblouk odpovídá predikci (AOS/LOS azimut sedí s `/passes`).
> 2. Funguje i mimo přelet — ukazuje **nejbližší** přelet dopředu.
> 3. Bez hardwaru a bez agenta se stránka vykreslí (jen bez živé polohy).

**Poznámky k realizaci:**
- `shared.tle.pass_track()` vektorizuje celý oblouk do jednoho volání skyfieldu
  (stejně jako ground track v A3), ne vzorek po vzorku.
- Zenit je ve středu, obzor na okraji, sever nahoře. Chytlo to při vizuální
  kontrole chybu: `_polar_xy` cvaká zápornou elevaci na okraj, takže satelit
  pod obzorem dostával sebevědomou tečku na obloze. Teď se živá tečka kreslí
  jen při `el >= 0` a legenda říká „satelit ještě pod obzorem (-21°)".

### M3.3 — Progress a síla signálu ✅ HOTOVO 2026-07-22
- [x] Progress bar (elapsed/total) + aktuální elevace a azimut
- [x] **Sparkline SNR v čase**, živě rostoucí
- [x] Fázový stav: `čeká` → `nahrává` → `dekóduje` → `hotovo` / `chyba`

> **Akceptační kritéria M3.3**
> 1. Během přeletu se progress hýbe a SNR křivka roste k TCA
>    (ověřitelné proti profilu z 22. 7.: PER 18 % → 0 % → 3 %).
> 2. Po LOS stránka přejde do `dekóduje` a pak na výsledek.
> 3. Když agent umře uprostřed, UI to do 30 s pozná.

**Výstup:** stránka `/pass` — stavový řádek, hlavička přeletu, polární graf
oblohy, progress bar a živá SNR křivka. Server renderuje fragment
`/pass/panel`, prohlížeč ho polluje po 5 s (žádné WebSockety, stejně jako
zbytek appky). 108 testů, ruff clean. Ověřeno vizuálně na simulovaném přeletu
(SNR křivka roste k TCA a zase klesá, progress 66 %, agent online).

**Iterace M3 je hotová** (M3.1–M3.5). Zbývají jen návrhy z tabulky níže.

### M3.4 — Event log přeletu ⭐ ✅ HOTOVO 2026-07-22
- [x] Časová osa událostí: AOS, start nahrávání, zámek, ztráta zámku, LOS,
      start/konec dekódování, výsledek
- [x] Uchovat u contactu, zobrazit i zpětně

> **Proč:** veškeré ladění 17.–22. 7. bylo „čti log". Mít to v UI je přesně ten
> operations mindset, který Groundcom chce vidět.
>
> **Akceptační kritéria M3.4**
> 1. Po přeletu lze z časové osy rekonstruovat, co se dělo a kde to selhalo.
> 2. Události mají čas a jsou seřazené.

**Výstup:** `agent/events.py` (`PassLog`), `POST /station/event`, sloupec
`Contact.events` (migrace 0005). Živě na `/pass`, zpětně rozbalovací „Průběh
přeletu" u contactu na dashboardu.

**Poznámky k realizaci:**
- **Slovo „zámek" jsem záměrně nepoužil.** Recorder nedemoduluje, takže o žádném
  zámku nemůže vědět. Místo toho jsou události `signal_acquired` /
  `signal_lost` odvozené z SNR streamu (práh 8 dB, ztráta až po 20 s ticha),
  což je to, co stanice opravdu ví.
- Události se posílají dvakrát: hned jak nastanou (aby je konzole ukázala
  během přeletu) a celé znovu s contactem (aby šly číst za půl roku).
  Reportování je best-effort, rozbitá síť nesmí zastavit přelet.

### M3.5 — Stav stanice (health) ⭐⭐ ✅ HOTOVO 2026-07-22
- [x] Agent online/offline, SDR připojený, **bias tee**, gain, frekvence, poloha
- [x] Varování, když je něco podezřelé (bias tee vypnutý při použití LNA)

> **Proč tohle považuju za nejcennější přírůstek:** za dva dny nás zdržely
> přesně tyhle věci — odpojený dongle, SatDump držící zařízení, **nenapájený
> LNA**. Kdyby to bylo vidět na jedné obrazovce *před* přeletem, ušetří to
> hodiny. Provozně je to důležitější než hezký graf.
>
> **Akceptační kritéria M3.5**
> 1. Odpojení dongle se projeví do 30 s.
> 2. Vypnutý bias tee při nakonfigurovaném LNA = viditelné varování.
> 3. Panel funguje i když žádný přelet neprobíhá.

**Výstup:** `agent/health.py` + panel „Stav stanice" na `/pass`. Varování na:
mlčící agent, chybějící SDR, **SDR držený jiným procesem**, vypnutý bias tee
s LNA v cestě, zapnuté AGC, MOCK režim a docházející místo na disku.

**Poznámky k realizaci:**
- **„busy" je vlastní stav SDR, ne chyba.** Dongle držený SatDumpem vypadá
  zvenku jako funkční stanice až do chvíle, kdy začne přelet a nahrávání
  spadne. Přesně tohle nás v červenci stálo přelety.
- Sonda otevře a hned zavře zařízení, takže **nikdy neběží během nahrávání** —
  vzala by dongle tomu, kdo ho zrovna potřebuje. Při nahrávání panel hlásí
  `recording`, jinak se sonduje nejvýš jednou za minutu.
- Nový env `LNA_PRESENT` (default 1) říká, jestli má smysl varovat na bias tee.
- Varování počítá server z toho, co agent nahlásil, ne agent sám: pravidla se
  tak dají měnit bez deploye agenta.

### Co dál na stránku — návrhy k rozhodnutí

| Prvek | Hodnota | Náklad | Poznámka |
|---|---|---|---|
| **Fronta dalších přeletů** | vysoká | nízký | 3–5 dalších + který se bude nahrávat |
| **Doppler: predikce vs. měření** | vysoká | střední | přesně tím jsme 21. 7. dokázali, že jde o satelit |
| **Výsledek dekódování inline** | vysoká | nízký | rámce, PER, náhled snímku hned po přeletu |
| **Spektrum v TCA (statické)** | střední | nízký | jeden snímek místo živého waterfallu |
| **Ruční ovládání** (arm/skip/record) | střední | **vysoký** | ⚠️ vyžaduje, aby agent **polloval příkazy** — obrácení toku, návrh zvlášť |
| **Živý waterfall** | střední | vysoký | ~9 MB/25 min; přes 5s polling trhané. **Odložit** |

> ⚠️ **Ruční ovládání je architektonicky jiná liga.** Dnes agent jen tlačí.
> Aby ho šlo z webu ovládat, musí si chodit pro příkazy (`GET /station/commands`).
> Řešitelné, ale je to samostatné rozhodnutí — ne „ještě jedno tlačítko".

---

## Bonusy (jen pokud zbyde čas — dle PLAN.md)
- Doppler kompenzace při nahrávání (skyfield dává range rate zadarmo).
- Multi-satelit konflikt scheduling (dva passy naráz → priorita dle max el).
- SNR degradace alerting (ntfy když klesne pod práh vs. průměr).

## Doporučené pořadí
**T → F → 5 → M → L → M2 → M3** — vše hotovo k 2026-07-22.
**P je odložené na neurčito.**

Zbývá: návrhy z tabulky u M3, iterace 4b (FUNcube) a bonusy — v libovolném
pořadí. Nad vším visí jedno: **celý IQ pipeline je zatím ověřený jen na
uložených datech a v mocku.** První přelet s připojeným donglem je důležitější
než další funkce.

M2 před M3 dávalo smysl: nemá cenu vizualizovat pipeline, která ještě neběží.
M2.1 spolu s M2.4 taky — bez retence by 3 GB/přelet zavalily disk hned při
prvním testu.
