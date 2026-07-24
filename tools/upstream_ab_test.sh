#!/bin/bash
# A/B test: nahraje jeden pas nezměněným upstream rekordérem a dekóduje ho.
#
# Otázka, kterou to zodpoví: je vada v našem recorder.py, nebo v podmínkách?
# Reference z 22. 7. 00:47 dekóduje na PER 1.0 %, naše nahrávky ze stejné noci
# na 75 až 100 %. Metadatová cesta je prokazatelně nevinná (reference dekóduje
# čistě i s našimi TLE, alt a pojmenováním), takže rozdíl je v samotných
# vzorcích. Upstream čte 2 s jedním voláním, my streamujeme 0,5s bloky.
#
# Stojí to jeden průlet, protože dongle je jen jeden.

set -u
REPO=/Users/radimzhor/Documents/satelite_tracker
REC=$REPO/orbcomm-receiver
PY=$REPO/.venv/bin/python
LOG=/tmp/upstream_ab.log
START_HHMM=${1:-2336}     # kdy zastavit agenta
STOP_HHMM=${2:-2348}      # kdy nejpozději ukončit nahrávání

export DYLD_LIBRARY_PATH=/opt/homebrew/lib
export MPLBACKEND=Agg
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

: > "$LOG"
say "cekam do $START_HHMM"
while [ "$(date +%H%M)" -lt "$START_HHMM" ]; do sleep 10; done

say "zastavuji agenta, uvolnuji dongle"
pkill -f "caffeinate -i -s -w"
pkill -f "agent.scheduler"
sleep 3

BEFORE=$(ls "$REC"/data/*.mat 2>/dev/null | wc -l | tr -d ' ')
say "souboru v data/ pred testem: $BEFORE"

say "spoustim upstream record_orbcomm.py (ceka na druzici nad 15 stupnu)"
cd "$REC" || exit 1
"$PY" record_orbcomm.py >> "$LOG" 2>&1 &
RECPID=$!

while [ "$(date +%H%M)" -lt "$STOP_HHMM" ]; do
    kill -0 "$RECPID" 2>/dev/null || { say "rekorder skoncil sam"; break; }
    sleep 5
done
kill "$RECPID" 2>/dev/null
sleep 3
say "nahravani ukonceno"

AFTER=$(ls "$REC"/data/*.mat 2>/dev/null | wc -l | tr -d ' ')
say "novych souboru: $((AFTER - BEFORE))"

say "restartuji agenta"
cd "$REPO" || exit 1
nohup env PYTHONUNBUFFERED=1 DYLD_LIBRARY_PATH=/opt/homebrew/lib \
    RECORDINGS_MAX_GB=10 MPLBACKEND=Agg \
    PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin \
    "$PY" -u -m agent.scheduler >> logs/agent.log 2>&1 &
disown
sleep 8
AG=$(pgrep -f agent.scheduler | head -1)
nohup caffeinate -i -s -w "$AG" >/dev/null 2>&1 &
disown
say "agent bezi jako PID $AG"

say "dekoduji 3 nejnovejsi nove soubory"
cd "$REC" || exit 1
for f in $(ls -t data/*.mat | head -3); do
    say "--- $f"
    "$PY" file_decoder.py "$f" 2>&1 | grep -iE "PER:|packets with errors|Satellites in recording|Remaining frequency" | tee -a "$LOG"
done
say "hotovo, cely log v $LOG"
