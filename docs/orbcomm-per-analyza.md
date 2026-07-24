# Proč Orbcomm nedekóduje: co víme a co dál

Stav k 22. 7. 2026, 22:40. Devět nahraných pasů, žádný použitelně dekódovaný.
PER se drží mezi 75 a 100 %, přičemž normál na této stanici je 0 % v TCA a
~18 % u obzoru.

## Rozhodující zjištění: chyba je ve vzorcích, ne v obalu

Vzal jsem vzorky z **funkční reference** (`orbcomm-receiver/data/1784674067p684.mat`,
FM 118, 22. 7. 00:47, dekóduje na PER 1.0 %) a obalil je postupně **naší**
metadatovou cestou:

| co jsem vyměnil | PER |
|---|---|
| nic, původní metadata reference | 1.0 % |
| naše TLE ze SatNOGS cache | 1.0 % |
| `alt = 0` jako v našem `meta.json` | 1.0 % |
| naše pojmenování družice (`sat_db_name`) | 1.0 % |
| všechno naše najednou | 1.0 % |

**Náš `_write_mat`, naše TLE, chybějící `alt` i pojmenování jsou nevinné.**
Stejné vzorky dekódují čistě bez ohledu na obal. Vada je tedy v tom, co
`recorder.py` zapíše do `.cs16`.

To zužuje hledání z celé cesty jen na záznam.

## Co je o našich vzorcích změřeno

| vlastnost | hodnota | zdroj |
|---|---|---|
| SNR na kanálu družice | +16.7 dB nad šumem (20 kHz) | FFT nahrávky |
| sousední kanály | +0.6 a +1.9 dB, tedy prázdné | FFT nahrávky |
| DC špička uprostřed | +9.0 dB, na prázdném místě | FFT nahrávky |
| zbytkový Doppler po korekci | −5 Hz (reference má +86.5 Hz) | log dekodéru |
| ztráta vzorků | 0.4 % po opravě (13.4 % před ní) | čítač vs nástěnný čas |
| amplituda | irelevantní, dekodér je scale-invariant | A/B se škálou 193× |
| DC offset | 10.9 % rms, reference má 24.4 % | komplexní průměr |

Signál je tedy silný, čistý, na správné frekvenci a bez rušení od sousedů.
Přesto padají skoro všechny kontrolní součty.

## Hypotézy, seřazené podle pravděpodobnosti

### H1. Souvislý stream ztrácí vzorky, které burst neztratí

Upstream `record_orbcomm.py` čte celé dvě sekundy **jedním** voláním
`read_bytes()` a nic mezi tím nedělá. Náš rekordér čte 0.5s bloky deset minut
v kuse a mezi nimi zapisuje na disk a počítá SNR. Zbylá ztráta 0.4 % znamená
při 1.2288 Msps asi 8 ms chybějících vzorků na každé dvousekundové okno, což
symbolové časování rozbije, i když spektrum vypadá bezvadně.

**Pro:** vysvětluje všechna pozorování naráz. Sedí na to, že upstream funguje
a my ne, při shodném hardwaru, zisku i bias tee.
**Proti:** neověřeno. Můj pokus hledat fázové skoky na hranicích bloků byl
špatně navržený, protože SDPSK fázi moduluje a skoky jsou data.

### H2. Stav tuneru se během dlouhého záznamu mění

Deset minut souvislého čtení je něco jiného než dvousekundová dávka: PLL se
může přelaďovat, tuner zahřívat, hodiny driftovat. Upstream tomu nikdy není
vystaven.

**Pro:** stejný mechanismus rozdílu burst vs stream jako H1.
**Proti:** neověřeno; ztráta vzorků je měřitelná, drift zatím ne.

### H3. Efektivní vzorkovací frekvence se liší od 1.2288 Msps

Dekodér má 1.2288 Msps zadrátované jako 256 × 4800 baud. I malá odchylka
rozjede časování symbolů přes dvousekundové okno.

**Pro:** vysvětlovalo by bimodalitu (buď se chytí, nebo ne).
**Proti:** dongle na dotaz hlásí přesně 1228800 Hz a poloha kanálu B seděla na
1.7 kHz, což je Doppler. Měření rozestupu kanálů jako pravítka **selhalo** —
dalo nesmysl i pro funkční referenci (28 000 ppm), protože hledání druhého
kanálu chytá cizí signály. Tuhle hypotézu tedy nemám ani potvrzenou, ani
vyvrácenou.

