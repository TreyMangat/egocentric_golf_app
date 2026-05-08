# Egocentric Golf Swing Pipeline — Master Doc

A complete, honest record of the project: architecture, what's built, what's broken, what's deferred, and what's only on paper. Source material for resume and cover-letter generation.

**Repo:** github.com/TreyMangat/egocentric_golf_app (private)
**Status:** V1 running locally against real cloud backends. First real swing processed end-to-end via external video upload path. Takeaway phase detector found and fixed against real-data bug. Phone-capture path validated; first phone-captured swing waiting on user recovery from injury.
**Built:** late April – May 2026
**Why it exists:** Designed to mirror Mecka AI's egocentric data infrastructure stack as a portfolio piece. Same primitives (egocentric video, body pose, durable orchestration), different application (personal swing analysis vs robot policy training).

Last updated: May 8, 2026.

---

## What it actually does

Captures golf swings on iPhone (via PWA installed to home screen, no App Store fee), uploads to S3, runs a Temporal-orchestrated pipeline that segments the session by audio impact transients, runs MediaPipe BlazePose pose estimation on each detected swing, computes deterministic biomechanical metrics, writes results to MongoDB Atlas, and renders everything in a Next.js dashboard with skeleton overlay, phase scrubbing, and side-by-side swing comparison.

Until phone capture happens (blocked on user recovery from injury), an `upload_external.py` script feeds existing video files (own captures or sourced footage) through the same presigned PUT path the PWA uses, so the rest of the pipeline runs identically.

---

## Stack (V1 actual, not aspirational)

- **Capture**: PWA built with Next.js 15 + TypeScript + Tailwind. Uses `getUserMedia` for camera access, `MediaRecorder` for video capture (1080p @ 60fps, MP4/H.264), Wake Lock API to prevent screen sleep, presigned PUT for upload. Installable via Safari "Add to Home Screen." Validated on iPhone over Cloudflare Tunnel.
- **Native iOS scaffold**: ~1000 LOC of SwiftUI + AVFoundation parked in `ios/`. Functional but unused in V1. Reserved for V2 when ARKit body tracking and 240fps slow-mo justify the $99/yr Apple Developer fee.
- **Object storage**: AWS S3 (real, not just configured), presigned URLs, 30-day TTL on raw video, 7-day expiry on swing video URLs (was 1 hour, fixed in `64066a7`), keypoints + reference frame retained forever.
- **Orchestration**: Temporal local dev server, Cloud-ready. `ProcessSession` parent workflow fans out to per-swing `ProcessSwing` child workflows. Activities are idempotent (s3 key + content hash as key) and heartbeat. One swing failing doesn't kill the session.
- **GPU inference**: MediaPipe BlazePose-Full, 33 keypoints, pinned at `0.10.14`. Modal serverless GPU function written but disabled by `LOCAL_DEV=true` flag — V1 runs pose on the developer's machine to stay $0/mo. Modal flips on for V1.5.
- **Audio segmenter**: ffmpeg → 22.05kHz mono WAV → Butterworth 4th-order bandpass 2.5–6kHz → librosa spectral flux onset envelope → adaptive threshold (median + 6×MAD) → peak-pick with 3000ms min spacing → emits `SwingWindow`s of `[-5s, +2s]` around each detected impact.
- **Database**: MongoDB Atlas M0 free tier (real, populated). Pydantic schemas with by_alias for Mongo. Auto-created indexes on `(userId, createdAt)`, `(userId, club, createdAt)`, `(userId, outcome)`. Atlas `$vectorSearch` index documented and the API endpoint exists, but the embedding is not yet computed (V1.5).
- **API**: FastAPI with `/upload/presign`, `/sessions`, `/sessions/{id}/finalize`, `/swings`, `/swings/{id}`, `/swings/{id}/similar` (V1.5). CORS configured for the web client.
- **Frontend dashboard**: Next.js 15 App Router. Custom dark instrument-panel aesthetic. Six routes: `/` (sessions), `/swings`, `/sessions/[id]`, `/swing/[id]`, `/capture`, `/compare`.
- **Tunnel**: Cloudflare Tunnel for HTTPS to phone. Required because `getUserMedia` is mandatory-HTTPS on iOS.

---

## Tier 1 metrics (12 deterministic, computed from pose timeseries)

