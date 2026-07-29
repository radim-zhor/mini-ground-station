# Satelity — cheat-sheet pro capture

Rychlý referenční list ke každému typu družice: kam ladit, jak nastavit hardware,
čím dekódovat a na co si dát pozor. Vše ověřené na této stanici (Brno, RTL2838U +
R820T, LNA, filtr FBP-137s). Podrobný běh bez dozoru viz `bezobsluzny-provoz.md`.

## Přehledová tabulka

| | Meteor (LRPT) | Orbcomm | ISS APRS | KOSTKA |
|---|---|---|---|---|
| **frekvence** | 137.9 MHz | 137.25–137.8 (dle sat.) | **437.825 MHz** ⚠ | **436.870 MHz** |
| **dongle laděn na** | 137.9 | **137.5** (parkovaná) | 437.825 | **436.820** (parkovaná) |
| **vzorkování** | 1 Msps | 1.2288 Msps | 1.024 Msps (široko) | 250 ksps |
| **filtr FBP-137s** | **v cestě** | v cestě | **VENKU** | **VENKU** |
| **zisk (SDR_GAIN)** | 20.7 | 20.7 | 15.7 | 15.7 |
| **bias tee** | ON | ON | ON | ON |
| **anténa** | 137 V-dipól ~52 cm | 137 V-dipól ~52 cm | **70cm dipól ~13–16 cm** | **70cm dipól ~16 cm** |
| **dekodér** | SatDump | vendored file_decoder | direwolf `atest` | **gr-satellites** |
| **výstup** | snímek (PNG) | telemetrie + efemeridy | APRS pakety (text) | AX.25 rámce |
| **úspěch =** | vznikl snímek | PER < 50 % | ≥1 rámec (CRC OK) | ≥1 rámec (CRC OK) |

⚠ ISS APRS bylo historicky 145.825 MHz. 2m rádio na ISS selhalo → přesun na 70cm.
**Vždy ověř aktuální frekvenci a stav** (ariss.org / aprs.fi → RS0ISS).

ISS a KOSTKA mají **stejnou fyzickou konfiguraci stanice** (filtr venku, 70cm
dipól ~16 cm, zisk 15.7) — jedna anténa obslouží obě. Přesto má každá svůj gate
(`ISS_ENABLED` / `KOSTKA_ENABLED`): ISS na ~87° přeletu by KOSTKU (z Brna max
~40°) ve výběru přebila. Zapni obojí najednou jen když o to stojíš.

---

## Meteor-M (LRPT) — snímky

- **137.9 MHz** (137.1 záložní downlink), 1 Msps, filtr v cestě, zisk 20.7, 137 dipól.
- Dekodér: **SatDump** pipeline `meteor_m2-x_lrpt` (72k; 80k varianta přes
  `SATDUMP_METEOR_PIPELINE`).
- **M2-4 je zdravá, M2-3 slabá** (nesprávně rozvinutá anténa → jen fragmenty).
- **Kvalita ∝ výška přeletu.** Nízký přelet = černé řádky. Na plný snímek chceš
  vysoký přelet (60°+) na zdravé M2-4. Zenitový přelet vytáhne obraz i ze slabé M2-3.
- Baseband IQ, ne demodulované audio (FM demod zabíjí OQPSK fázi).

## Orbcomm — telemetrie a efemeridy

- **137.5 MHz parkovaná** (ne vlastní kanál — všechny kanály 137.25–137.6625 se pak
  vejdou do pásma a žádný nesedí na DC špičce), 1.2288 Msps (= 256 × 4800 baud).
- Filtr v cestě, zisk 20.7, 137 dipól. Dekodér: vendored `file_decoder.py`.
- **PER rozhoduje o úspěchu** (práh 50 %, `MAX_ACCEPTABLE_PER`). Normál: 0 % v TCA,
  ~18 % u obzoru. Nad prahem → neúspěch, IQ zůstane.