### H4. Něco jiného v `_sdr_chunks`

Konverze `(raw - 127) << 7`, pořadí I/Q, zarovnání na násobky 512. Nic z toho
nevypadá podezřele a škálování je prokazatelně jedno, ale je to poslední místo
mezi donglem a souborem, kam jsem se nedíval do detailu.

## ROZHODNUTO 22. 7. 23:47: vada je v `agent/recorder.py`

A/B test proběhl na FM 118. Agent byl na deset minut zastaven, pas nahrál
nezměněný `orbcomm-receiver/record_orbcomm.py`, pak se agent vrátil a stihl
posledních 41 s téhož pasu. Stejná družice, stejná minuta, stejný hardware,
stejná anténa:

| kdo nahrával | čas | PER |
|---|---|---|
| upstream `record_orbcomm.py` | 23:46:32 | **7.0 %** |
| upstream `record_orbcomm.py` | 23:46:37 | **15.3 %** |
| upstream `record_orbcomm.py` | 23:46:42 | **16.3 %** |
| náš `recorder.py` | 23:46:52 až 23:47:33 | **99.0 %** |

Rozdíl mezi 7 % a 99 % nelze vysvětlit podmínkami, protože jsou oddělené
sekundami. **Náš záznamový kód produkuje vzorky, ze kterých se dekódovat nedá.**

Pozoruhodné je, že upstream má zbytkový Doppler 87 až 89 Hz, zatímco my −5 Hz.
Naše frekvenční korekce je tedy lepší a přesto dekód selhává, což potvrzuje, že
problém je v časové ose vzorků, ne ve frekvenci.

### Co s tím dál

Jediný podstatný rozdíl v tom, jak se čte z donglu:

- upstream: **jedno** volání `read_bytes()` na celé dvě sekundy, mezi voláními
  nic jiného neběží
- my: `read_bytes()` po 0,5 s v cyklu, deset minut v kuse, mezi voláními zápis
  na disk a výpočet SNR

Dvě cesty, obě se dají zkusit na jednom pasu:

1. **Async čtení.** `sdr.read_bytes_async()` drží USB přenosy zafrontované
   nepřetržitě, takže mezi bloky nevzniká mezera. Tohle je standardní řešení
   ztrátového `rtlsdr_read_sync` při vyšších vzorkovacích frekvencích.
2. **Mnohem větší bloky.** Zvýšit `CHUNK_SECONDS` na 4 s a víc, aby se
   dvousekundové okno dekodéru vešlo dovnitř jednoho čtení. Levnější zásah,
   ale řeší to jen následek.

Měřítko úspěchu je jasné: PER pod 20 % na pasu, kde upstream dává 7 až 16 %.

## Původní rozhodující experiment (zadání)

**Nahrát jeden pas přímo upstream rekordérem** (`orbcomm-receiver/record_orbcomm.py`,
beze změny) na stejném hardwaru, stejný večer, a jeho `.mat` dekódovat.

- Dekóduje-li čistě, je rozdíl prokazatelně v našem `recorder.py` a platí H1,
  H2 nebo H4. Další krok je pak zkusit náš rekordér s mnohem většími bloky,
  ať se dvousekundové okno vejde do jednoho čtení.
- Nedekóduje-li ani upstream, není to naším kódem vůbec a příčina je v
  podmínkách příjmu nebo ve stavu stanice, který se od rána změnil.

Stojí to jeden průlet, protože dongle je jen jeden. Nejlepší kandidáti dnes v
noci jsou **FM 114 ve 23:04 (67.4°)** a **FM 118 ve 23:37 (70.1°)**; ten druhý
je stejná družice jako funkční reference, takže srovnání je nejčistší.

## Co si nechat

- `recordings/` drží poslední pasy s IQ, protože PER nad 50 % je teď neúspěch.
- `orbcomm-receiver/data/` má ~100 referenčních souborů z 22. 7. 00:39 až 00:47.
  **Nemazat**, jsou to jediné známé dobré vzorky.
- `tools/window_sweep.py` dekóduje pas z několika oken a porovná PER.
