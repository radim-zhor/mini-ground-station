# Bezobslužný provoz stanice

Jak nechat agenta nahrávat přes noc, aniž bys u toho byl, a co všechno se
při tom může tiše rozbít. Všechno níže je ověřené měřením na téhle stanici
22. 7. 2026, ne odhad.

## Spuštění

```bash
cd ~/Documents/satelite_tracker
nohup env \
  PYTHONUNBUFFERED=1 \
  DYLD_LIBRARY_PATH=/opt/homebrew/lib \
  PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  RECORDINGS_MAX_GB=10 \
  MPLBACKEND=Agg \
  .venv/bin/python -u -m agent.scheduler >> logs/agent.log 2>&1 &
disown
```

Pak pojistka proti uspání, navázaná na život agenta (skončí spolu s ním):

```bash
nohup caffeinate -i -s -w $(pgrep -f agent.scheduler | head -1) >/dev/null 2>&1 &
disown
```

Kontrola, že to opravdu běží odpojeně:

```bash
ps -o pid,ppid,etime -p $(pgrep -f agent.scheduler | head -1)
```

**PPID musí být 1.** Když je to cokoliv jiného, proces visí na shellu, který ho
zabije, až se zavře. To je přesně důvod, proč agent spuštěný z Claude Code
session zemře s ní.

Zastavení:

```bash
pkill -f "caffeinate -i -s -w"; pkill -f agent.scheduler
```

## Proč každá proměnná

| Proměnná | Co se stane bez ní |
|---|---|
| `DYLD_LIBRARY_PATH=/opt/homebrew/lib` | `pyrtlsdr` nenajde `librtlsdr.dylib`. ctypes prohledává `/usr/lib` a `/usr/local/lib`, ale ne Homebrew na Apple Siliconu. **A import je v `recorder.py:166` líný**, takže agent v pohodě nastartuje a spadne až při AOS, kdy je na záchranu pozdě. |
| `PATH` s `/opt/homebrew/bin` | `decoder.py` nenajde `satdump`, každý Meteor dekód selže, a protože se IQ maže jen po **úspěšném** dekódu, zůstanou na disku 3 GB za průlet. |
| `PYTHONUNBUFFERED=1` | Log zůstane prázdný a nepoznáš, jestli agent žije. |
| `MPLBACKEND=Agg` | Vendorovaný `file_decoder.py` končí `plt.show()` a zablokoval by se. |
| `RECORDINGS_MAX_GB` | Default je 20 GB. Kontroluj volné místo, viz níže. |

## Studený start trvá 40 sekund

Import skyfield a numpy zabere při prvním spuštění zhruba **40 s**, za tepla
asi 13 s. Do té doby je log prázdný a proces má 0 % CPU, což vypadá k
nerozeznání od zaseknutého procesu. **Nezabíjej ho dřív než po minutě.**
Když opravdu potřebuješ vědět, kde je, vezmi stack:

```bash
sample $(pgrep -f agent.scheduler | head -1) 3 -f /tmp/s.txt && head -40 /tmp/s.txt
```

## launchd zatím nefunguje (TCC)

Plist je hotový v `~/Library/LaunchAgents/com.radimzhor.groundstation.plist`,
ale spuštěná služba padá:

```
PermissionError: [Errno 1] Operation not permitted:
  '.../satelite_tracker/.venv/pyvenv.cfg'
```

Repo leží v `~/Documents`, což je pod ochranou macOS TCC. Terminál a Claude Code
tam přístup mají, proces spuštěný launchd ne. Dvě cesty, obě vyžadují rozhodnutí:

1. **Přesunout repo mimo `~/Documents`** (třeba do `~/groundstation`). TCC pak
   nezasahuje, launchd naběhne, dostaneš `KeepAlive` (restart po pádu) a
   `RunAtLoad` (start po přihlášení). Nutno upravit cestu v plistu.
2. **Udělit Full Disk Access** binárce `.venv/bin/python3` v Nastavení systému.
   Repo zůstane, ale je to ruční krok a po přeinstalaci venv se to rozbije.

Do té doby platí `nohup` výše. Ten přežije konec session, ale **nepřežije
restart ani odhlášení** a nemá automatický restart po pádu.

## Disk