| Metric | View | Target range |
|---|---|---|
| Tempo ratio (backswing:downswing) | both | 2.8–3.2 |
| Backswing duration | both | 700–1500 ms |
| Downswing duration | both | 220–320 ms |
| Shoulder turn at top | DTL/FO | 80–105° |
| Hip turn at top | FO | 35–55° |
| X-factor (shoulder − hip) | FO | 35–55° |
| Wrist hinge max | DTL | 80–95° |
| Head sway max | FO | < 50 mm |
| Head lift max | FO | < 30 mm |
| Spine tilt at address | DTL | 28–38° |
| Spine tilt at impact | DTL | ≥ tilt at address |
| Lead arm angle at top | DTL/FO | 160–180° |

Each metric ships with a `RangeStatus` (pass/warn/fail) computed against the target range with 15% padding.

---

## Phase detection (the load-bearing detail)

Six keyframes detected per swing: address, takeaway, top, transition, impact, finish.

**Five are detected from pose alone.** The sixth — impact — is **audio-anchored**. Pose at impact is unreliable because of motion blur and self-occlusion of the lead wrist behind the trail wrist at the moment of contact. The audio segmenter's detected impact time is converted to a frame index and passed into `compute_all` as the authoritative impact frame. This is the single most important architectural decision in the metrics layer.

**Takeaway detector specifics (V1, post-fix):** consumes lead-wrist 3D speed (`np.linalg.norm * fps`), applies 3-frame `uniform_filter1d` smoothing, then triggers on the first frame where smoothed speed sustains above `2 × speed_thresh` for `ceil(0.2 * fps)` consecutive frames (200ms persistence). The persistence requirement is what discriminates real backswing initiation from pre-shot waggle bursts and pose-tracking jitter — a single-frame spike, or even a 3–5 frame waggle pulse, will not trigger.

---

## Decisions locked

- One phone V1, two phones V2 (true 3D triangulation)
- Both face-on (FO) and down-the-line (DTL) supported, auto-detected per clip, metrics filtered by view
- Manual session start/stop in V1, on-device rolling buffer + auto-detection in V2
- Club tagging via tap in app, defaults to last selected
- Outcome tagging optional (good/ok/bad + draw/fade/hook/slice/fat/thin)
- Storage: video 30 days, keypoints + ref frame forever
- Cost target ~$0/mo for V1 (LOCAL_DEV mode), ~$30/mo target for V1.5 with Modal at ~100 swings/wk
- Single-user V1, `userId` field stubbed everywhere for future multi-user
- Tier 1 metrics only V1, Tier 3 (VLM coach via Claude/GPT-4o on keyframes) deferred to V1.5
- Pro comparison reframed as "closest pro match" via vector search, not "diff vs Rory"

---

## Build process — what actually happened

### Phase 1: Architecture and scoping (~1 day, in chat)
Researched Mecka AI's stack, picked golf swing analysis as the application, decided on driving range as primary capture environment, chose audio impact detection over manual clipping, wrote `PROJECT_SPEC.md` as single source of truth.

### Phase 2: Backend scaffold (~3 hours, Claude Code Opus 4.7)
45 files, ~4000 lines. Pydantic schemas, audio segmenter with full DSP pipeline, Modal pose function + CPU local fallback, metrics module with all 12 Tier 1 computations, Temporal workflows + activities, FastAPI server with all six endpoints, 3 unit tests passing for synthetic pose data → metrics.

### Phase 3: PWA capture surface (~2 hours)
Decided against $99/yr Apple Developer fee for V1 — built PWA. Full-screen capture page, live preview, REC pill, club picker, view toggle, tag panel, upload progress. PWA manifest + service worker. SwiftUI scaffold preserved in `ios/`.

### Phase 4: Cloud setup decisions (in chat)
All free for V1: local Temporal, MongoDB Atlas M0, AWS S3 free tier, Vercel hobby. Modal deferred. Cloudflare Tunnel required for phone HTTPS.

### Phase 5: Backend hardening with Claude Code (~3 hours, multiple commits)
Audit caught mediapipe API regression risk — pinned to `0.10.14`. Synthetic audio segmenter test built (`synth_impacts.py` + `test_audio_segmenter.py`) — failed at default settings, surfaced two real algorithmic bugs (startup artifact, dedup shadowing). Fixed startup artifact, dedup shadowing left as TODO needing real audio. `LOCAL_DEV` flag wired so pose runs on local CPU/GPU instead of Modal. End-to-end smoke test script added. Code review caught and fixed: ffmpeg keyframe alignment, lazy-import discipline, duplicated parsing, defensive guards.

