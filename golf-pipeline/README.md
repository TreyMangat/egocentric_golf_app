# golf-pipeline

Personal egocentric golf swing capture and analysis pipeline. Mirrors Mecka AI's data infrastructure stack applied to a personal use case.

> **Read first:** [`PROJECT_SPEC.md`](./PROJECT_SPEC.md) — full architecture, schemas, decisions, and phasing.

## Repo layout

```
golf-pipeline/
├── PROJECT_SPEC.md         # ← architecture & decisions (the brain of the project)
├── README.md               # this file
├── backend/                # Python — Temporal workers, Modal pose, FastAPI, Mongo
├── web/                    # Next.js dashboard
├── ios/                    # SwiftUI capture app
└── docs/                   # benchmark writeups, design notes
```

## Stack

| Layer | Choice |
|---|---|
| Capture | iOS (SwiftUI + AVFoundation, optional ARKit) |
| Object storage | AWS S3 |
| Orchestration | Temporal |
| GPU inference | Modal (Runpod as alternative for benchmark) |
| Pose model | MediaPipe BlazePose (V1), HaMeR (V1.5) |
| Database | MongoDB Atlas (free tier) with vector search |
| API | FastAPI |
| Frontend | Next.js 15 + TypeScript + Tailwind |

## Quickstart (backend)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in credentials

# In separate terminals:
temporal server start-dev                                # local Temporal
python -m golf_pipeline.temporal.worker                  # Temporal worker
modal serve src/golf_pipeline/modal_pose/inference.py    # Modal (dev mode)
uvicorn golf_pipeline.api.server:app --reload --port 8000
```

Run end-to-end on a sample video:
```bash
python scripts/run_local.py --video sample.mov --club 7i --view DTL
```

## Quickstart (web)

```bash
cd web
npm install
npm run dev    # http://localhost:3000
```

## Quickstart (iOS capture)

**V1 path: PWA (no $99 dev fee).** Deploy the Next.js app to Vercel, expose the FastAPI backend over HTTPS (Cloudflare Tunnel or Fly.io), then on iPhone:

1. Open the deployed URL in Safari
2. Share → **Add to Home Screen**
3. Tap the icon → grants camera/mic on first run → records full-screen

The capture UI lives at `/capture`. See [`docs/capture-surface.md`](./docs/capture-surface.md) for the full PWA-vs-native decision and tradeoff table.

**V2 path: SwiftUI native.** Scaffold lives in `ios/GolfCapture/`. Open in Xcode 15+, set the API base in `Config.swift`, sign with your team. See `ios/README.md`.

## Required accounts / credentials

- AWS (S3 bucket + IAM user with put/get on the bucket prefix)
- MongoDB Atlas (free cluster, get connection string)
- Modal (`modal token new`)
- Temporal Cloud or local dev server
- Apple Developer (for iOS device install only)

## Status

V1 scaffold. See `PROJECT_SPEC.md` § Phasing for what ships when.
