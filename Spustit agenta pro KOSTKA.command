#!/bin/bash
# Dvojklik = spustí agenta s NAHRÁVÁNÍM družice KOSTKA na 436.870 MHz (UHF/70cm).
# KOSTKA = 1U CubeSat VUT v Brně (tým YSpace), 9k6 GFSK G3RUH AX.25.
# POZOR:
#  • Přemosti filtr FBP-137s (anténa → LNA → dongle, filtr ven) — 436 je mimo něj.
#  • Anténa: 70cm dipól, ramena ~16 cm, úhel 120° (stejná jako pro ISS).
#  • LNA a bias tee nech.
cd "$(dirname "$0")" || exit 1

echo "=== Spouštím agenta v režimu KOSTKA (436.870 MHz UHF) ==="
echo "!!! Filtr přemostěný? Anténa přeladěná na 70cm (~16 cm ramena)?"
echo

if pgrep -f "agent.scheduler" >/dev/null; then
    echo "Agent už běží (PID $(pgrep -f agent.scheduler | head -1))."
    echo "Nejdřív ho zastav ('Zastavit agenta'), pak spusť tenhle."
    echo; read -r -p "Zavři okno (Enter)…" _; exit 0
fi

if ! DYLD_LIBRARY_PATH=/opt/homebrew/lib rtl_test -t 2>&1 | grep -q "Found 1 device"; then
    echo "!!! Dongle nenalezen (librtlsdr ho nevidí). Připoj ho a spusť znovu."
    echo; read -r -p "Zavři okno (Enter)…" _; exit 1
fi

# gr-satellites je dekodér pro 9k6 G3RUH. Bez něj přelet klidně nahraj —
# IQ zůstane na disku (dekód selže → retention ho nesmaže) a dá se dekódovat
# později. Ale ať to víš dopředu, ne až ráno z logu.
if ! command -v gr_satellites >/dev/null; then
    echo "!!! gr_satellites nenalezen — přelet se NAHRAJE, ale nedekóduje."
    echo "    Instaluje se buildem ze zdrojů, postup viz CLAUDE.md (není balíček)."
    echo
    read -r -p "Přesto spustit? [a/N] " ANSWER
    case "$ANSWER" in [aAyY]*) ;; *) exit 0 ;; esac
fi
sleep 1

nohup env \
  PYTHONUNBUFFERED=1 \
  KOSTKA_ENABLED=1 \
  DYLD_LIBRARY_PATH=/opt/homebrew/lib \
  RECORDINGS_MAX_GB=20 \
  SDR_GAIN=15.7 \
  MPLBACKEND=Agg \
  PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  .venv/bin/python -u -m agent.scheduler >> logs/agent.log 2>&1 &
disown
# SDR_GAIN=15.7 (ne 20.7) — bez filtru hrozí přebuzení tuneru silným
# rozhlasovým FM, a přebuzení je NEVRATNÉ. Zisk po LOS už nezměníš.
#
# ISS_ENABLED tu schválně NENÍ: obě družice jsou sice na 70cm a nahrávaly by se
# se stejným HW, ale ISS na vysokém přeletu (~87°) by KOSTKU (max ~40° z Brna)
# ve výběru přebila. Když chceš obojí, přidej ISS_ENABLED=1 ručně.

sleep 6
AGENT=$(pgrep -f agent.scheduler | head -1)
if [ -z "$AGENT" ]; then
    echo "!!! Agent nenaběhl. Podívej se do logs/agent.log."
    echo; read -r -p "Zavři okno (Enter)…" _; exit 1
fi
nohup caffeinate -i -s -w "$AGENT" >/dev/null 2>&1 &
disown

echo "Agent běží v režimu KOSTKA (PID $AGENT). Můžeš zavřít okno, poběží dál."
echo
echo "!!! Po přeletu vrať filtr a spusť normální 'Spustit agenta',"
echo "    jinak budou 137MHz družice (Meteor/Orbcomm) přes bypass nekvalitní."
echo
echo "Nejbližší přelet:"
grep -aE "Next:" logs/agent.log | tail -1 | sed 's/.*INFO */  /'
echo; read -r -p "Zavři okno (Enter)…" _