### Phase 6: Frontend pass with Claude Code
Found and fixed coordinate-space bug — backend stored `pose_world_landmarks` (3D metric, hip-centered) but SVG overlay needed image-normalized [0,1] coords. Fix: dual `keypoints_world` (33×4) and `keypoints_image` (33×3) arrays. Schema bumped to `blazepose-33-v2`. Frontend types split into `WorldJoint` vs `ImageJoint`.

Then a feature pass: phase pills clickable, frame-step controls, scrubber promoted to real timeline, side-by-side compare view at `/compare`, club filter pills with URL state, tempo trajectory chart on session detail, categorical metric grid on swing detail.

### Phase 7: Documentation
`docs/first-run.md` — terse, copy-pasteable setup guide. Cross-linked to `PROJECT_SPEC.md`, `capture-surface.md`, `atlas-vector-index.md`.

### Phase 8: Cloud integration + first real swing (May 2026)
Set up real MongoDB Atlas M0 cluster, S3 bucket with least-privilege IAM, local Temporal dev server, Cloudflare Tunnel for HTTPS-to-phone. Validated PWA on iPhone — camera access, home-screen install, all clean.

User injury blocked the canonical "first real swing" milestone (driving-range capture via PWA), so substituted: built `scripts/upload_external.py` to direct-upload existing video files via the same presigned PUT path the PWA uses. Processed `Driver.mp4` (real driving-range footage) end-to-end. Audio segmenter detected 5 impacts; motion gate accepted 1 (`external_driver_d860189231a8_swing_003`) and rejected 4 — the rejected ones are audio false positives (background range noise hitting the 2.5–6kHz transient filter without corresponding swing motion). Pose ran locally (LOCAL_DEV=true), metrics persisted to Atlas, dashboard rendered with skeleton overlay aligned to the golfer.

This is the first real-data validation milestone for the project.

### Phase 9: First real-data bug — takeaway phase detection (May 2026)
Visual inspection of swing_003's dashboard showed takeaway marker firing at ~0.4s into the fragment — far too early. Impact (audio-anchored) matched actual contact correctly; takeaway was sitting essentially on top of address. Investigation followed a disciplined diagnose-propose-implement workflow:

- **Step 1:** Built static HTML diagnostic embedding lead-wrist 3D speed, threshold lines, persisted phase markers, video fragments per swing. Committed at `becfef0`.
- **Step 2:** Located detector logic at `compute.py:94-99` in `detect_phases`. Documented the signal it consumes, the trigger rule, the search window, and assumptions about address.
- **Step 3:** Empirically verified on swing_003: speed_thresh=0.067, address_frame=16, takeaway_frame=24 — bug was a single-frame spike (0.1362, 1.2% over trigger) latching takeaway during pre-shot stillness. Address detector itself was fine. Threshold was a contributing factor but not dominant.
- **Step 4:** Initial fix proposal (3-frame smoothing + 50ms persistence) failed empirical validation — landed at f=45 inside a sub-second waggle burst. Parameter sweep showed 200ms persistence minimum needed to reach the real wrist-motion-onset at f≈249. The empirical sweep caught a spec error before code shipped.
- **Step 5:** Implemented Fix B at `compute.py:94-115` — 3-frame `uniform_filter1d` smoothing + `ceil(0.2 * fps)`-frame persistence above 2× threshold. Regression test against swing_003 fixture asserts `takeaway_frame ∈ [150, 290]`, `> 30`, `< impact_frame`. 29/29 backend tests pass, ruff clean. Committed at `4e92602`.
- **Step 6:** Backfilled swing_003 — Mongo now reflects `takeaway=249` (was 24). Diff was takeaway-only: all 12 stored metrics unchanged because the current `tempo()` and head-excursion math doesn't read `phases.takeaway`. Analytical sanity-check of remaining phases on the same clip: `top=281` (lwr_y plateau peak +0.495 m above shoulder mid +0.429 m, but wrist still moving at 1.63 m/s — argmax(y) catches a y-peak, not a velocity-quiescent apex), `transition=282` (just top+1; the "first frame y_decreases" rule is structurally redundant against a flat top), `finish=359` (hit the `impact + 1 s` fallback cap — the natural wrist-above-shoulder + speed-quiet condition never fires post-impact on this clip; wrist at f=359 is 8 mm below shoulder mid and speed is rising). Dashboard visual verification of the takeaway marker deferred to user.

