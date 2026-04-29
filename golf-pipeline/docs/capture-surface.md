# Capture surface — PWA vs native iOS

## Decision (V1)

**Build the capture UI as a PWA inside the existing Next.js app.** Keep the SwiftUI scaffold in `ios/` for V2 evaluation. Single user, single iPhone, $0 dev cost.

## Why PWA wins for V1

| Factor | PWA | Native (free Apple ID + Xcode) | Native ($99 dev) |
|---|---|---|---|
| Cost | $0 | $0 | $99/yr |
| Distribution | URL → "Add to Home Screen" | Xcode → physical device, re-sign every 7 days | App Store or TestFlight |
| Camera + audio | ✅ via `getUserMedia` | ✅ AVFoundation | ✅ AVFoundation |
| Recording | ✅ `MediaRecorder` (MP4/H.264 since iOS 14.3) | ✅ | ✅ |
| Resolution | 1080p @ 60fps reliable; 4K hit-and-miss | 4K @ 60fps native | 4K @ 60fps native |
| Iteration | Push to Vercel, refresh on phone | Xcode rebuild + re-install | Xcode rebuild + re-install |
| ARKit body tracking | ❌ | ✅ | ✅ |
| Background recording | ❌ | ❌ (foregrounded only) | ❌ (foregrounded only) |
| 7-day expiry | n/a | ✅ (annoying) | n/a |
| Multi-user | URL share | UDID-locked | TestFlight or App Store |

## What 1080p @ 60fps means for the metrics

The Tier 1 metrics defined in `PROJECT_SPEC.md` are robust at 1080p. What actually matters for golf-swing pose work isn't pixel count — it's framerate. The downswing is ~250–320 ms; at 30 fps that's 7–10 frames, at 60 fps that's 15–20. Higher framerate = more accurate impact phase, more accurate club-proxy wrist trajectory. A pose model gains very little from 4K vs 1080p on a body-sized subject filling most of the frame; it gains a lot from 60 fps over 30 fps.

When we want true 240 fps slow-mo at impact (V2), that's a native-only feature on iOS. But by the time that matters, the PWA will have generated enough data and demos to justify the $99 if we want it.

## What we lose vs the SwiftUI version

1. **No ARKit body tracking** — would have been a useful ground-truth signal to compare BlazePose against. Defer to V2.
2. **No phone IMU access** — interesting only if we ever want phone-on-club-shaft or in-pocket capture. Not V1 anyway.
3. **No 4K** — see above.
4. **Permission re-prompts on route change** — mitigated by keeping the capture page single-route and preserving the `MediaStream` for the page lifetime.
5. **Tab switching kills the recording** — solved by Wake Lock API for screen-on, and by user discipline (don't switch apps mid-session).

## What stays identical between PWA and native

The whole backend pipeline. Same presigned-URL upload, same `IngestRequest`, same Temporal workflow, same audio segmentation, same metrics, same dashboard. The capture surface is interchangeable — we could in principle ship both clients pointing at the same backend without any pipeline changes.

## How to install the PWA on iPhone

1. Deploy the Next.js app over HTTPS (Vercel is one click — `vercel deploy`).
2. Make sure the FastAPI backend is reachable over HTTPS too. Either:
   - Cloudflare Tunnel pointing at your laptop (`cloudflared tunnel --url http://localhost:8000`), or
   - Deploy the backend to Fly.io / Railway / Render.
3. Set `NEXT_PUBLIC_API_BASE` to the backend HTTPS URL when building the web app.
4. On iPhone, open the deployed URL in Safari → Share → **Add to Home Screen**.
5. Tap the home-screen icon → opens full-screen with no Safari chrome → camera prompt → record.

HTTPS is non-negotiable. iOS Safari will refuse to grant camera/mic access on plain HTTP outside `localhost`.

## When to revisit the native path

Triggers for going native (in priority order):

1. **You want ARKit body tracking** as a ground-truth comparison vs the BlazePose output. This is a legitimate Mecka-relevant differentiator and a real ML eval rep.
2. **You want true slow-mo (240 fps)** at impact for sharper phase detection.
3. **You want to share with a friend or coach** who doesn't want to install via URL.
4. **You're submitting to App Store** as a portfolio artifact — only worth the $99 if you'll actually ship it publicly.
5. **You want phone-on-club-shaft IMU experiments.**

When any of those land, the SwiftUI scaffold in `ios/` is the starting point. The data model and backend don't change.

## Files

- `web/app/capture/page.tsx` — main capture UI
- `web/lib/capture.ts` — `getUserMedia`, `MediaRecorder`, wake lock, format detection
- `web/components/capture/{ClubPicker,TagPanel}.tsx` — controls
- `web/public/manifest.json` — PWA manifest
- `web/public/sw.js` — minimal service worker (install eligibility only)
- `web/components/PwaInit.tsx` — service-worker registration on first load
- `ios/` — SwiftUI scaffold, kept for V2 evaluation
