# STAV 23. 7. dopoledne (aktualizace pro odpoledne)

**Oprava rekordéru NASAZENA a z poloviny POTVRZENA.** Nové oddělené čtecí
vlákno (`_sdr_chunks` v `agent/recorder.py`) měří **0.00 % ztráty vzorků**
(bylo 13.4 %, mezikrok 0.4 %). Nahrávka je bez děr. Commit na větvi
`unattended-recording-fixes`, testy 140/140.

**PER se ověřit NEPODAŘILO — rozbitá RF cesta.** Ráno se ukázalo, že do donglu
nejde žádný signál: kanál družice je na úrovni šumu i při reálném průletu na
53°, a SNR v čase je plochý (žádný hrb kolem kulminace). Dokázáno na FM 109 a
FM 108. Není to software, oprava vzorků funguje.

Diagnostika: bias tee ON vs OFF = +0.0 dB → LNA nedostává napájení (test ale
může být necitlivý). Časová osa (večer fungovalo → déšť → ráno nic) ukazuje na
**vlhkost**. Uživatel nechává **sušit**, agent zastaven, bias tee vypnut.

**Odpolední plán:**
1. Nechat doschnout, připojit zpět.
2. **Bisekce jedním krokem:** obejít LNA, připojit anténní kabel přímo do
   donglu. Signál na dalším průletu = vada v LNA/napájení; nic = vada v
   anténě/svodu.
3. Spustit agenta (řádek dole), počkat na průlet, ověřit PER proti golden
   benchmarku 7–16 %. Diskriminátor „je tam signál": tvar SNR v čase musí mít
   hrb kolem kulminace, ne úroveň kanálu (kanály u DC jsou znečištěné).

Vše ostatní níže platí.

---

# Ráno 23. 7. — kde jsme skončili

Agent je **zastavený** (déšť, odpojená anténa 23. 7. ~01:40). Než ho znovu
spustíš, přečti si tohle, jinak retence přepíše materiál níže.

## Co je hotové a ověřené

Za noc padly čtyři reálné chyby, všechny opravené a v testech (140/140 prošlo),
commit na větvi `unattended-recording-fixes` (pushnuto, PR nezaložen):

1. Rekordér ztrácel 13 % vzorků (blokující `post_live`) → async, teď 0,4 %.
2. Špatný dekód se tvářil jako úspěch a retence mazala IQ → PER teď rozhoduje.
3. Rozdělané pasy mizely při přepočtu cache → lookback okno. Ověřeno: Meteor
   se ve 22:55 chytil místo aby zmizel.
4. Výběr podle AOS zastiňoval lepší pasy → výběr podle elevace + bonus Meteor.

## Co zbývá vyřešit: PER ~75 %

**Příčina je jistá.** A/B test na FM 118 ve 23:46, stejná družice, stejná
minuta, stejný hardware:

| kdo nahrával | PER |
|---|---|
| upstream `record_orbcomm.py` | 7,0 / 15,3 / 16,3 % |
| náš `recorder.py` | 99,0 % |

Vada je v `agent/recorder.py`, ne v podmínkách, dekodéru ani metadatech (vše
ostatní vyloučeno měřením — viz `docs/orbcomm-per-analyza.md`).

**Nenasazená oprava** (čekala na tvé rozhodnutí): rekordér čte 0,5s bloky v
cyklu, upstream čte 2 s jedním voláním. Mezi bloky vzniká mezera, do které
librtlsdr zahazuje vzorky, což láme symbolové časování. Dvě cesty:

1. `sdr.read_bytes_async()` — drží USB frontu plnou, standardní řešení. (lepší)
2. Zvětšit `CHUNK_SECONDS` (agent/recorder.py) z 0,5 na 4+ s. (jednořádkové)

**Měřítko úspěchu:** PER pod 20 % na pasu, kde upstream dává 7–16 %.

## Materiál k analýze (NEMAZAT, nespouštět agenta dokud nevyužiješ)

- `recordings/ORBCOMM_FM_118_20260722_232049` — náš plný pas FM 118,
  souvislý (po opravě rekordéru), PER ~76 %. Stejná družice jako golden níže.
- `orbcomm-receiver/data/*.mat` — 100 golden souborů z upstreamu (23:36),
  FM 118, PER 7–16 %. Přímé srovnání „jak má vypadat dobrý vzorek".
- `docs/orbcomm-per-analyza.md` — celý postup, tabulky vyloučených příčin.
- `tools/window_sweep.py`, `tools/upstream_ab_test.sh` — nástroje na opakování.

## Spuštění agenta zpět (až budeš chtít)

Anténu připoj, pak (spouštěcí řádek viz `docs/bezobsluzny-provoz.md`):

```bash
cd ~/Documents/satelite_tracker
nohup env PYTHONUNBUFFERED=1 DYLD_LIBRARY_PATH=/opt/homebrew/lib \
  RECORDINGS_MAX_GB=10 MPLBACKEND=Agg \
  PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  .venv/bin/python -u -m agent.scheduler >> logs/agent.log 2>&1 & disown
nohup caffeinate -i -s -w $(pgrep -f agent.scheduler | head -1) >/dev/null 2>&1 & disown
```
