# Patches for vendored third-party tools

`orbcomm-receiver/` is a checkout of
[fbieberly/ORBCOMM-receiver](https://github.com/fbieberly/ORBCOMM-receiver)
and is gitignored, so the fixes below would otherwise be lost with the
directory. Kept here as a patch — and as a ready-made basis for an upstream PR,
since every one of these bites any macOS user.

## orbcomm-receiver-macos.patch

Apply with:

    cd orbcomm-receiver && git apply ../patches/orbcomm-receiver-macos.patch

### What it fixes

| Fix | Why |
|---|---|
| `mp.set_start_method('fork')` | macOS (Py 3.8+) defaults to `spawn`, which re-imports the module in the child. The main `while 1:` loop sits at module level with no `__main__` guard, so the child ran the main loop instead of `process_samples()` — nothing drained the queue. |
| `queue.qsize()` → `empty()`/`get_nowait()` | `qsize()` raises `NotImplementedError` on macOS (no `sem_getvalue()`), killing the SDR callback once the queue filled. |
| `sdr.set_bias_tee(True)` | The script never powered the bias tee. With a bias-tee-fed LNA that means an unpowered attenuator — **measured +14 dB** with it on. |
| `gain = 'auto'` → manual | AGC overloads on a high pass when an LNA sits ahead of the tuner. |
| Dropped `orbcomm fm103` | No TLE in Celestrak's orbcomm group nor SatNOGS; `ephem.readtle()` aborts the whole receiver on the first satellite it cannot parse. |
| `file_decoder.py` takes a filename argument | Default picked the *first* recording of a pass — lowest elevation, worst SNR. |

### Not in the patch

- `CONFIG.py` — local station coordinates, not upstream material.
- TLE handling: Celestrak returns **CRLF**, which `ephem.readtle()` rejects, and
  `update_orbcomm_tle.py` sanity-checks for NORAD 21576 (ORBCOMM-X, long gone) so
  it never writes the file. Worked around by fetching manually; a proper fix
  would strip `\r` and check against a satellite that still exists.

### Environment note (not a code fix)

pyrtlsdr 0.4.0 binds `rtlsdr_set_dithering`, which Homebrew's osmocom
`librtlsdr` does not export — it fails at import. Use **pyrtlsdr 0.3.0**. Also
export `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` *inside* the launching
script: macOS SIP strips `DYLD_*` when exec'ing a protected binary such as
`caffeinate`.
