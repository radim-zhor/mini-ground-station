#!/usr/bin/env python3
"""
Posbírá efemeridy ze všech nahraných průletů a vynese je do mapy.

Každý úspěšně dekódovaný Orbcomm průlet uloží do `products/telemetry.json`
efemeridy, které o sobě družice sama odvysílala (poloha, výška, rychlost, čas
a odchylka od TLE). IQ se po úspěchu maže, ale telemetrie zůstává, takže tohle
jde spustit kdykoliv, i ráno na celém nočním úlovku:

    .venv/bin/python tools/ephemeris_map.py

Vytvoří PNG s pozemní stopou (colored per družice) a odchylkou od TLE v čase.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
REC = REPO / "recordings"
# Reálná efemerida sedí na TLE do desítek km a Orbcomm létá ~700-720 km.
# Cokoliv mimo je dekód šumu (viděno 22. 7.: rozdíl 13 350 km, výška 1940 km).
MAX_TLE_DIFF_KM = 150.0
ALT_RANGE_KM = (600.0, 800.0)


def collect():
    good, bad = [], []
    for tj in sorted(REC.glob("*/products/telemetry.json")):
        try:
            d = json.loads(tj.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue  # Meteor telemetry.json je seznam metadat snímků, ne efemeridy
        sat = d.get("satellite", tj.parents[1].name)
        for e in d.get("ephemeris", []):
            rec = {
                "sat": sat,
                "lat": e.get("lat"), "lon": e.get("lon"),
                "alt": e.get("alt_km"), "vel": e.get("velocity_ms"),
                "time": e.get("satellite_time"),
                "diff": e.get("ephemeris_diff_km"),
                "pass": tj.parents[1].name,
            }
            if rec["lat"] is None or rec["lon"] is None:
                continue
            plausible = (
                rec["diff"] is not None and rec["diff"] <= MAX_TLE_DIFF_KM
                and rec["alt"] is not None and ALT_RANGE_KM[0] <= rec["alt"] <= ALT_RANGE_KM[1]
            )
            (good if plausible else bad).append(rec)
    return good, bad


def zoom_to_data(ax, lons, lats):
    """Přiblíží na oblast dat s okrajem — efemeridy se hloučí kolem stanice,
    takže světová mapa by z nich udělala jednu tečku."""
    pad = 4.0
    lo0, lo1 = min(lons + [16.61]), max(lons + [16.61])
    la0, la1 = min(lats + [49.20]), max(lats + [49.20])
    span = max(lo1 - lo0, la1 - la0, 6.0)  # nejméně 6° pole, ať to není přeostřené
    cx, cy = (lo0 + lo1) / 2, (la0 + la1) / 2
    ax.set_xlim(cx - span / 2 - pad, cx + span / 2 + pad)
    ax.set_ylim(cy - span / 2 - pad, cy + span / 2 + pad)
    ax.grid(color="0.9", lw=0.5, zorder=0)


def main():
    good, bad = collect()
    print(f"\n  Efemeridy: {len(good)} platných, {len(bad)} odfiltrovaných (dekód šumu)\n")
    if not good:
        print("  Zatím žádná platná efemerida — spusť znovu, až nasbírá noc.\n")
        return

    per_sat = {}
    for r in good:
        per_sat.setdefault(r["sat"], []).append(r)
    print("   družice           frames   výška km        rychlost m/s     odchylka TLE km")
    for sat in sorted(per_sat):
        rs = per_sat[sat]
        al = [r["alt"] for r in rs]
        ve = [r["vel"] for r in rs]
        di = [r["diff"] for r in rs]
        print(f"  {sat:<18s} {len(rs):4d}   "
              f"{min(al):.1f}-{max(al):.1f}   {sum(ve)/len(ve):8.1f}     "
              f"med {sorted(di)[len(di)//2]:.1f}, max {max(di):.1f}")
    if bad:
        print(f"\n  Odfiltrováno {len(bad)} nesmyslných (velká odchylka/výška mimo dráhu).")

    # --- vykreslení ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 10),
                                   gridspec_kw={"height_ratios": [3, 1]})
    cmap = plt.get_cmap("tab10")
    sats = sorted(per_sat)
    all_lon, all_lat = [], []
    for i, sat in enumerate(sats):
        rs = sorted(per_sat[sat], key=lambda r: r["time"] or "")
        lons = [r["lon"] for r in rs]
        lats = [r["lat"] for r in rs]
        all_lon += lons
        all_lat += lats
        ax1.scatter(lons, lats, s=70, color=cmap(i % 10), label=f"{sat} ({len(rs)})",
                    edgecolor="white", linewidth=0.7, zorder=3)
    # pozorovatel (Brno)
    ax1.scatter([16.61], [49.20], marker="*", s=260, color="red",
                edgecolor="black", linewidth=0.6, zorder=4, label="stanice (Brno)")
    zoom_to_data(ax1, all_lon, all_lat)
    ax1.set_xlabel("zeměpisná délka [°]")
    ax1.set_ylabel("šířka [°]")
    ax1.set_title("Pozemní stopa družic z odvysílaných efemerid")
    ax1.legend(loc="lower left", fontsize=8, framealpha=0.9)
    ax1.set_aspect("equal", adjustable="box")

    # odchylka od TLE v čase
    allr = sorted(good, key=lambda r: r["time"] or "")
    xs = list(range(len(allr)))
    ax2.bar(xs, [r["diff"] for r in allr],
            color=[cmap(sats.index(r["sat"]) % 10) for r in allr])
    ax2.set_ylabel("odchylka od TLE [km]")
    ax2.set_xlabel("efemerida (v pořadí času)")
    ax2.set_title("Jak přesně TLE sedí na realitu")
    ax2.grid(axis="y", color="0.9")

    out = REPO / "recordings" / f"ephemeris_{datetime.now().strftime('%Y%m%d_%H%M')}.png"
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print(f"\n  Mapa uložena: {out}\n")
    print(out)  # posledni radek = cesta, pro snadne odeslani


if __name__ == "__main__":
    sys.exit(main())
