# Nahrávání NOAA APT signálu v SDR++

## Co budeš potřebovat

- RTL-SDR dongle zapojen přes USB
- Anténa s výhledem ven (ideálně dipól nebo V-dipól venku, nebo u okna)
- SDR++ nainstalovaný
- `noaa-apt` CLI dekodér ([stáhnout](https://github.com/martinber/noaa-apt/releases))

---

## Krok 1 — Zjisti čas přeletu

Otevři `http://localhost:8000/passes` a najdi nejbližší přelet NOAA satelitu.

Zapamatuj si:
- **čas AOS** (začátek přeletu) — kdy začít nahrávat
- **čas LOS** (konec přeletu) — kdy zastavit
- **max. elevace** — čím výš, tím silnější signál (přelety pod 20° bývají slabé)

---

## Krok 2 — Nastav SDR++

| Parametr | Hodnota |
|---|---|
| Frekvence | viz tabulka níže |
| Modulace | **WFM** |
| Bandwidth | **50 000 Hz** (minimum v SDR++) |
| Gain | **49.6 dB** (nebo RTL AGC) |

### Frekvence NOAA satelitů (APT)

| Satelit | Frekvence |
|---|---|
| NOAA 15 | 137.620 MHz |
| NOAA 18 | 137.9125 MHz |
| NOAA 19 | 137.1000 MHz |

---

## Krok 3 — Nastav Recorder

V levém panelu rozbal sekci **Recorder**:

| Nastavení | Hodnota |
|---|---|
| Typ | **Audio** (ne Baseband) |
| Container | **WAV** |
| Sample type | **Int16** |
| Stream | **Radio** |
| Stereo | **vypnuto** ← důležité, noaa-apt potřebuje mono |
| Výstupní složka | `/recordings/` (nebo libovolná) |

---

## Krok 4 — Nahraj přelet

1. Pár minut před AOS spusť SDR++ a nastav frekvenci
2. V čase AOS klikni **Record**
3. Čekej — APT zvuk zní jako "bzzzzt bzzzzt" (2 tóny střídající se ~2×/s)
4. V čase LOS klikni **Stop**

Signál sílí jak satelit stoupá k max. elevaci a slábne při sestupu — to je normální.

Pokud neslyšíš nic než šum: zkontroluj frekvenci a anténu (výhled na oblohu).

---

## Krok 5 — Dekóduj snímek

```bash
noaa-apt /cesta/k/recording.wav -o obrazek.png
```

Výsledkem je satelitní snímek počasí — dva pásy vedle sebe:

```
[ viditelné světlo ] [ infračervené ]
```

Kvalita závisí na síle signálu. Přelety s max. elevací nad 30° dávají čitelné snímky.

---

## Zkratky

| Zkratka | Význam |
|---|---|
| AOS | Acquisition of Signal — začátek přeletu (satelit se vynoří nad obzorem) |
| LOS | Loss of Signal — konec přeletu (satelit zmizí za obzorem) |
| APT | Automatic Picture Transmission — analogový obrazový protokol NOAA satelitů |
| WFM | Wide FM — modulace používaná APT signálem |
| El | Elevace — úhel satelitu nad obzorem (0° = horizont, 90° = zenit) |
| Az | Azimut — směr satelitu (0° = sever, 90° = východ, 180° = jih, 270° = západ) |
