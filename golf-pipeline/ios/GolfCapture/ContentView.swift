import AVFoundation
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var capture: CaptureSession

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            CameraPreview(session: capture.session)
                .ignoresSafeArea()

            VStack {
                topBar
                Spacer()
                bottomControls
            }
            .padding()
        }
        .task { await capture.configureIfNeeded() }
    }

    // MARK: - top bar

    private var topBar: some View {
        HStack {
            statePill
            Spacer()
            HStack(spacing: 6) {
                Text("•")
                    .foregroundColor(isRecording ? Color(hex: 0xff6b6b) : .white.opacity(0.3))
                Text(formatElapsed(capture.elapsedMs))
                    .font(.system(.callout, design: .monospaced).weight(.bold))
                    .foregroundColor(.white)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 4).fill(Color.black.opacity(0.6))
                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(.white.opacity(0.08)))
            )
        }
    }

    private var statePill: some View {
        let (label, color): (String, Color) = {
            switch capture.state {
            case .idle, .configuring: return ("BOOT", .white.opacity(0.4))
            case .ready: return ("READY", Color(hex: 0x9ce28a))
            case .recording: return ("REC", Color(hex: 0xff6b6b))
            case .finishing: return ("UPLOADING", Color(hex: 0xf3c969))
            case .error: return ("ERROR", Color(hex: 0xff6b6b))
            }
        }()
        return Text(label)
            .font(.system(.caption2, design: .monospaced).weight(.bold))
            .tracking(2)
            .foregroundColor(.black)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: 3).fill(color))
    }

    // MARK: - bottom

    private var bottomControls: some View {
        VStack(spacing: 14) {
            if case .error(let msg) = capture.state {
                Text(msg)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(Color(hex: 0xff6b6b))
                    .padding(8)
                    .background(Color.black.opacity(0.6))
                    .cornerRadius(4)
            }

            if isRecording {
                TagPanel()
            }

            HStack(spacing: 12) {
                viewToggle
                ClubPicker(selected: $capture.currentClub)
                    .onChange(of: capture.currentClub) { _, newValue in
                        if isRecording { capture.tag(club: newValue) }
                    }
                Spacer(minLength: 0)
                primaryButton
            }
        }
    }

    private var viewToggle: some View {
        Button {
            capture.currentView = (capture.currentView == .dtl) ? .fo : .dtl
            if isRecording { capture.tag(view: capture.currentView) }
        } label: {
            Text(capture.currentView.rawValue)
                .font(.system(.caption, design: .monospaced).weight(.bold))
                .foregroundColor(.white)
                .frame(width: 56, height: 32)
                .background(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(Color.white.opacity(0.3), lineWidth: 1)
                )
        }
    }

    private var primaryButton: some View {
        Button {
            Task {
                if isRecording { capture.endSession() }
                else { await capture.startSession() }
            }
        } label: {
            Text(isRecording ? "End" : "Start")
                .font(.system(.callout, design: .monospaced).weight(.bold))
                .foregroundColor(.black)
                .frame(width: 100, height: 40)
                .background(
                    RoundedRectangle(cornerRadius: 4)
                        .fill(isRecording ? Color(hex: 0xff6b6b) : Color(hex: 0xd4ff5a))
                )
        }
        .disabled(capture.state == .configuring || capture.state == .finishing)
    }

    // MARK: - helpers

    private var isRecording: Bool {
        if case .recording = capture.state { return true }
        return false
    }

    private func formatElapsed(_ ms: Int) -> String {
        let totalSec = ms / 1000
        return String(format: "%02d:%02d", totalSec / 60, totalSec % 60)
    }
}

// MARK: - camera preview

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let v = PreviewView()
        v.videoPreviewLayer.session = session
        v.videoPreviewLayer.videoGravity = .resizeAspectFill
        return v
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}
}

final class PreviewView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var videoPreviewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
}
