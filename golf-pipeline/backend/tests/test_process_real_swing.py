"""Tests for the real-swing dev driver script."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import process_real_swing  # noqa: E402


def test_presign_real_upload_uses_upload_content_type(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post_json(url: str, body: dict, timeout: float = 30.0) -> dict:
        calls.append((url, body))
        return {"upload_url": "https://example.test/upload", "s3_key": "raw/u/s/session.mov"}

    monkeypatch.setattr(process_real_swing, "_http_post_json", fake_post_json)

    upload_url, s3_key = process_real_swing._presign_real_upload(
        "http://localhost:8000",
        "user_1",
        "session_1",
        "video/mp4",
    )

    assert upload_url == "https://example.test/upload"
    assert s3_key == "raw/u/s/session.mov"
    assert calls == [
        (
            "http://localhost:8000/api/v1/upload/presign",
            {
                "user_id": "user_1",
                "session_id": "session_1",
                "clip_id": "session",
                "content_type": "video/mp4",
            },
        )
    ]


def test_content_type_for_mp4_matches_presignable_upload():
    assert process_real_swing._content_type_for(Path("swing.mp4")) == "video/mp4"