Findings logged from this investigation:
- **Audio segmenter false-positive rate** (4/5 on diagnostic clip): motion gate currently masks but ideally segmenter wouldn't surface them. V1.5.
- **Wrist-vs-clubhead lever-arm gap** (~75–100 frames at 60fps): wrist-based takeaway detector lags the human-visual takeaway because the clubhead at the lever-arm tip moves visibly before the wrist itself ramps. Unfixable without club tracking. V2.
- **Phase-anchor semantics: `tempo()` and head-excursion metrics anchor on `address`, not `takeaway`.** Audio segmenter pre-pads -5 s before impact, so `address_frame` (first 100 ms quiet window) routinely lands in the pre-shot routine. On swing_003 this produces a 4.4 s "backswing" and `tempoRatioBackswingDownswing = 13.94` (target 2.8-3.2); `headSwayMaxMm = 330` and `headLiftMaxMm = 317` include all pre-swing setup motion. Detectors are fine — the metrics' phase-anchors aren't. Re-anchoring `tempo()` on takeaway and clipping head-excursion windows to `[takeaway, finish]` would tighten without touching detector logic. Revisit before V1.5.
- **Top detector is position-only (no velocity gate).** `argmax(lead_wrist_y)` between takeaway and impact catches the y-peak but not the biomechanical apex (wrist velocity ≈ 0). On swing_003 the picked frame has wrist speed 1.63 m/s. Likely contributes to under-measured shoulder/hip turn-at-top.
- **`finish` frequently hits the `impact + 1 s` fallback cap on real swings.** The wrist-above-shoulder + 100 ms-quiet condition never fires post-impact on swing_003 — the lead wrist genuinely doesn't return above the shoulder line within the captured follow-through. Cap keeps the math finite but means "finish" on this clip is fabricated. May be a recurring pattern on driver swings whose follow-through is mid-motion when the audio window ends.

---

## Repo state (commit graph as of last session)

```
4e92602  feat: takeaway persistence + smoothing fix (200ms gate)
becfef0  docs: takeaway phase diagnostic report (Step 1)
1ef5ecd  test: cover swing overlay animation lifecycle (vitest)
64066a7  fix: extend swing video URLs to seven days
31ebe28  fix: render overlays when capture resolution is missing
476e763  feat: add external video upload driver
279703b  docs: first-run.md
74fa753  /swing/[id] design pass — categorical metrics + scrubber
cbe6929  /sessions/[id] design pass — tempo trajectory chart
277daa4  /swings design pass — club filter pills + URL state
d936bed  / design pass — practice band summary stats
74e4e3a  feat: side-by-side compare view at /compare
3f78a64  feat: phase scrubber with frame ticks and clickable markers
a5b6c4d  feat: ←/→ frame stepping + ↑/↓ phase nav
b1a87f3  feat: phase pills clickable to seek video
6ed50b8  /swings index page
a07d1bd  feat: BlazePose skeleton overlay with rAF lifecycle
8171216  /sessions/[id] page
54bd72b  refactor: pre-existing ruff cleanups
eea1f47  refactor: address code-review findings (6 items in one commit)
5e0e4e8  feat: end-to-end local pipeline smoke script
1252e5e  feat: LOCAL_DEV flag for pose-routing
3ea1b69  test: synthetic audio segmenter + early-impact regression
8492e78  fix: mask onset-envelope startup artifact in detect_impacts
247243b  build: pin mediapipe to 0.10.14, flag legacy solutions API
bcb8ca2  fix: dual keypoints_world/keypoints_image storage
fda6c1f  build: commit web/package-lock.json
... (initial scaffold commits)
```

Plus intermediate commits between `becfef0` and `4e92602` for Steps 2–4 of the takeaway investigation. Working tree clean.

---

## What's tested

- 29 backend pytest tests passing (up from 6 pre-Phase-9): synthetic pose → metrics, audio segmenter precision/recall on synth audio, activity-level Modal lazy-import + LOCAL_DEV routing, takeaway phase regression test against real swing_003 fixture, keypoints endpoint integration.
- TypeScript strict-mode typecheck clean across the entire web app.
- Ruff clean on all new code.
- Vitest spec for SwingPlayer rAF lifecycle (mount → draws on frame → unmount cancels rAF → no leaked timers). 1 spec, 1 passing.
- npm install reports 2 moderate audit findings; not auto-fixed because `--force` could cascade breaking changes. To review deliberately when convenient.

