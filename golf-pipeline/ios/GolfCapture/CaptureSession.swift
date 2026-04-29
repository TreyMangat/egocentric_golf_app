import AVFoundation
import Combine
import SwiftUI
import UIKit

/// AVFoundation pipeline.
///
/// Configures the back camera at 4K @ 60fps with audio, writes to an
/// `.mov` file in the app's temp directory, and exposes elapsed time +
/// current state as published properties for the UI.
@MainActor
final class CaptureSession: NSObject, ObservableObject {
    enum State: Equatable {
        case idle
        case configuring
        case ready
        case recording(startedAt: Date)
        case finishing
        case error(String)
    }

    // MARK: published

    @Published var state: State = .idle
    @Published var elapsedMs: Int = 0
    @Published var currentClub: Club = .sevenI
    @Published var currentView: View = .dtl
    @Published var tagEvents: [TagEvent] = []
    @Published var sessionId: String = ""

    // MARK: AV plumbing

    let session = AVCaptureSession()
    private let movieOutput = AVCaptureMovieFileOutput()
    private var videoDevice: AVCaptureDevice?
    private var currentFileURL: URL?
    private var elapsedTimer: Timer?

    // MARK: setup

    override init() {
        super.init()
    }

    func configureIfNeeded() async {
        guard state == .idle else { return }
        state = .configuring

        await requestPermissions()

        session.beginConfiguration()
        session.sessionPreset = .hd4K3840x2160

        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            state = .error("No back camera")
            session.commitConfiguration()
            return
        }
        videoDevice = camera

        do {
            // Pick a 4K @ 60fps format if available, else fall back.
            try camera.lockForConfiguration()
            if let format = camera.formats.first(where: { fmt in
                let dims = CMVideoFormatDescriptionGetDimensions(fmt.formatDescription)
                let supports60 = fmt.videoSupportedFrameRateRanges.contains(where: { $0.maxFrameRate >= 60 })
                return dims.width == 3840 && dims.height == 2160 && supports60
            }) {
                camera.activeFormat = format
                camera.activeVideoMinFrameDuration = CMTimeMake(value: 1, timescale: 60)
                camera.activeVideoMaxFrameDuration = CMTimeMake(value: 1, timescale: 60)
            }
            camera.unlockForConfiguration()

            let videoInput = try AVCaptureDeviceInput(device: camera)
            if session.canAddInput(videoInput) { session.addInput(videoInput) }

            if let mic = AVCaptureDevice.default(for: .audio) {
                let audioInput = try AVCaptureDeviceInput(device: mic)
                if session.canAddInput(audioInput) { session.addInput(audioInput) }
            }

            if session.canAddOutput(movieOutput) {
                session.addOutput(movieOutput)
                if let conn = movieOutput.connection(with: .video), conn.isVideoStabilizationSupported {
                    conn.preferredVideoStabilizationMode = .standard
                }
            }
        } catch {
            state = .error("Configuration failed: \(error.localizedDescription)")
            session.commitConfiguration()
            return
        }

        session.commitConfiguration()

        Task.detached { [session] in
            session.startRunning()
        }

        state = .ready
    }

    private func requestPermissions() async {
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .audio)
        }
    }

    // MARK: recording lifecycle

    func startSession() async {
        guard state == .ready else { return }

        let now = Date()
        let formatter = ISO8601DateFormatter()
        sessionId = "session_\(formatter.string(from: now).replacingOccurrences(of: ":", with: "-"))"

        do {
            try await UploadService.shared.startSession(
                StartSessionRequest(
                    userId: Config.userId,
                    sessionId: sessionId,
                    startedAt: now,
                    location: nil,
                    notes: nil
                )
            )
        } catch {
            state = .error("Backend unreachable: \(error.localizedDescription)")
            return
        }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(sessionId).mov")
        try? FileManager.default.removeItem(at: url)
        currentFileURL = url

        movieOutput.startRecording(to: url, recordingDelegate: self)
        state = .recording(startedAt: now)
        startElapsedTimer(from: now)
    }

    func endSession() {
        guard case .recording = state else { return }
        state = .finishing
        movieOutput.stopRecording()
        stopElapsedTimer()
    }

    private func startElapsedTimer(from start: Date) {
        elapsedTimer?.invalidate()
        elapsedTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.elapsedMs = Int(Date().timeIntervalSince(start) * 1000)
            }
        }
    }

    private func stopElapsedTimer() {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
    }

    // MARK: tagging

    /// Emit a tag at the current elapsed time. Use this when:
    /// - the user changes club (between swings)
    /// - the user taps an outcome / shape after a swing
    func tag(
        club: Club? = nil,
        view: View? = nil,
        outcome: Outcome? = nil,
        shape: Shape? = nil
    ) {
        let event = TagEvent(
            tMs: elapsedMs,
            club: club ?? currentClub,
            view: view ?? currentView,
            outcome: outcome,
            shape: shape
        )
        tagEvents.append(event)
        if let club { currentClub = club }
        if let view { currentView = view }
    }

    // MARK: upload + finalize

    private func uploadAndFinalize(localURL: URL) async {
        do {
            // 1. Get presigned URL
            let presign = try await UploadService.shared.presign(
                PresignRequest(
                    userId: Config.userId,
                    sessionId: sessionId,
                    clipId: "session",
                    contentType: "video/quicktime"
                )
            )

            // 2. PUT the .mov to S3
            try await UploadService.shared.upload(localFile: localURL, to: presign.uploadUrl)

            // 3. Tell backend to start the Temporal workflow
            let model = UIDevice.current.model
            let metadata = CaptureMetadata(
                location: nil,
                phoneModel: model,
                fps: Int(Config.preferredFPS),
                width: Config.preferredResolution.width,
                height: Config.preferredResolution.height,
                leadSide: Config.leadSide.rawValue,
                tagEvents: tagEvents.map { $0.dto }
            )
            try await UploadService.shared.finalize(
                sessionId: sessionId,
                request: FinalizeRequest(userId: Config.userId, captureMetadata: metadata)
            )

            await MainActor.run {
                self.state = .ready
                self.tagEvents.removeAll()
                self.elapsedMs = 0
            }
            try? FileManager.default.removeItem(at: localURL)
        } catch {
            await MainActor.run { self.state = .error("Upload failed: \(error.localizedDescription)") }
        }
    }
}

// MARK: - File output delegate

extension CaptureSession: AVCaptureFileOutputRecordingDelegate {
    nonisolated func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        if let error {
            Task { @MainActor in self.state = .error(error.localizedDescription) }
            return
        }
        Task { await self.uploadAndFinalize(localURL: outputFileURL) }
    }
}
