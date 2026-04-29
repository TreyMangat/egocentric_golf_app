# GolfCapture (iOS)

SwiftUI capture app for the golf-pipeline. Records 4K @ 60fps with audio at the driving range, lets you tap a club / view / outcome per swing, and uploads to S3 via a presigned URL.

## Setup

1. Open Xcode 15+, create a new SwiftUI App project named `GolfCapture` and replace the generated files with the ones in this directory.
2. Add to `Info.plist`:
   - `NSCameraUsageDescription` — "Record your golf swing"
   - `NSMicrophoneUsageDescription` — "Detect ball impact for swing segmentation"
3. Set the API base URL in `Config.swift`.
4. Sign with your Apple Developer team and run on a physical device (camera/mic require hardware).

## V1 capture flow

1. Tap **Start Session** — backend creates session, returns sessionId.
2. Recording begins immediately at 4K 60fps with audio.
3. Tap the club button to switch clubs (defaults to last selected).
4. Tap **Tag last swing** anytime — picks good/ok/bad and shape with timestamp; backend associates with the closest detected impact.
5. Tap **End Session** — file is sealed, presigned URL fetched, full session video uploads.
6. Backend Temporal workflow (`ProcessSession`) picks up from there.

## V2 (deferred)

- On-device rolling buffer (last 30s)
- `SoundAnalysis` framework or DSP-thresholded impact detection
- Per-swing upload (only the -5s/+2s windows around each impact)
- Practice-swing detection (motion peak with no audio impact)

## File layout

```
GolfCapture/
├── GolfCaptureApp.swift       # App entry
├── Config.swift               # API base URL, user id
├── ContentView.swift          # Main screen with camera + controls
├── CaptureSession.swift       # AVFoundation pipeline wrapper
├── ClubPicker.swift           # Sliding club selector
├── TagPanel.swift             # Outcome / shape tagging UI
├── UploadService.swift        # Presign + S3 upload
└── Models.swift               # Mirrors backend schemas
```