---

## What's broken / honest gaps

- **No production users.** Single-developer use. V1 is portfolio + personal tool, not shipping to customers.
- **No phone-captured swing yet.** PWA, tunnel, and home-screen install all validated end-to-end on iPhone, but the "go to range and capture" milestone is blocked on user recovery from injury. External-upload path is the substitute and works identically downstream of capture.
- **Phase-anchor semantics: tempo + head-excursion metrics use `address`, not `takeaway`.** Audio segmenter's -5 s pre-pad means `address_frame` lands in the pre-shot routine on real clips. swing_003 reports `tempoRatioBackswingDownswing = 13.94` and head sway/lift in the 300+ mm range. Detectors are fine; the metrics' phase-anchors aren't. Takeaway is currently display-only — no metric reads it. Revisit before V1.5. (See Phase 9.)
- **Top detection is position-only (no velocity gate).** `argmax(lead_wrist_y)` catches the y-peak but not the biomechanical apex; on swing_003 the picked frame has wrist speed 1.63 m/s. Likely under-measures shoulder/hip turn-at-top. (See Phase 9.)
- **`finish` frequently hits the `impact + 1 s` fallback cap.** Natural condition (wrist above shoulder + 100 ms quiet) never fires post-impact on swing_003. Cap keeps the math finite but "finish" is fabricated on this clip. May be a recurring pattern on real driver clips. (See Phase 9.)
- **Audio segmenter has a 4/5 false-positive rate on the diagnostic clip.** Motion gate currently masks the symptom (rejected swings get no metrics computed), so end-user impact is zero in V1. But the segmenter ideally wouldn't surface them. Documented as V1.5.
- **Inline-vs-offload threshold for keypoints in Mongo is unresolved.** Current code only writes `storageRef` to S3. Means the overlay shows a "deferred to V1.5" badge for every real swing today. Now resolvable since real swing payload sizes are measurable.
- **Embeddings are not computed.** Schema has `embedding: list[float]`, `/similar` endpoint exists, vector index docs exist. Code doesn't populate the field. V1.5.
- **Dedup-shadowing bug in audio segmenter** documented as TODO. Will reveal itself the first time a real range session has impacts < 3 seconds apart. Diagnostic clip with 5 impacts at ~2s spacing is now real test data for it.
- **MediaPipe legacy Solutions API** is on borrowed time. Migration to `PoseLandmarker` Tasks API documented as TODO before V1.5.
- **Web has minimal unit tests.** SwingPlayer rAF lifecycle covered. Most other components only typecheck-clean.
- **Modal not deployed.** Function written and importable, just not configured with credentials. LOCAL_DEV=true is the V1 mode.
- **Vercel not deployed.** Web app builds and typechecks, hasn't been pushed to a hosted environment.
- **iOS native app parked.** ~1000 LOC of working SwiftUI in `ios/`, but unused. V2 work.

---

## What's deliberately deferred

- VLM coach (Tier 3 — Claude/GPT-4o called on 4–6 keyframes + metrics for structured swing feedback). V1.5.
- Vector embedding computation + similarity search. V1.5.
- HaMeR 3D hand pose. V1.5.
- Audio segmenter false-positive reduction (e.g., requiring concurrent motion or stricter spectral profile). V1.5.
- Two-camera synchronized triangulation. V2.
- On-device rolling buffer + auto impact detection. V2.
- Native SwiftUI app with ARKit body tracking. V2.
- Club tracking (would close the wrist-vs-clubhead gap on takeaway detection). V2.
- Fine-tuned swing-fault classifier on labeled data. V3.
- Real-time on-device pose preview. V3.
- Multi-user / coach-share mode. V3.

---

## Mecka talking points this generates