Za 24 hodin je nad 10° zhruba **62 nahratelných průletů**, což by při plném
nahrávání dalo **152 GB IQ**. Retence to drží dole, ale má dvě slabiny:

- IQ se maže jen po **úspěšném** dekódu. Série neúspěchů disk zaplní.
- `has_room_for` v `retention.py:129` porovnává proti stropu `RECORDINGS_MAX_GB`,
  **ne proti skutečnému volnému místu na disku**, a jeho návratová hodnota se v
  `scheduler.py:304` zahazuje. Když disk zaplní něco mimo `recordings/`, agent
  stejně začne nahrávat a ztratí pas uprostřed.

Před odchodem od počítače se vyplatí zkontrolovat:

```bash
df -h ~ | tail -1; du -sh ~/Documents/satelite_tracker/recordings/
```

Drž `RECORDINGS_MAX_GB` aspoň o 4 GB pod volným místem, ať se vejde rozdělaný
průlet (~3 GB).

## ISS APRS (145.825 MHz) — opt-in přes ISS_ENABLED

ISS vysílá APRS digipeater na 145.825 MHz, což je **mimo pásmo filtru
FBP-137s**. Zachytit ho jde jen když filtr fyzicky přemostíš (LNA nech, je
širokopásmová 50 MHz–4 GHz, bias tee zapnutý). Postup pro konkrétní ISS průlet:

1. Přemosti filtr: `anténa → LNA → dongle`, bez FBP-137s v cestě.
2. Spusť agenta se zapnutým ISS: přidej `ISS_ENABLED=1` do spouštěcího `env`.
3. Po ISS průletu filtr vrať a agenta spusť bez `ISS_ENABLED`.

**Proč je to opt-in.** Bez `ISS_ENABLED` (default) agent ISS ignoruje. Kdyby byl
pořád zapnutý, vysoký ISS průlet (dosahují ~87°) by ve výběru podle hodnoty
přebil každý 137MHz satelit, zabral dongle a nahrával šum přes filtr. Proto se
ISS zapíná jen na session, kdy je filtr venku.

Dekódování dělá `agent/aprs.py`: baseband IQ → FM demodulace → `atest`
(z **direwolf**, `brew install direwolf`) → AX.25/APRS rámce. Volačky, pozice a
komentáře jsou v čistém textu, na rozdíl od šifrovaného Orbcommu. Úspěch je
libovolný platný rámec (žádný PER — rámec buď prošel CRC, nebo se neobjevil).

Jména ISS jsou ze SatNOGS nestabilní („ISS" živě, „ISS (ZARYA)" ve fixture),
takže všechno kolem ISS klíčuje na prefix `is_iss()`, ne na přesné jméno.

## Anténa: jak ji NEtestovat

Stanice má v cestě **FBP-137s**, pásmovou propust na 137 MHz, a **LNA napájenou
přes bias tee**.

- **Neměř průchodnost na rozhlasovém FM (88 až 108 MHz).** Ten filtr tohle pásmo
  má potlačovat. Naměříš 10 až 15 dB místo očekávaných 40 a budeš si myslet, že
  je anténa vadná. Není.
- **Vždy zapni bias tee.** Bez napájení sedí LNA v cestě jako pasivní útlum.
  Agent to řeší sám (`SDR_BIAS_TEE` default `1`), ale ruční `rtl_power` a
  `rtl_fm` ne, tam si to zapni: `rtl_biast -b 1`.
- Ruční měření dělej **v pásmu** a se **ziskem 20.7 dB**, což je hodnota ověřená
  proti Meteoru a Orbcommu na téhle sestavě. `-g 40` přebuzuje.

## Známé chyby scheduleru

**Výběr podle AOS, ne podle elevace.** `_next_upcoming_pass()` bere průlet s
nejbližším AOS. Slabý průlet, který začne dřív, drží dongle a zastíní lepší,
který začne o pár minut později. Změřeno na dnešní noc: Meteor M2-3 na **78.3°**
(jediný obrázkový průlet) dostane jen **69.8 % pokrytí**, protože ho Orbcomm
FM 110 na 32.2° drží do 21:12.

**Restart uprostřed průletu ten průlet zahodí.** `predict_passes()` staví na
`find_events()`, které potřebuje celou trojici AOS, kulminace, LOS uvnitř okna
začínajícího *teď*. Pas, který už běží, tedy po restartu z cache vypadne.
Docstring u `_next_upcoming_pass` slibuje částečné nahrání, což platí jen když
cache vznikla před začátkem pasu. **Nerestartuj agenta, když něco běží.**

