"""Tests for the external-video upload dev driver."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import upload_external  # noqa: E402


def test_session_id_is_stable_from_file_hash(tmp_path):
    video = tmp_path / "Driver Swing!.mp4"
    video.write_bytes(b"same video bytes")

    first = upload_external._session_id_for(video)
    second = upload_external._session_id_for(video)

    assert first == second
    assert first.startswith("external_driver_swing_")


def test_capture_metadata_includes_optional_outcome():
    body = upload_external._capture_metadata(
        club="driver",
        view="DTL",
        outcome="good",
        location="external-upload",
    )

    assert body == {
        "tagEvents": [{"tMs": 0, "club": "driver", "view": "DTL", "outcome": "good"}],
        "location": "external-upload",
    }


def test_normalization_not_needed_for_h264_aac_mp4():
    info = upload_external.MediaInfo(
        container="mov,mp4,m4a,3gp,3g2,mj2",
        vcodec="h264",
        width=464,
        height=832,
        fps=60.0,
        duration_s=20.64,
        has_audio=True,
        acodec="aac",
    )

    assert upload_external._needs_normalization(Path("Driver.mp4"), info) is False


def test_normalization_needed_for_non_h264_video():
    info = upload_external.MediaInfo(
        container="matroska,webm",
        vcodec="vp9",
        width=1280,
        height=720,
        fps=30.0,
        duration_s=10.0,
        has_audio=True,
        acodec="opus",
    )

    assert upload_external._needs_normalization(Path("clip.webm"), info) is True