- "Built a Temporal-orchestrated egocentric pipeline mirroring your architecture, with per-session parent workflow fanning out to per-swing child workflows for independent failure domains."
- "Audio-anchored phase detection — pose alone is unreliable at impact due to motion blur and self-occlusion. Acoustic transient at 2.5–6kHz gives sub-millisecond impact timestamps."
- "PWA capture surface in V1 (1080p/60fps, $0/yr) with SwiftUI scaffold ready for V2 — explicit scope decision based on what 4K and ARKit actually buy you for body pose."
- "Pose at the speed-of-development tradeoff: BlazePose-Full V1 for fast iteration, HaMeR for V1.5 (matches your hand-pose stack), 4D-Humans considered for V2 if monocular 3D wobble shows up in real data."
- "MongoDB with native `$vectorSearch` for closest-pro-match similarity rather than 'diff vs Rory' theater."
- "Cost-conscious by design: $0/mo V1 with LOCAL_DEV flag routing pose to a local GPU; Modal flips on for V1.5 with target ~$0.10/swing."
- "Identified and fixed the first real-data bug (takeaway firing on pre-shot waggle bursts) using systematic-debugging discipline — diagnostic-first, hypothesis verification with empirical parameter sweep, smallest viable fix. The sweep caught a spec error (initial 50ms persistence wasn't enough; needed 200ms) before code shipped, demonstrating the discipline of letting data correct theory."
- "Structured Claude + Claude Code hybrid workflow — checkpointed commits, explicit working agreements, plugins (Context7, Superpowers, Code Review) for spec-driven execution."

---

## Honest self-assessment for resume generation

### Strongest narrative for Mecka AI specifically

1. **Built an egocentric data infrastructure pipeline that mirrors theirs.** Same architecture, different domain. Audio-anchored phase detection is the engineering insight worth leading with.
2. **First real swing processed end-to-end.** No longer a code-complete blueprint — a working pipeline with at least one real swing through it and one real-data bug found and fixed.
3. **PWA-vs-native scope decision documented and defended.** Demonstrates engineering judgment over default "build it native" answer.
4. **Explicit cost-conscious design.** $0/mo V1 with `LOCAL_DEV` flag, ~$0.10/swing target for V1.5.
5. **Disciplined real-data debugging.** Diagnose → propose → empirically verify → implement, with explicit stop conditions between phases. The takeaway bug investigation is a clean example.
6. **Hybrid AI workflow with checkpointed commits.** Hireable signal at any AI infra company.

### Weakest spots / honest caveats

- **Not deployed at scale.** V1 runs locally with real cloud backends and processes real swings, but no production users, no Modal, no Vercel.
- **Only one real swing has been processed end-to-end.** First validation milestone hit; corpus is n=1 until phone capture or more external clips happen.
- **No production users.** Personal project, portfolio + personal-use tool.
- **Native iOS scaffold isn't being used.** Recruiters will ask about Swift — honest answer is "scaffold ready for V2, deferred for cost reasons in V1."
- **No fine-tuning / model training has been done.** All inference uses pretrained models. Hugging Face fine-tuning rep is V2 scope.
- **No production monitoring / observability.** No Sentry, no Datadog, no logs aggregation. Local pytest + ruff is the entire test loop.
- **Solo development.** No team coordination, on-call rotation, or production incident management.

### Resume-ready phrasings (verified accurate as of May 8, 2026)

> "Built an egocentric capture-to-analysis pipeline modeled on data infrastructure used in physical-AI labs. PWA capture (Next.js, MediaRecorder, 1080p/60fps) on iPhone with SwiftUI scaffold ready for V2 native; AWS S3 for storage; Temporal orchestrates per-session jobs with idempotent activities and per-swing child workflows that retry independently; MediaPipe BlazePose pose inference (Modal-ready, runs locally in V1); MongoDB Atlas with native `$vectorSearch` index for swing-similarity queries (embeddings V1.5); Next.js dashboard with skeleton overlay, phase scrubbing, and side-by-side comparison. V1 deployed locally against real cloud backends; first real swing processed end-to-end via direct-upload path. Wrote an audio-anchored impact detector (2.5–6 kHz transient) to auto-segment swings without manual tagging — solving the problem that pose alone is unreliable at impact due to motion blur and self-occlusion. Deterministic Tier 1 metrics (tempo ratio, X-factor, spine tilt, head excursion) backed by a synthetic pose pytest suite plus a regression test against real swing data. Identified and fixed the first real-data bug (takeaway phase detector firing on pre-shot waggle bursts) using a diagnose → propose → empirically-verify → implement workflow; an empirical parameter sweep caught a spec error before code shipped. Vector similarity search planned for V1.5."
