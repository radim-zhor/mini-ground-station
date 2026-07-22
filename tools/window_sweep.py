#!/usr/bin/env python3
"""
Dekóduje jeden Orbcomm pas z několika různých 2s oken a porovná PER.

Slouží k ověření hypotézy, že `best_window_offset` (maximum SNR) padá na
izolované rušivé záblesky, a že stabilnější okno dekóduje líp.

    .venv/bin/python tools/window_sweep.py recordings/ORBCOMM_FM_116_...
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import orbcomm  # noqa: E402

WINDOW_S = orbcomm.WINDOW_S


def sustained_offsets(series, duration_s, n=4):
    """Okna s nejvyšším *minimem* SNR — tedy vysoká a zároveň stabilní."""
    if not series:
        return []
    step = series[1][0] - series[0][0] if len(series) > 1 else 0.5
    k = max(1, int(round(WINDOW_S / step)))
    scored = []
    for i in range(len(series) - k):
        chunk = [v for _, v in series[i:i + k]]
        start = float(series[i][0]) - step
        if 0 <= start <= duration_s - WINDOW_S:
            # minimum uvnitř okna: zábleskové okno má nízké minimum
            scored.append((min(chunk), statistics.mean(chunk), start))
    scored.sort(reverse=True)
    out, seen = [], []
    for mn, mean, start in scored:
        if all(abs(start - s) > WINDOW_S for s in seen):
            seen.append(start)
            out.append((start, mn, mean))
        if len(out) >= n:
            break
    return out


def run(pass_dir: Path):
    meta = json.loads((pass_dir / "meta.json").read_text())
    iq = sorted(pass_dir.glob("*.cs16"))
    if not iq:
        raise SystemExit("Zadne .cs16 — retence ho uz smazala.")
    series = meta.get("snr_series") or []
    dur = meta.get("duration_s", 0)

    sat_key = orbcomm.sat_db_name(meta.get("satellite", ""))
    tle = orbcomm._tle_for(meta.get("satellite", ""))
    if sat_key is None or tle is None:
        raise SystemExit(f"Chybi sat_key/TLE pro {meta.get('satellite')}")

    cands = []
    cur = orbcomm.best_window_offset(series, dur)
    cands.append(("max SNR (soucasna heuristika)", cur))
    for i, (start, mn, mean) in enumerate(sustained_offsets(series, dur), 1):
        cands.append((f"stabilni #{i} (min {mn:.1f}, prum {mean:.1f} dB)", start))

    print(f"\n  {meta['satellite']}  {dur:.0f} s  avg {meta.get('avg_snr')} dB  peak {meta.get('peak_snr')} dB")
    print(f"  IQ: {iq[0].name}  ({iq[0].stat().st_size/1e9:.2f} GB)\n")
    print(f"  {'okno':<42s} {'offset':>8s} {'pakety':>7s} {'PER':>8s}")
    print("  " + "-" * 70)

    results = []
    for label, off in cands:
        try:
            mat = orbcomm._write_mat(iq[0], meta, sat_key, tle, off)
            stdout = orbcomm._run_decoder(mat)
            stats = orbcomm.parse_output(stdout)
            per = stats.get("per")
            pk = stats.get("packets", 0)
            results.append((label, off, pk, per))
            print(f"  {label:<42s} {off:7.1f}s {pk:7d} {('%.1f%%' % per) if per is not None else '   n/a':>8s}")
        except Exception as e:
            print(f"  {label:<42s} {off:7.1f}s   CHYBA: {type(e).__name__}: {e}")
        finally:
            try:
                mat.unlink()
            except Exception:
                pass

    ok = [r for r in results if r[3] is not None and r[3] < 50]
    print()
    if ok:
        best = min(ok, key=lambda r: r[3])
        print(f"  ZAVER: nejlepsi okno '{best[0]}' na {best[1]:.1f} s dalo PER {best[3]:.1f} %.")
        print("  Hypoteza potvrzena — vyber okna je pricinou, ne prijem.")
    else:
        print("  ZAVER: zadne okno nedalo PER pod 50 %. Pricina je jinde nez ve vyberu okna.")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run(Path(sys.argv[1]))
