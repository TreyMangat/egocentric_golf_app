"""Audio-based swing segmentation.

Detect club-on-ball impact transients in a session video's audio track.
The acoustic signature of golf impact is distinct: a sharp ~50–150ms transient
with peak energy in the 3–5 kHz band, well above whoosh / divot / voice.

This is the most important piece of the segmentation pipeline. Audio gives us
the impact frame at sub-millisecond precision; pose alone is unreliable at impact
because of motion blur and self-occlusion.

Algorithm
---------
1. Extract audio at a known rate (e.g. 22.05 kHz mono).
2. Bandpass to 2.5–6 kHz where impact energy concentrates.
3. Compute onset envelope (spectral flux on the bandpassed signal).
4. Peak-pick with adaptive threshold (e.g. local median + k * MAD).
5. For each peak, validate with a sharpness check: peak height vs surrounding
   100ms window, and a duration check (transient must be < 150ms wide).
6. Emit `SwingWindow`s spanning [-5s, +2s] around each impact, capped to the
   audio length, deduped if windows overlap.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from golf_pipeline.schemas import SwingWindow

# tunables — calibrate on your own audio
SAMPLE_RATE = 22050
BAND_LOW_HZ = 2500
BAND_HIGH_HZ = 6000
MIN_INTER_IMPACT_MS = 3000  # min spacing between detected impacts
PRE_WINDOW_MS = 5000
POST_WINDOW_MS = 2000
ONSET_MAD_K = 6.0  # threshold = median + k * MAD

# DSP guard, NOT a sensitivity tunable. librosa.onset.onset_strength zero-pads
# frames at the start of the spectral-flux computation; the first non-zero
# bandpassed frame then produces a one-frame "fake onset" comparable in
# magnitude to a real impact. We mask the first ONSET_WARMUP_MS of the onset
# envelope to suppress that startup artifact. Threshold (ONSET_MAD_K) and
# band edges (BAND_LOW_HZ / BAND_HIGH_HZ) are unchanged — this fixes the
# DSP startup transient, it does not tune detection sensitivity.
ONSET_WARMUP_MS = 150


@dataclass
class Impact:
    t_ms: int
    confidence: float


def extract_audio(video_path: str | Path, out_wav: str | Path) -> Path:
    """Use ffmpeg to pull a mono 22.05 kHz wav from the video."""
    out = Path(out_wav)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-vn", "-f", "wav", str(out),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def detect_impacts(wav_path: str | Path) -> list[Impact]:
    """Return a list of impact times (in ms from start) with confidences."""
    import librosa
    import scipy.signal as sps

    y, sr = librosa.load(str(wav_path), sr=SAMPLE_RATE, mono=True)

    # bandpass filter to the impact band
    sos = sps.butter(
        N=4,
        Wn=[BAND_LOW_HZ, BAND_HIGH_HZ],
        btype="bandpass",
        fs=sr,
        output="sos",
    )
    y_bp = sps.sosfiltfilt(sos, y)

    # onset envelope from spectral flux
    onset = librosa.onset.onset_strength(
        y=y_bp.astype(np.float32),
        sr=sr,
        hop_length=256,
    )

    # Suppress the spectral-flux startup artifact (see ONSET_WARMUP_MS docstring).
    warmup_frames = int(np.ceil(ONSET_WARMUP_MS / 1000.0 * sr / 256))
    if warmup_frames > 0:
        onset[:warmup_frames] = 0.0

    # adaptive threshold via local MAD
    median = np.median(onset)
    mad = np.median(np.abs(onset - median)) + 1e-9
    threshold = median + ONSET_MAD_K * mad

    # TODO(real-audio calibration): scipy.signal.find_peaks with `distance=` is
    # amplitude-priority — within MIN_INTER_IMPACT_MS it keeps the larger peak
    # and drops the smaller, regardless of which one is the real impact. A loud
    # non-impact event (range chatter, neighbor divot strike, dropped club)
    # inside that window can shadow a real swing. Revisit when we have a real
    # range recording; likely needs prominence-based dedup or a smarter pairing
    # strategy in windows_from_impacts that uses confidence scores.
    peaks, props = sps.find_peaks(
        onset,
        height=threshold,
        distance=int(sr / 256 * MIN_INTER_IMPACT_MS / 1000),
    )

    times_s = librosa.frames_to_time(peaks, sr=sr, hop_length=256)
    impacts: list[Impact] = []
    for idx, t in zip(peaks, times_s, strict=True):
        # confidence: how many MADs above median, squashed to [0, 1]
        z = (onset[idx] - median) / mad
        confidence = float(1 - math.exp(-max(0, z - ONSET_MAD_K) / 6))
        impacts.append(Impact(t_ms=int(t * 1000), confidence=confidence))

    return impacts


def windows_from_impacts(
    impacts: list[Impact],
    audio_duration_ms: int,
    swing_id_for_index: callable | None = None,
) -> list[SwingWindow]:
    """Convert impact times to swing-window spans.

    Args:
        impacts: detected impacts within the session
        audio_duration_ms: total session duration, to clip windows at the edges
        swing_id_for_index: callback(i) → swing id; defaults to a positional id

    Returns:
        Non-overlapping `SwingWindow`s. If two impacts fall within the merged
        window, only the higher-confidence one is kept.
    """
    if swing_id_for_index is None:
        def swing_id_for_index(i: int) -> str:
            return f"swing_{i:03d}"

    # sort by impact time, then dedupe overlapping
    sorted_imps = sorted(impacts, key=lambda x: x.t_ms)
    chosen: list[Impact] = []
    for imp in sorted_imps:
        if chosen and (imp.t_ms - chosen[-1].t_ms) < MIN_INTER_IMPACT_MS:
            # overlap — keep higher confidence
            if imp.confidence > chosen[-1].confidence:
                chosen[-1] = imp
            continue
        chosen.append(imp)

    windows: list[SwingWindow] = []
    for i, imp in enumerate(chosen):
        start = max(0, imp.t_ms - PRE_WINDOW_MS)
        end = min(audio_duration_ms, imp.t_ms + POST_WINDOW_MS)
        windows.append(
            SwingWindow(
                swing_id=swing_id_for_index(i),
                start_ms=start,
                end_ms=end,
                impact_ms=imp.t_ms,
                impact_confidence=imp.confidence,
            )
        )
    return windows


def segment_video(video_path: str | Path, tmp_wav: str | Path) -> list[SwingWindow]:
    """Convenience: extract audio, detect impacts, return swing windows."""
    extract_audio(video_path, tmp_wav)

    import soundfile as sf
    info = sf.info(str(tmp_wav))
    duration_ms = int(info.duration * 1000)

    impacts = detect_impacts(tmp_wav)
    return windows_from_impacts(impacts, audio_duration_ms=duration_ms)