**ISS má neshodu jmen.** SatNOGS vrací `"ISS"`, `FREQUENCIES` má klíč
`"ISS (ZARYA)"`, takže se ISS vždy přeskočí. Do 22. 7. to navíc blokovalo
všechny překrývající se průlety, než se filtrace přesunula do
`_next_upcoming_pass()`. Až budeš chtít ISS doopravdy, musíš spravit obojí:
jméno **a** přemostit FBP-137s, protože 145.825 MHz je mimo jeho pásmo.

## PER rozhoduje o úspěchu, jinak přijdeš o důkazy

Do 22. 7. vracel `_decode_orbcomm` `success=True`, kdykoliv dekodér nespadl a
vyprodukoval aspoň jeden paket. **PER se parsoval, zalogoval a zahodil.**
Důsledek na prvním nočním průletu:

```
19:02:03  Decode: orbcomm: 99 packets, PER 99.0%
19:02:03  Retention: removed ...cs16 (2.94 GB, decoded)
```

Nahrávka byla dobrá (avg SNR 17.8 dB, peak 43.0 dB), dekód byl odpad, a protože
se tvářil jako úspěch, retence smazala baseband. Selhání se tím stalo
nediagnostikovatelným a neopakovatelným.

Teď to hlídá `MAX_ACCEPTABLE_PER` v `agent/decoder.py` (default 50 %, laditelné
stejnojmennou proměnnou). Nad prahem je dekód `success=False`, takže IQ zůstane
k ručnímu opakování. Normální rozsah na téhle stanici je 0 % v TCA a ~18 % u
obzoru, takže práh 50 % chytá jen skutečný odpad.

### Vyšetřování vysokého PER (22. 7., neuzavřeno)

Dva po sobě jdoucí pasy skončily na PER 99 % a 100 % při zdravém příjmu
(avg SNR 17.8 a 18.5 dB). Na druhém zůstalo IQ, takže se dalo měřit.
`tools/window_sweep.py` dekóduje jeden pas z několika oken a porovná PER.

**Co je vyloučené:**

| Hypotéza | Test | Výsledek |
|---|---|---|
| Špatný výběr okna (maximum SNR padne na rušivý záblesk) | `window_sweep.py`, 5 oken | Vliv **má** (100 % → 52.5 %), ale ani nejlepší okno není pod prahem. Není to celá příčina. |
| Rozbitý dekodér | Referenční `.mat` z 22. 7. 00:47 | Dekóduje **čistě**, žádný `###`. Dekodér je v pořádku. |
| Špatný Doppler, pozice, čas nebo TLE | Log dekodéru | Reziduum **-5 Hz**, lepší než u reference (86.5 Hz). V pořádku. |
| Chybějící `alt` v `meta.json` | Reference má 230, my posíláme 0 | 230 m Doppler neovlivní, viz reziduum výše. |
| Normalizace amplitudy (`/32768` v `_write_mat`) | Dekódováno se škálou 1× a 193× | PER **identický** do desetiny. Dekodér je scale-invariant. |
| DC offset z centrování (`- 127` u nás vs `- 128` upstream) | Změřen komplexní průměr obou sad | Náš je **čistší**: \|DC\|/rms 10.9 % proti 24.4 % u reference. |

### Nalezeno a opraveno: rekordér zahazoval 13 % vzorků

Porovnání nástěnného času s čítačem vzorků v průběžných hláškách ukázalo, že
rekordér nasbírá 570 s vzorků za 658 s reálného času. Potvrzeno na třech pasech
nezávisle: **13.2 %, 13.4 %, 13.6 %**.

Příčina: `_progress_reporter` volal `post_live()` **synchronně** v nahrávací
smyčce. `LIVE_INTERVAL_S = 2`, latence POSTu na Render 190 až 410 ms, tedy
260/2000 = **13.0 %** času, kdy nikdo nečte z USB a librtlsdr tiše zahazuje.
Predikce a měření se shodly na desetinu procenta.

Nešlo o zpracování: konverze, `tobytes`, zápis na disk a `_snr_of_iq` dohromady
zaberou **2.6 ms z 500 ms**. Ani o synchronní čtení: `read_bytes` v těsném cyklu
bez zpracování má na tomto donglu ztrátu 0.0 %.

