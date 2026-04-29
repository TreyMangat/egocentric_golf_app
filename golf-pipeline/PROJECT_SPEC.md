# Golf Swing Pipeline — Project Spec

A personal egocentric / multi-view golf swing capture and analysis pipeline.

The shape of this system intentionally mirrors Mecka AI's data infrastructure stack: iOS capture → cloud pipeline → pose extraction → unified storage → analysis surface. Same primitives (egocentric video, hand/body pose, IMU, durable orchestration), different application (personal swing analysis instead of robot policy training).

---

## Goals

**Primary** — observe and track your own golf swing with metrics and visualizations you can't get from watching live.

**Secondary** — build a serious portfolio piece that exercises Temporal, Modal/Runpod, MongoDB, Hugging Face, AWS, Swift/SwiftUI, ARKit, React/TypeScript, and Claude Code at speed.

**Explicit non-goals** (V1)
- Replace a coach or launch monitor.
- Diagnose causes of ball-flight issues without ball data.
- Real-time pose overlay during the swing on phone (deferred — full real-time is V3).
- Multi-user / coach-sharing (single-user V1, but `userId` stubbed in schemas).

---

## Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Capture environment | Driving range primarily | Outdoor, propped phone, repeat swings, Wi-Fi at home for upload sync |
| Feedback timing | Both — minimal real-time + full post-session | Real-time = swing-detected confirmation + tempo number on phone; post-session = full dashboard on laptop |
| Coaching tier | Tier 1 (metrics + biomechanical ranges) for V1 | Trustworthy, deterministic, no LLM hallucinations. Tier 3 (VLM coach) deferred to V1.5 |
| Camera count | One phone V1, two phones V2 | iPhone 16 Pro Max as primary; second phone optional for true 3D triangulation |
| View support | Both face-on (FO) and down-the-line (DTL) | Auto-detected per clip; metrics filtered by view |
| Club tagging | Manual tap in app, defaults to "same as last" | Driver ≠ wedge swing; comparing across clubs is noise |
| Outcome tagging | Optional one-tap (good/ok/bad + shot shape) | Turns dataset into something diagnostic — your bad vs good swings |
| Capture mode | Press start at session begin, backend segments by audio | V1 simplification. V2 adds on-device rolling buffer + impact detection |
| Storage policy | Video 30 days, keypoints + ref frame forever | Storage adds up at ~30MB/swing |
| Cost ceiling | ~$30/mo for inference at typical use | Modal serverless GPU at ~$0.05–0.15/swing × ~100/wk |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ iPhone (SwiftUI capture app)                                     │
│  - AVFoundation: 4K @ 60fps, audio track                         │
│  - ARKit (optional): body joints, camera intrinsics              │
│  - Club picker, view picker, outcome tagger                      │
│  - Presigned-URL upload to S3                                    │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTPS upload
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ AWS S3                                                           │
│  - raw/{userId}/{sessionId}/{clipId}.mov                         │
│  - meta/{userId}/{sessionId}/{clipId}.json (club, view, etc.)    │
└───────────────────────────┬──────────────────────────────────────┘
                            │ S3 EventBridge → API → Temporal
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Temporal Cloud (or self-hosted)                                  │
│   ProcessSession workflow                                        │
│     ├─ ingest           (probe, transcode if needed)             │
│     ├─ segment_swings   (audio impact transients → windows)      │
│     │      ├─ for each swing → child workflow ProcessSwing       │
│     │           ├─ pose_inference   (calls Modal)                │
│     │           ├─ phase_detect     (P1–P10 from pose)           │
│     │           ├─ compute_metrics  (deterministic from pose)    │
│     │           ├─ embed            (sequence → vector)          │
│     │           └─ write_to_db      (Mongo)                      │
│     └─ summarize_session                                         │
└───────────────────────────┬──────────────────────────────────────┘
                            │
              ┌─────────────┴────────────────┐
              ▼                              ▼