- Vytáhneš **efemeridy** (poloha družice, kterou sama vysílá). Message pakety jsou
  proprietární/šifrované — čitelné jen typy rámců a efemeridy.
- **FM 36 se nedekóduje** (není ve vendored `sat_db`).
- Nástroj: `tools/ephemeris_map.py` (mapa efemerid), `tools/window_sweep.py`.

## ISS APRS — pakety (volačky, pozice, text)

- **437.825 MHz** (⚠ ne 145.825 — ověř status!), 1.024 Msps (široko = hedge pro
  nejistotu 437.825 vs 437.550, IQ zachytí obojí).
- **Filtr VENKU** (437 je mimo jeho pásmo), zisk **15.7** (bez filtru hrozí
  nevratné přebuzení silným rozhlasovým FM — a zisk po LOS nezměníš), bias tee ON.
- **Anténa: 70cm dipól**, ramena ~16 cm (ideál), min. ~13 cm (ISS je silný), úhel 120°.
- Dekodér: `agent/aprs.py` → FM demod → **direwolf `atest`** (AFSK1200/AX.25).
- **Opt-in přes `ISS_ENABLED=1`** — jinak by vysoký ISS přelet přebil 137 družice.
- Frekvence i vzorkování konfigurovatelné: `ISS_FREQ_HZ`, `ISS_SAMPLE_RATE`.
- Syrové IQ se u ISS zachová i při úspěchu (`archive_raw_iq`) — pro re-dekód.
- **Než uzavřeš „nevysílá": ověř status** na ariss.org / amsat.org (plánované SSTV/
  hlas vypnou APRS) nebo aprs.fi (RS0ISS = živý stav digipeateru).
- MIC-E poloha v beaconu bývá **placeholder (0,0)** — ISS ID beacon nenese živý GPS.
  Skutečnou polohu vezmi z vlastního trackingu (TLE/skyfield).
- Ploché SNR se špičkami je pro **dávkový** APRS normální (ne jako souvislý Meteor).

## KOSTKA — telemetrie z brněnského CubeSatu

- **436.870 MHz**, 9600 bd GFSK / G3RUH / AX.25, 250 ksps. 1U CubeSat VUT v Brně
  (tým YSpace), vypuštěn Falconem 9 na přelomu června a července 2026.
- **Dongle parkuje na 436.820**, ne na družici — DC špička R820T sedí přesně na
  naladěné frekvenci a signál je široký jen ~20 kHz, takže by ji měl uprostřed.
  `agent/kostka.py` si signál z těch 50 kHz stáhne zpět na nulu.
- **Filtr VENKU**, zisk **15.7**, bias tee ON, 70cm dipól ~16 cm (jako ISS).
- Dekodér: **gr-satellites**. **Není to balíček** — není na PyPI, v Homebrew ani
  v condě, instaluje se jen buildem ze zdrojů (postup v CLAUDE.md, ~20 min).
  KOSTKA je moc nová na to, aby byla v jeho databázi → `agent/satyaml/kostka.yml`.
- **DC blocker se musí vypnout** (`--disable_dc_block`). Signál dáváme záměrně na
  nulu, takže by mu výchozí blocker vykousl střed; skutečná DC špička je po
  korekci na −50 kHz. Pro FSK s IQ vstupem naopak `--f_offset` **neexistuje**.
- **Když zdravě vypadající přelet nedekóduje nic, zkus `KOSTKA_DEVIATION_HZ`.**
  Deviace KOSTKY není publikovaná (ANS-186 dává jen baudrate), gr-satellites
  má výchozích 5 kHz, 9k6 G3RUH se běžně vysílá spíš kolem 3 kHz.
- Ověřeno 29. 7. na umělém signálu z direwolfího `gen_packets -B 9600`
  namodulovaném do IQ i s Dopplerem: **34 rámců** proti 37, které dá `atest`
  přímo z audia. Řetězec tedy sedí ještě před prvním ostrým přeletem.
