# AGENTS.md

## Working agreements

- One PR at a time. Always include tests.
- Checkpointed commits with **pause-and-report after each commit** — that means *after*, not before. Default outside the stop conditions below is **execute → commit → report**, not pause for confirmation.
- Keep CI green.
- Surface architectural decisions. Don't silently pivot.

**Stop conditions** (surface before continuing):

- Drift between spec and repo state.
- Architectural / schema / design decisions not covered by the spec.
- Failing tests or typecheck.
- Work outside the stated scope.

When a plan is generated from a state document or narrative spec, **verify it against `git log --oneline` and the actual file tree before executing**. State docs go stale faster than narrative ones; if the doc and the repo disagree, stop and surface — don't barrel through a plan whose premise is wrong.

## Repo landmines

- **Nested layout.** The project lives in `golf-pipeline/{backend,web,ios,docs}` *inside* the git root. Most paths in commits look like `golf-pipeline/web/...`. Don't assume flat.
- **Python package uses `src/` layout:** `backend/src/golf_pipeline/...`. The dist name is `golf-pipeline`, the import name is `golf_pipeline`.
- **`mediapipe==0.10.14` pin** in `backend/pyproject.toml`. Newer versions removed the `mediapipe.solutions` API the pose code depends on. Don't bump without porting to `mediapipe.tasks.python.vision.PoseLandmarker`.
- **Dual-array keypoints invariant** in pose `.npz` files: `keypoints_world` is `(frames, 33, 4)` (x, y, z, visibility), `keypoints_image` is `(frames, 33, 3)` (u, v, visibility). Metric formulas pick one or the other deliberately; don't conflate them. Activities reach for `keypoints_world` for 3-D metrics, `keypoints_image` for the SVG overlay.
- **`LOCAL_DEV=true`** in `backend/.env` routes pose inference to local CPU/GPU instead of Modal. Smoke tests and dev runs depend on this; production sets it `false`.
- **Temporal `Client.connect` requires `data_converter=pydantic_data_converter`** (`from temporalio.contrib.pydantic import pydantic_data_converter`). Without it, Pydantic v2 models silently fail to serialize across the workflow boundary. Both `api/server.py` and `temporal/worker.py` set it; any new client must too.
- **Temporal activities are passed by reference**, not as strings, so the data converter can deserialize their argument types. Worker registration must use the same activity object the workflow imports.
- **`NaNSafeJSONResponse` is the FastAPI default** (`api/server.py:57`, wired at `default_response_class=NaNSafeJSONResponse`). It sanitizes `NaN` / `Inf` to `null`. Raw `JSONResponse` will crash on incomplete metrics.
- **`parse_s3_uri` lives in `backend/src/golf_pipeline/storage/s3.py`.** Don't redefine it — multiple call sites already import it.
- **S3 client must use a regional endpoint:** `endpoint_url=f"https://s3.{region}.amazonaws.com"`. Without it, presigned URLs hit the global endpoint and get 307-redirected, which breaks `fetch` in the browser. Both `storage/s3.py` and `modal_pose/inference.py` already do this — match the pattern.
- **ffmpeg keyframe alignment uses `-g 1`, not `-g 30`.** The audio segmenter cuts on impact transients, not GOP boundaries; `-g 30` produces P-frame-only segments that won't decode standalone.

## Test / lint commands

- **Backend tests:** `cd golf-pipeline/backend && pytest`. Synthetic data only — no cloud connectivity required.
- **Backend lint:** `cd golf-pipeline/backend && ruff check`.
- **Web typecheck:** `cd golf-pipeline/web && npm run typecheck` (= `tsc --noEmit`).

## Source-of-truth pointers

- **`golf-pipeline/PROJECT_SPEC.md`** — architecture, data model, phasing, intent. Read first.
- **`golf-pipeline/docs/capture-surface.md`** — why PWA over native iOS for V1.
- **`golf-pipeline/docs/atlas-vector-index.md`** — V1.5 vector-search work.
- **`golf-pipeline/docs/first-run.md`** — clone-to-running setup.

State of what's shipped lives in `git log --oneline`, not in narrative docs. Narrative docs cover intent and decisions; the log covers state. When they disagree, trust the log.
