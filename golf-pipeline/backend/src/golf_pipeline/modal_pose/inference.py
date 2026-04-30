"""Modal function for GPU pose inference on a swing clip.

Run via:
    modal serve src/golf_pipeline/modal_pose/inference.py     # dev mode (live reload)
    modal deploy src/golf_pipeline/modal_pose/inference.py    # prod

Exposed as a remote callable from the Temporal worker:

    from golf_pipeline.modal_pose.inference import extract_pose
    keypoints_uri, fps = extract_pose.remote(video_s3_uri="s3://.../swing.mov")

V1 uses MediaPipe BlazePose-Full (33 keypoints). V1.5 swaps in HaMeR for 3D
hand pose (matches Mecka's hand-pose stack). The interface stays the same;
the model dispatch lives behind the `model_name` arg.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# TODO(V1.5): migrate BOTH extract_pose (Modal GPU) and extract_pose_local
# (CPU local-dev) from mp.solutions.pose (legacy) to
# mediapipe.tasks.python.vision.PoseLandmarker. Google has marked the
# `solutions` namespace as legacy; the Tasks API is the supported path
# forward and exposes the same 33-keypoint BlazePose model with a more
# stable interface and explicit running-mode (IMAGE / VIDEO / LIVE_STREAM).
# Pin in pyproject.toml is mediapipe==0.10.14 to keep this code reproducible
# until that migration happens.
import modal

from golf_pipeline.storage.s3 import parse_s3_uri

# ─── modal app definition ─────────────────────────────────────────────────────

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install(
        "mediapipe==0.10.14",
        "opencv-python-headless==4.10.0.84",
        "numpy==1.26.4",
        "boto3==1.34.99",
        "ffmpeg-python==0.2.0",
    )
)

app = modal.App("golf-pose-inference", image=image)

# Re-use a warm container during a session — saves ~10s/swing on cold starts.
SCALEDOWN_WINDOW_S = 300
GPU = "T4"  # bump to "A10G" for HaMeR

# AWS creds passed via Modal secrets (`modal secret create aws ...`)
aws_secret = modal.Secret.from_name("aws-credentials")


# ─── the function ─────────────────────────────────────────────────────────────


@app.function(
    gpu=GPU,
    secrets=[aws_secret],
    timeout=600,
    scaledown_window=SCALEDOWN_WINDOW_S,
)
def extract_pose(
    video_s3_uri: str,
    out_keypoints_s3_uri: str,
    model_name: str = "blazepose-full",
) -> dict:
    """Download video, run pose, upload .npz of keypoints, return summary.

    Returns:
        {
            "fps": int,
            "frames": int,
            "schema": "blazepose-33-v2" | "hamer-21",
            "keypoints_uri": "s3://.../swing.npz",
            "model": "blazepose-full",
        }
    """
    import boto3
    import cv2
    import mediapipe as mp
    import numpy as np

    src_bucket, src_key = parse_s3_uri(video_s3_uri)
    dst_bucket, dst_key = parse_s3_uri(out_keypoints_s3_uri)

    with tempfile.TemporaryDirectory() as td:
        local_video = Path(td) / "swing.mov"
        local_npz = Path(td) / "swing.npz"

        # download
        s3 = boto3.client("s3")
        s3.download_file(src_bucket, src_key, str(local_video))

        # extract pose frame-by-frame
        cap = cv2.VideoCapture(str(local_video))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 60)
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if model_name != "blazepose-full":
            raise NotImplementedError(f"V1 only supports blazepose-full, got {model_name!r}")

        n_joints = 33
        # Two parallel arrays so downstream consumers can pick the right
        # space: world (metric, hip-centered) for biomechanics, image
        # (normalized, in-frame) for the SVG overlay. BlazePose's image-
        # space z is unreliable so the image array drops it.
        kp_world = np.full((n_frames, n_joints, 4), np.nan, dtype=np.float32)
        kp_image = np.full((n_frames, n_joints, 3), np.nan, dtype=np.float32)
        with mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=2,
            enable_segmentation=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as pose:
            i = 0
            while True:
                ok, frame = cap.read()
                if not ok or i >= n_frames:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                res = pose.process(rgb)
                if res.pose_world_landmarks:
                    for j, lm in enumerate(res.pose_world_landmarks.landmark):
                        kp_world[i, j] = (lm.x, lm.y, lm.z, lm.visibility)
                if res.pose_landmarks:
                    for j, lm in enumerate(res.pose_landmarks.landmark):
                        kp_image[i, j] = (lm.x, lm.y, lm.visibility)
                i += 1
        cap.release()

        np.savez_compressed(
            local_npz,
            keypoints_world=kp_world,
            keypoints_image=kp_image,
            fps=fps,
        )
        s3.upload_file(str(local_npz), dst_bucket, dst_key)

    return {
        "fps": fps,
        "frames": int(n_frames),
        "schema": "blazepose-33-v2",
        "keypoints_uri": out_keypoints_s3_uri,
        "model": model_name,
    }


# ─── local fallback (cpu, for development without modal) ──────────────────────


def extract_pose_local(video_path: str, out_npz: str) -> dict:
    """CPU/local equivalent — same model, no GPU. For dev / unit tests."""
    import cv2
    import mediapipe as mp
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 60)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    # See the dual-array note in extract_pose for why both spaces are stored.
    kp_world = np.full((n_frames, 33, 4), np.nan, dtype=np.float32)
    kp_image = np.full((n_frames, 33, 3), np.nan, dtype=np.float32)

    with mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as pose:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok or i >= n_frames:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = pose.process(rgb)
            if res.pose_world_landmarks:
                for j, lm in enumerate(res.pose_world_landmarks.landmark):
                    kp_world[i, j] = (lm.x, lm.y, lm.z, lm.visibility)
            if res.pose_landmarks:
                for j, lm in enumerate(res.pose_landmarks.landmark):
                    kp_image[i, j] = (lm.x, lm.y, lm.visibility)
            i += 1
    cap.release()

    os.makedirs(os.path.dirname(out_npz) or ".", exist_ok=True)
    np.savez_compressed(
        out_npz,
        keypoints_world=kp_world,
        keypoints_image=kp_image,
        fps=fps,
    )
    return {
        "fps": fps,
        "frames": n_frames,
        "schema": "blazepose-33-v2",
        "model": "blazepose-full",
    }
