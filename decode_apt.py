"""
Minimal NOAA APT decoder.
Usage: python decode_apt.py <input.wav> <output.png>
"""
import sys
import numpy as np
from scipy.signal import hilbert, resample_poly
from scipy.io import wavfile
from PIL import Image

NOAA_APT_LINE_RATE = 2        # lines/sec
NOAA_APT_PIXELS_PER_LINE = 2080
NOAA_APT_WORK_RATE = 4160    # samples/sec (= 2080 pixels * 2 lines/sec)


def decode(wav_path, out_path):
    rate, data = wavfile.read(wav_path)
    print(f"Loaded {wav_path}: {rate} Hz, {len(data)} samples ({len(data)/rate:.1f}s)")

    # Convert to float mono
    if data.ndim == 2:
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    data /= np.max(np.abs(data))

    # Resample to work rate
    from math import gcd
    g = gcd(rate, NOAA_APT_WORK_RATE)
    up, down = NOAA_APT_WORK_RATE // g, rate // g
    print(f"Resampling {rate} Hz -> {NOAA_APT_WORK_RATE} Hz (up={up}, down={down})")
    data = resample_poly(data, up, down).astype(np.float32)
    print(f"After resample: {len(data)} samples")

    # AM demodulation via Hilbert envelope
    analytic = hilbert(data)
    envelope = np.abs(analytic).astype(np.float32)

    # Reshape into lines
    pixels_per_line = NOAA_APT_PIXELS_PER_LINE
    n_lines = len(envelope) // pixels_per_line
    if n_lines < 10:
        print("ERROR: Recording too short or wrong sample rate")
        sys.exit(1)

    image_data = envelope[:n_lines * pixels_per_line].reshape(n_lines, pixels_per_line)

    # Normalize per-line (handles signal fade in/out)
    p2, p98 = np.percentile(image_data, 2), np.percentile(image_data, 98)
    image_data = np.clip((image_data - p2) / (p98 - p2), 0, 1)
    image_data = (image_data * 255).astype(np.uint8)

    img = Image.fromarray(image_data, mode='L')
    img.save(out_path)
    print(f"Saved {out_path} ({img.width}x{img.height} px)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} input.wav output.png")
        sys.exit(1)
    decode(sys.argv[1], sys.argv[2])
