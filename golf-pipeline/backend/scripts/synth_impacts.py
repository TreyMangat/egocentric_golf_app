"""Synthesize a fake driving-range audio track with planted impacts.

Used by tests/test_audio_segmenter.py to feed the impact segmenter at its
default settings — synthetic audio is more discriminable than real range
audio, so we explicitly do not tune the algorithm against this signal.

A planted impact is a band-limited (3-5 kHz) burst with an exponential
decay envelope (~80 ms tail). A distractor is a low-frequency whoosh
(200-800 Hz, smooth envelope, no sharp transient) — it should NOT be
detected, by design. Background is broadband white noise at ~20 dB SNR
relative to the impact peak.

CLI:
    python scripts/synth_impacts.py --out artifacts/fake.wav \\
        --gt artifacts/fake.gt.json --duration 30 --seed 0
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import scipy.signal as sps
import soundfile as sf

# Match the segmenter's expected rate exactly so the test path mirrors prod.
SAMPLE_RATE = 22050

# Real range recordings always have a few seconds of lead-in before the first
# swing (user fumbling with the phone, walking to the tee). The synthesizer
# mirrors that with ~2 s of impact-free background at the start. Defaults are
# chosen so no planted event lands inside the lead-in window. Callers that
# specifically want to test early-impact behavior pass their own timestamps.
PRE_ROLL_S = 2.0
DEFAULT_DURATION_S = 32.0
DEFAULT_IMPACTS_MS = (5000, 10500, 16000, 21500, 27000)
DEFAULT_DISTRACTORS_MS = (13000, 23500)

IMPACT_PEAK = 0.5
IMPACT_DECAY_TAU_MS = 25.0  # envelope ~ exp(-t/tau); at 80ms ~ 4% of peak
IMPACT_DURATION_MS = 120

WHOOSH_PEAK = 0.4
WHOOSH_DURATION_MS = 350

NOISE_RMS = 0.05  # 20 dB below impact peak (peak/rms = 10)


@dataclass
class GroundTruth:
    sample_rate: int
    duration_s: float
    impacts_ms: list[int]
    distractors_ms: list[int]
    snr_db: float


def _impact_burst(rng: np.random.Generator, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Sharp 3-5 kHz transient with ~80 ms exponential decay."""
    n = int(IMPACT_DURATION_MS / 1000.0 * sr)
    t = np.arange(n) / sr
    raw = rng.standard_normal(n)
    sos = sps.butter(4, [3000, 5000], btype="bandpass", fs=sr, output="sos")
    band = sps.sosfilt(sos, raw).astype(np.float32)
    peak = float(np.max(np.abs(band)) + 1e-9)
    band /= peak
    env = np.exp(-t / (IMPACT_DECAY_TAU_MS / 1000.0)).astype(np.float32)
    return (band * env * IMPACT_PEAK).astype(np.float32)


def _whoosh(rng: np.random.Generator, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Low-frequency whoosh — gentle envelope, no sharp transient.

    Engineered to be loud in time but contain no energy in the impact
    detector's 2.5-6 kHz band, so it should slide right past.
    """
    n = int(WHOOSH_DURATION_MS / 1000.0 * sr)
    t = np.arange(n) / sr
    raw = rng.standard_normal(n)
    sos = sps.butter(4, [200, 800], btype="bandpass", fs=sr, output="sos")
    band = sps.sosfilt(sos, raw).astype(np.float32)
    peak = float(np.max(np.abs(band)) + 1e-9)
    band /= peak
    # sin² hump over the whole window — no edges sharper than the burst itself
    env = np.sin(np.pi * t / (WHOOSH_DURATION_MS / 1000.0)) ** 2
    return (band * env.astype(np.float32) * WHOOSH_PEAK).astype(np.float32)


def synthesize_session(
    duration_s: float = DEFAULT_DURATION_S,
    impacts_ms: list[int] | tuple[int, ...] = DEFAULT_IMPACTS_MS,
    distractors_ms: list[int] | tuple[int, ...] = DEFAULT_DISTRACTORS_MS,
    sample_rate: int = SAMPLE_RATE,
    seed: int = 0,
) -> tuple[np.ndarray, GroundTruth]:
    rng = np.random.default_rng(seed)
    n = int(duration_s * sample_rate)
    track = (rng.standard_normal(n) * NOISE_RMS).astype(np.float32)

    for t_ms in impacts_ms:
        burst = _impact_burst(rng, sample_rate)
        start = int(t_ms / 1000.0 * sample_rate)
        end = min(start + len(burst), n)
        track[start:end] += burst[: end - start]

    for t_ms in distractors_ms:
        wh = _whoosh(rng, sample_rate)
        start = int(t_ms / 1000.0 * sample_rate)
        end = min(start + len(wh), n)
        track[start:end] += wh[: end - start]

    gt = GroundTruth(
        sample_rate=sample_rate,
        duration_s=float(duration_s),
        impacts_ms=list(impacts_ms),
        distractors_ms=list(distractors_ms),
        snr_db=20.0,
    )
    return track, gt


def write_session(
    out_wav: Path,
    gt_json: Path,
    *,
    duration_s: float = DEFAULT_DURATION_S,
    impacts_ms: list[int] | tuple[int, ...] = DEFAULT_IMPACTS_MS,
    distractors_ms: list[int] | tuple[int, ...] = DEFAULT_DISTRACTORS_MS,
    sample_rate: int = SAMPLE_RATE,
    seed: int = 0,
) -> tuple[Path, Path, GroundTruth]:
    track, gt = synthesize_session(
        duration_s=duration_s,
        impacts_ms=impacts_ms,
        distractors_ms=distractors_ms,
        sample_rate=sample_rate,
        seed=seed,
    )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), track, gt.sample_rate, subtype="PCM_16")
    gt_json.parent.mkdir(parents=True, exist_ok=True)
    gt_json.write_text(json.dumps(asdict(gt), indent=2))
    return out_wav, gt_json, gt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=Path("artifacts/fake_range.wav"), type=Path)
    parser.add_argument("--gt", default=Path("artifacts/fake_range.gt.json"), type=Path)
    parser.add_argument("--duration", default=DEFAULT_DURATION_S, type=float)
    parser.add_argument("--seed", default=0, type=int)
    args = parser.parse_args()

    wav, gt_path, gt = write_session(
        args.out, args.gt, duration_s=args.duration, seed=args.seed
    )
    print(f"wrote {wav}")
    print(f"wrote {gt_path}")
    print(f"impacts_ms = {gt.impacts_ms}")
    print(f"distractors_ms = {gt.distractors_ms}")


if __name__ == "__main__":
    main()