┌──────────────────────────────┐ ┌──────────────────────────────┐
│ Modal (or Runpod)            │ │ MongoDB Atlas                │
│   - GPU pose inference        │ │   - swings (timeseries pose) │
│   - BlazePose v1, HaMeR v2   │ │   - sessions                 │
│   - Returns keypoints        │ │   - users                    │
│   - Auto-scales to 0          │ │   - vector index on embeds  │
└──────────────────────────────┘ └──────────────┬───────────────┘
                                                │
                                                ▼
                                  ┌──────────────────────────────┐
                                  │ FastAPI                      │
                                  │   /api/v1/sessions           │
                                  │   /api/v1/swings/:id         │
                                  │   /api/v1/swings/:id/similar │
                                  │   /api/v1/upload/presign     │
                                  └──────────────┬───────────────┘
                                                 ▼
                                  ┌──────────────────────────────┐
                                  │ Next.js dashboard            │
                                  │   - Session list             │
                                  │   - Swing detail w/ overlay  │
                                  │   - Metric trendlines        │
                                  │   - Side-by-side comparison  │
                                  └──────────────────────────────┘
```

---

## Data model

### `swings` (MongoDB collection)

```jsonc
{
  "_id": "swing_2026_04_29_abc123",
  "userId": "trey",
  "sessionId": "session_2026_04_29_15h",
  "createdAt": "2026-04-29T15:34:21Z",

  "capture": {
    "view": "DTL" | "FO",            // auto-detected, user-overridable
    "club": "driver" | "7i" | "lw" | ...,
    "fps": 60,
    "resolution": [3840, 2160],
    "phoneModel": "iPhone16,2",
    "videoKey": "raw/trey/.../swing_abc123.mov",
    "videoExpiresAt": "2026-05-29T..."
  },

  "tags": {
    "outcome": "good" | "ok" | "bad" | null,
    "shape": "straight" | "draw" | "fade" | "hook" | "slice" | "fat" | "thin" | null,
    "notes": "felt rushed at top"
  },

  "phases": {                         // frame indices in pose timeseries
    "address":   {"frame": 0,    "tMs": 0},
    "takeaway":  {"frame": 24,   "tMs": 400},
    "top":       {"frame": 78,   "tMs": 1300},
    "transition":{"frame": 84,   "tMs": 1400},
    "impact":    {"frame": 102,  "tMs": 1700},
    "finish":    {"frame": 144,  "tMs": 2400}
  },

  "metrics": {
    "tempoRatioBackswingDownswing": 2.7,
    "backswingDurationMs": 1300,
    "downswingDurationMs": 480,
    "shoulderTurnAtTopDeg": 88,
    "hipTurnAtTopDeg": 42,
    "xFactorDeg": 46,
    "wristHingeMaxDeg": 88,
    "headSwayMaxMm": 45,
    "headLiftMaxMm": 18,
    "spineTiltAtAddressDeg": 32,
    "spineTiltAtImpactDeg": 36,
    "leadArmAngleAtTopDeg": 165
  },

  "ranges": {                         // per-metric: pass/warn/fail vs targets
    "tempoRatioBackswingDownswing": {"target": [2.8, 3.2], "status": "warn"}
  },

  "keypoints": {                      // pose timeseries — main payload
    "schema": "blazepose-33",
    "fps": 60,
    "data": [/* [frame][joint][x,y,z,visibility] */],
    "storageRef": "s3://.../keypoints/swing_abc123.npz" // if too big inline
  },

  "embedding": [/* 256-d vector for similarity search */],

  "pipeline": {
    "version": "0.1.0",
    "poseModel": "blazepose-full-v1",
    "modalRunId": "ap-...",
    "temporalRunId": "...",
    "processingMs": 4321
  }
}
```

### `sessions`

```jsonc
{
  "_id": "session_2026_04_29_15h",
  "userId": "trey",
  "startedAt": "...",
  "endedAt": "...",
  "location": "Practice Range Name",
  "swingCount": 47,
  "swingIds": ["swing_..."],
  "summaryMetrics": {
    "tempoRatioMean": 2.6,
    "tempoRatioStd": 0.21
    // ...
  },
  "notes": "Worked on tempo. New grip."
}
```

### Indexes

- `swings`: `(userId, createdAt desc)`, `(userId, capture.club, createdAt)`, `(userId, tags.outcome)`, plus a vector index on `embedding`
- `sessions`: `(userId, startedAt desc)`

---

## Metrics — Tier 1 list

All deterministic, computable from a 33-keypoint timeseries. No ML needed for the metrics layer — that's the point.

| Metric | View | Formula sketch | Target range |
|---|---|---|---|
| Tempo ratio | both | `backswingDur / downswingDur` | 2.8–3.2 |
| Backswing duration | both | `t(top) - t(address)` | 700–1500 ms |
| Downswing duration | both | `t(impact) - t(top)` | 220–320 ms |
| Shoulder turn at top | DTL/FO | angle of (LSh→RSh) projected | 80–105° |
| Hip turn at top | FO | angle of (LHip→RHip) projected | 35–55° |
| X-factor | FO | `shoulderTurn - hipTurn` at top | 35–55° |
| Wrist hinge max | DTL | angle (lead forearm, club proxy) | 80–95° |
| Head sway | FO | lateral disp of nose vs address | < 50 mm |
| Head lift | FO | vertical disp of nose vs address | < 30 mm |
| Spine tilt at address | DTL | torso vector vs vertical | 28–38° |
| Spine tilt at impact | DTL | torso vector vs vertical | ≥ tilt at address |
| Lead arm angle at top | DTL/FO | angle at lead elbow | 160–180° |

"Club proxy" = lead-hand wrist vector × scale; we don't track the club explicitly in V1.

---

## Phase detection (P1–P10 simplified)

Detect 6 key frames from the pose timeseries:

1. **Address** — first stable frame (wrist velocity < threshold)
2. **Takeaway** — first frame after address with wrist velocity > threshold
3. **Top** — frame where lead-wrist height is maximum
4. **Transition** — first frame after top where wrist velocity reverses
5. **Impact** — *audio-anchored*. The audio impact transient gives the exact frame; pose alone is unreliable here because of motion blur and self-occlusion
6. **Finish** — first stable frame after impact with wrist height > shoulder height

Audio-anchoring impact is the single most important detail. Don't trust pose-only detection at impact.

---

## Capture details

### V1 mode (manual session)
- User taps "Start session" in app
- Phone records continuously, 4K 60fps, audio enabled
- User taps club picker between clubs (defaults to last selected)
- Optional: user taps outcome (good/ok/bad) after each swing — UI shows last 5 swings as tappable rows
- User taps "End session" — full session video uploads (or auto-uploads in chunks during session if Wi-Fi)
- Backend segments the long video into individual swings via audio impact detection

### V2 mode (auto)
- Phone runs rolling buffer (~30s)
- Local DSP detects impact transients (Apple `SoundAnalysis` framework or custom threshold on transient sharpness 3000–5000Hz)
- Auto-extracts -5s to +2s window around each impact, uploads only that
- Practice swings (motion peak with no impact sound) optionally tagged separately
- Battery target: 2hr session

### Why audio impact works
Club-on-ball is acoustically distinctive — sharp ~100ms transient with peak energy in 3000–5000Hz, distinguishable from divot, ground, or voice. False-positive rate is low if you set the threshold right. Practice swings are loud (whoosh) but lack the sharp transient.

---

## Pipeline tech

### Temporal
- One workflow per uploaded session: `ProcessSession`
- Spawns one child workflow per detected swing: `ProcessSwing`
- Activities are idempotent and retryable (every step uses `s3 key + content hash` as idempotency key)
- Heartbeats on long activities (pose inference)
- Failure recovery: if pose inference fails on swing 17/47, the other 46 still complete

### Modal (primary GPU provider)
- One Modal function for pose inference: `extract_pose(video_s3_uri) -> keypoints_s3_uri`
- Uses A10G or T4 (T4 is fine for BlazePose; A10G needed for HaMeR)
- Cold-start mitigation: keep one warm container during a session
- Cost target: ≤ $0.10/swing average

### Runpod (alternative — benchmark, don't run both in prod)
- Same model, deployed as serverless endpoint
- Compare cold-start, throughput, $/swing
- Writeup goes in `docs/inference-benchmark.md`

### MongoDB Atlas
- Free tier (512MB) is plenty for V1 — keypoints stored compressed in S3, only metadata + embeddings inline
- Native `$vectorSearch` for "find swings most similar"
- Atlas search index on the `embedding` field

### Hugging Face
- V1: pretrained BlazePose via MediaPipe Python (not strictly HF, but the comparable HF model is fine)
- V1.5: HaMeR for 3D hand pose (HF Hub) — matches Mecka's hand-pose stack
- V2: fine-tune a swing-fault classifier on labeled swings (your outcome tags) — real HF training rep

---

## Phasing

### V1 (2 weeks) — minimum viable
- iOS app: manual session capture, club picker, outcome tagger, upload
- Backend: Temporal `ProcessSession` + `ProcessSwing`, Modal BlazePose, Mongo write
- Audio segmentation
- Tier 1 metrics
- Web dashboard: session list, swing detail with skeleton overlay, basic trendlines

### V1.5 (+1 week)
- VLM coach (Tier 3): keyframes + metrics → Claude/GPT-4o → structured feedback
- Atlas vector search: "find similar swings"
- Pro-swing reference comparison ("your closest pro match")
- ARKit body tracking ground-truth comparison vs pose model

### V2 (+2 weeks)
- On-device rolling buffer + impact detection (no manual record)
- Two-phone synced capture + true 3D triangulation
- HaMeR 3D hand pose
- Practice-swing tagging
- Modal vs Runpod benchmark writeup

### V3
- Fine-tuned fault classifier on labeled data
- Real-time on-device pose preview
- Coach-share mode (multi-user)
- Inference API as standalone product surface (mirrors Mecka's API offering)

---

## Honest reality checks (don't lose these)

- **Won't replace a coach.** Pose data is one signal of many. A coach watches ball flight + asks what you're feeling.
- **Pro comparison is theater unless reframed.** "Closest pro match" via vector search is interesting; "you differ from Rory by 3°" is noise.
- **Monocular 3D wobbles.** Single phone gives approximate 3D. Either accept 2D-only metrics in V1 or commit to two phones for V2.
- **240fps slow-mo > 30fps regular.** Downswing is ~7 frames at 30fps. Capture at 4K 60fps minimum, ideally 1080p 240fps for impact.
- **Storage costs sneak up.** 30MB/swing × 100/wk × 52 = ~150GB/yr if kept forever. The 30-day video TTL matters.

---

## Mecka interview talking points this generates

1. "Built a Temporal-orchestrated egocentric capture pipeline mirroring your architecture, with Modal for GPU inference and Mongo with vector search for similarity."
2. Concrete tradeoff opinions: BlazePose vs HaMeR vs 4D-Humans (latency, accuracy, occlusion robustness)
3. Modal vs Runpod with real $/swing numbers and cold-start measurements
4. Audio-anchored phase detection — solves the problem that pose alone is unreliable at impact
5. The on-device rolling buffer + auto-segmentation pattern (V2) — exactly the data-quality vs data-volume tradeoff their infra team thinks about
6. Honest unit economics: $X/swing inference, $Y/mo Mongo, $Z/mo S3 — they care about this
7. Pipeline failure modes hit and how Temporal's retry semantics handled them