- **Doppler si korigujeme sami**, před dekodérem. Na 436 MHz LEO přelet zamete
  ±10 kHz, což je půlka šířky 9k6 GFSK signálu — na 137 MHz je to ±3 kHz a dá se
  to ignorovat, tady ne. Bere se z TLE + polohy stanice v `meta.json`.
- **Telemetrie, SSDV (obrázky), digipeater i CW maják jedou po jedné frekvenci** —
  liší se až AX.25 rámcem, ne laděním. Nahrává se tedy jedna věc.
- **Syrové IQ se zachová i při úspěchu** (`archive_raw_iq`). Družice je stará dny;
  publikovaná frekvence a modulace jsou zatím tvrzení, ne měření, takže rané
  nahrávky se možná budou dekódovat znovu s jiným nastavením.
- **Sledujeme ji podle SatNOGS `sat_id`, ne podle jména ani NORAD ID.** V TLE
  feedu je (29. 7.) bez jména jako `0 OBJECT BU`, s `norad_cat_id` 98395, zatímco
  v samotných TLE řádcích už je 69935. Obojí se ještě změní; `sat_id` ne. Jméno
  „KOSTKA" jí přiřazujeme sami v `shared/tle.py`.
- Uplink zatím nekoordinovaný — doplnit, až IARU zveřejní koordinační dopis.
- Stav ověřuj na `db.satnogs.org/satellite/PCVZ-1444-3446-1456-5852/`.

---

## Anténa: délka ramene podle pásma

Čtvrtvlna (jedno rameno půlvlnného dipólu), rychlostní činitel ~0,95:

| cíl | frekvence | ideální rameno |
|---|---|---|
| Meteor/Orbcomm | 137–138 MHz | ~52 cm |
| ISS APRS (2m, historicky) | 145.825 MHz | ~49 cm |
| **ISS APRS (dnes)** | **437.825 MHz** | **~16 cm** |
| **KOSTKA** | **436.870 MHz** | **~16 cm** |

Úhel V-dipólu **120°** je stejný pro všechna pásma (řeší charakteristiku, ne ladění).
Kompromisní délka 51 cm rezonuje ~140 MHz (mezi 137 a 145). Prodloužit drátem jde,
ale **spoj musí být pevný** — vratký spoj je horší než kratší rameno.

## Zlatá pravidla (draho zaplacená)

1. **Ověř status cíle, než uzavřeš „nevysílá".** APT je mrtvé od 2025 (17. 7.),
   ISS APRS se přesunul na UHF (24. 7.). Dvakrát stejná chyba.
2. **Netestuj anténu na rozhlasovém FM** (88–108 MHz) — filtr FBP-137s to pásmo
   zahazuje, naměříš „vadnou anténu" na zdravé sestavě. Testuj v pásmu.
3. **Bias tee test dělej v jednom procesu** — `rtl_biast` a `rtl_power` si předávají
   dongle a nastavení se ztratí. Funkční LNA pak vypadá jako bez napájení.
4. **Zisk nastav správně předem** — je zapečený v IQ, po LOS ho nezměníš. Přebuzení
   je nevratné; bez filtru raději níž.
5. **`pyrtlsdr` potřebuje `DYLD_LIBRARY_PATH=/opt/homebrew/lib`** a importuje se líně
   → špatně nastavený agent nastartuje čistě a spadne až při AOS.
6. **Detekce donglu přes `rtl_test`, ne `system_profiler`** (ten dongle nespolehlivě
   zobrazuje).

## Jak spustit

Dvojklikové spouštěče v kořeni repa:
- **Spustit agenta.command** — normální režim (137 družice, filtr v cestě)
- **Spustit agenta pro ISS.command** — ISS režim (437.825, filtr venku, 70cm anténa)
- **Spustit agenta pro KOSTKA.command** — KOSTKA režim (436.870, filtr venku, 70cm anténa)
- **Zastavit agenta.command**

Nebo ručně, spouštěcí řádek s `env` viz `bezobsluzny-provoz.md`.