Oprava je `_post_live_async()` v `scheduler.py`: POST jde do daemon vlákna a
pojistka `_live_inflight` zahodí tik, pokud předchozí ještě letí, takže se
vlákna nehromadí a pomalá appka stojí nanejvýš čerstvost konzole. Měřeno:
nejhorší callback 260 ms → **0.34 ms**, blokování ve smyčce 13 % → **0.01 %**,
ztráta vzorků na dalším pasu **1.1 %** a deficit dál neroste (je to jednorázový
posun ze startu, ne únik).

### Co je stále neobjasněné

Po opravě klesl PER ze 100 % na **76.8 %**, ale to je pořád daleko od normálu
(0 % v TCA, ~18 % u obzoru). **Díry v nahrávce tedy byly jednou příčinou, ne
jedinou.**

Změnil se ale charakter: na děravé nahrávce kolísal PER mezi oknu 52.5 a 100 %,
na souvislé je **75.8 až 77.8 % napříč všemi okny**. Rozptyl zmizel, což
ukazuje na systematickou příčinu, ne na náhodnou degradaci.

Vyloučeno navíc:

| Hypotéza | Test | Výsledek |
|---|---|---|
| Zpracování v cyklu zdržuje | Benchmark částí | 2.6 ms z 500 ms, 0.5 %. |
| Synchronní `read_bytes` ztrácí | 8 s čtení bez zpracování | Ztráta 0.0 %. |
| Špatná vzorkovací frekvence | Poloha nosné v FFT | Kanál B na +301.7 kHz proti +300.0, rozdíl je Doppler. Frekvence sedí. |
| Kolize na sdíleném kanálu | Elevace všech družic na 137.6625 v čase pasu | FM 112 byl jediný nad obzorem, ostatní -55° a -58°. |

**PER je bimodální.** Pět pasů nahraných po opravě rekordéru (všechny souvislé,
ztráta vzorků 0.4 %):

| pas | max el | PER |
|---|---|---|
| FM 113 | 10.8° | 100.0 % |
| FM 112 | 25.3° | 76.8 % |
| FM 110 | 32.2° | 75.3 % |
| FM 114 | 37.1° | 100.0 % |
| FM 118 | 48.7° | 75.8 % |

Hodnoty padají do dvou shluků, 75.3 až 76.8 % nebo rovných 100 %, nikdy mezi
tím a nikdy níž. **S elevací to nekoreluje** (37.1° dalo 100 %, 25.3° dalo
76.8 %), takže to není prosté slábnutí signálu. Dva režimy místo spojité
degradace ukazují spíš na něco diskrétního: buď se demodulátor chytí, nebo ne.

**Kde pokračovat:** souvislé nahrávky se zachovaným IQ jsou v `recordings/`.
Referenční soubory z funkčního běhu jsou v `orbcomm-receiver/data/` (~100 kusů
z 22. 7. 00:39 až 00:47), **nemazat**, jsou to jediné známé dobré vzorky.
Nejpřímější další krok je porovnat demodulační mezistupně: pustit
`file_decoder.py` na referenci a na naší souvislé nahrávce a podívat se, kde se
rozcházejí (souhvězdí, symbolové časování, offset bitového proudu). Náš
`Bit stream offset` byl 79, referenční 18.

## Uspávání

Uložená konfigurace pro AC má `sleep 0`, takže na napájení Mac neusne. Na
baterii by usnul, proto ten `caffeinate -i -s -w`. Ověření:

```bash
pmset -g assertions | grep -i caffeinate
```

## Co zkontrolovat, než odejdeš

```bash
pgrep -f agent.scheduler >/dev/null && echo "agent bezi" || echo "AGENT NEBEZI"
ps -o ppid= -p $(pgrep -f agent.scheduler | head -1)     # musi byt 1
pgrep -f "caffeinate -i -s -w" >/dev/null && echo "caffeinate ok"
df -h ~ | tail -1
tail -3 ~/Documents/satelite_tracker/logs/agent.log
```

Průběh pak sleduj z mobilu na <https://mini-ground-station.onrender.com/pass>.
Agent posílá heartbeat, takže „Agent offline" na té stránce znamená, že spadl.
