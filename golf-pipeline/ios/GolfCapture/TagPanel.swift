import SwiftUI

/// One-tap tagging UI for the most recent swing.
///
/// Backend associates each tag with the closest detected impact, so the user
/// can tap a few seconds after the swing without precise timing.
struct TagPanel: View {
    @EnvironmentObject var capture: CaptureSession
    @State private var pendingOutcome: Outcome?
    @State private var pendingShape: Shape?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tag last swing")
                .font(.system(.caption2, design: .monospaced).weight(.bold))
                .foregroundColor(.white.opacity(0.5))
                .tracking(2)

            HStack(spacing: 6) {
                ForEach(Outcome.allCases) { o in
                    pill(label: o.rawValue.uppercased(),
                         active: pendingOutcome == o,
                         tint: tint(for: o)) {
                        pendingOutcome = (pendingOutcome == o) ? nil : o
                    }
                }
            }

            HStack(spacing: 6) {
                ForEach(Shape.allCases) { s in
                    pill(label: s.rawValue,
                         active: pendingShape == s,
                         tint: .white) {
                        pendingShape = (pendingShape == s) ? nil : s
                    }
                }
            }

            HStack {
                Button {
                    capture.tag(outcome: pendingOutcome, shape: pendingShape)
                    pendingOutcome = nil
                    pendingShape = nil
                } label: {
                    Text("Save tag")
                        .font(.system(.caption, design: .monospaced).weight(.bold))
                        .foregroundColor(.black)
                        .frame(maxWidth: .infinity, minHeight: 36)
                        .background(
                            RoundedRectangle(cornerRadius: 4)
                                .fill(canSave ? Color(hex: 0xd4ff5a) : Color.white.opacity(0.15))
                        )
                }
                .disabled(!canSave)

                Text("\(capture.tagEvents.count) tag\(capture.tagEvents.count == 1 ? "" : "s")")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundColor(.white.opacity(0.4))
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.black.opacity(0.6))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.white.opacity(0.08), lineWidth: 1)
                )
        )
    }

    private var canSave: Bool { pendingOutcome != nil || pendingShape != nil }

    private func tint(for outcome: Outcome) -> Color {
        switch outcome {
        case .good: return Color(hex: 0x9ce28a)
        case .ok: return Color(hex: 0xf3c969)
        case .bad: return Color(hex: 0xff6b6b)
        }
    }

    @ViewBuilder
    private func pill(label: String, active: Bool, tint: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(.caption2, design: .monospaced).weight(.bold))
                .foregroundColor(active ? .black : .white)
                .padding(.horizontal, 8)
                .frame(minWidth: 36, minHeight: 28)
                .background(
                    RoundedRectangle(cornerRadius: 3)
                        .fill(active ? tint : Color.white.opacity(0.08))
                )
        }
    }
}
