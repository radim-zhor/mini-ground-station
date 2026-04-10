# SDR++ nastavení — audit pro příjem NOAA APT

Audit na základě screenshotu ze dne 2026-04-08.

---

## Source

| Pole | Aktuální hodnota | Správně? | Doporučení |
|---|---|---|---|
| Source | RTL-SDR | ✅ | — |
| Device | Realtek RTL2838UHIDIR [00000000] | ✅ | — |
| Sample rate | 2.4 MHz | ✅ | 2.4 MHz je standardní, dostačující |
| Direct Sampling | Disabled | ✅ | Pro 137 MHz správně — Direct Sampling jen pro HF pod 30 MHz |
| PPM Correction | 0 | ⚠️ | RTL2838 bývá o ±25–75 ppm. Pokud je signál posunutý, uprav. Zatím OK |
| Gain | 22.9 dB | ⚠️ | Pro slabý signál zkus **35–40 dB**. 22.9 může být příliš nízký |
| Bias T | vypnuto | ✅ | Správně — Bias T je pro LNA napájené přes koax, bez LNA vypnuto |
| Offset Tuning | vypnuto | ✅ | — |
| RTL AGC | vypnuto | ✅ | Manuální gain je lepší než AGC pro satelity |
| Tuner AGC | vypnuto | ✅ | — |
| IQ Correction | vypnuto | ⚠️ | Lze zapnout — pomáhá s DC spike uprostřed spektra |
| Invert IQ | vypnuto | ✅ | — |
| Offset mode | None | ✅ | — |
| Decimation | None | ✅ | — |

---

## Radio

| Pole | Aktuální hodnota | Správně? | Doporučení |
|---|---|---|---|
| Modulace | WFM | ✅ | Správně pro APT |
| Bandwidth | 50000 | ✅ | Minimum v SDR++, pro APT dostačující |
| Snap Interval | 100000 | ✅ | — |
| De-emphasis | 22us | ❌ | **Změň na 50us** — 22us je pro USA/Japonsko, 50us pro Evropu. Ovlivňuje zvuk, ne signál, ale pro správné audio chování 50us |
| Squelch Mode | Off | ✅ | Správně — Squelch by mohl odříznout slabý signál |
| IF Noise Reduction | vypnuto | ✅ | Nezapínat — může deformovat APT audio |
| High Pass | **zapnuto** | ❌ | **Vypni** — High Pass filtr ořezává nízké frekvence. APT sync tóny jsou na 1040 Hz a 832 Hz, High Pass může tyto frekvence tlumit |
| Low Pass | vypnuto | ✅ | — |
| Stereo | vypnuto | ✅ | Správně — noaa-apt potřebuje mono |
| Decode RDS | vypnuto | ✅ | — |

---

## Recorder

| Pole | Aktuální hodnota | Správně? | Doporučení |
|---|---|---|---|
| Typ | Audio | ✅ | Správně (ne Baseband) |
| Výstupní složka | /Users/radimzhor/Documents/satelite_tracker/recordings | ✅ | — |
| Name template | $t_$f_$h-$m-$s_$d-$M-$y | ✅ | — |
| Container | WAV | ✅ | — |
| Sample type | Int16 | ✅ | — |
| Stream | Radio | ✅ | Správně |

---

## Shrnutí problémů

| Priorita | Pole | Problém | Oprava |
|---|---|---|---|
| 🔴 Vysoká | High Pass | Zapnutý — ořezává APT sync frekvence | **Vypnout** |
| 🟡 Střední | Gain | 22.9 dB může být příliš nízký | Zkus **35–40 dB** |
| 🟡 Střední | De-emphasis | 22us místo 50us | Změnit na **50us** |
| 🟢 Nízká | IQ Correction | Může pomoci s DC spike | Zapnout volitelně |

---

**Nejdůležitější oprava: vypnout High Pass filtr.** Tento filtr je nejvíc podezřelý ze všech nastavení — APT signál obsahuje důležité nízké frekvence které High Pass může blokovat.
